"""Menu bar app: hold the hotkey, speak, release; the text is pasted at the cursor.

Run: uv run python -m flow.app
"""

import atexit
import logging
import queue
import threading
import time

import Quartz
import rumps
from AppKit import NSBundle, NSWorkspace
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from PyObjCTools import AppHelper

from . import audio, config, inject
from .cleanup import Cleaner
from .stt.base import RateLimited, SttError, Unavailable
from .stt.gemini import GeminiTranscribe
from .stt.local_whisper import LocalWhisper

log = logging.getLogger("flow")

IDLE, RECORDING, BUSY, WARN = "🎙", "🔴", "⏳", "⚠️"

# Modifier keys that can be held to talk: name -> (keycode, flag bit set while it is down).
_KEYS = {
    "cmd_r": (54, 0x10),
    "alt_r": (61, 0x40),
    "ctrl_r": (62, 0x2000),
    "fn": (63, Quartz.kCGEventFlagMaskSecondaryFn),
}
# Clips quieter than this are treated as silence and never sent.
_QUIET_DBFS = -50


def wispr_running() -> bool:
    apps = NSWorkspace.sharedWorkspace().runningApplications()
    return any((a.localizedName() or "") == "Wispr Flow" for a in apps)


class Chain:
    """Tries providers in order and rests one that failed while another can answer."""

    def __init__(self, providers: list):
        self.providers = providers
        self._rest_until: dict[str, float] = {}

    def transcribe(self, flac: bytes, vocabulary: list[str]) -> tuple[str, str]:
        now = time.monotonic()
        ready = [p for p in self.providers if now >= self._rest_until.get(p.name, 0)]
        errors = []
        for provider in ready or self.providers:
            try:
                return provider.transcribe(flac, vocabulary), provider.name
            except RateLimited as e:
                self._rest_until[provider.name] = now + (e.retry_after or 60)
                errors.append(str(e))
            except Unavailable as e:
                self._rest_until[provider.name] = now + 30
                errors.append(str(e))
            log.warning("%s", errors[-1])
        raise Unavailable("; ".join(errors) or "no speech provider is set up")


def build_providers(cfg: dict) -> list:
    providers = []
    for name in cfg["stt"]["order"]:
        if name == "gemini" and config.key("GEMINI_API_KEY"):
            gemini = cfg["stt"]["gemini"]
            providers.append(
                GeminiTranscribe(
                    config.key("GEMINI_API_KEY"),
                    model=gemini["model"],
                    language_codes=gemini["language_codes"],
                    smart_mode=gemini["smart_mode"],
                    timeout=cfg["stt"]["timeout_seconds"],
                )
            )
        elif name == "local":
            local = cfg["stt"]["local"]
            providers.append(
                LocalWhisper(local["model"], local["language"], local["unload_after_idle_minutes"])
            )
        else:
            log.info("provider %s is skipped: not set up on this Mac", name)
    return providers


class FlowApp(rumps.App):
    def __init__(self, cfg: dict):
        super().__init__("Flow Backup", title=IDLE, quit_button="Quit")
        self.cfg = cfg
        self.status = rumps.MenuItem("Starting...")
        self.menu = [self.status]
        self.recorder = audio.Recorder(cfg["audio"]["sample_rate"], cfg["audio"]["max_seconds"])
        self.chain = Chain(build_providers(cfg))
        self.cleaner = Cleaner(cfg, config.key("GEMINI_API_KEY"))
        self._commands: queue.Queue[str] = queue.Queue()
        self._paste_lock = threading.Lock()
        self._held: str | None = None
        self._muted = False
        atexit.register(self._restore_sound)
        self._tap = None
        self._problem = self._check_setup()
        threading.Thread(target=self._worker, daemon=True).start()

    def _check_setup(self) -> str:
        """Asks macOS for the permissions and starts the key listener. Returns a problem or ''."""
        trusted = AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(
            Quartz.kCGEventKeyDown
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._on_event,
            None,
        )
        if self._tap is None:
            return "Allow Input Monitoring, then restart"
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        if not trusted:
            return "Allow Accessibility, then restart"
        if not self.chain.providers:
            return "Add GEMINI_API_KEY to .env, then restart"
        return ""

    def _key_active(self, name: str) -> bool:
        if name == self.cfg["hotkey"]["push_to_talk"]:
            return True
        wispr = self.cfg["wispr"]
        return wispr["takeover"] and name == wispr["wispr_key"] and not wispr_running()

    def _on_event(self, proxy, kind, event, refcon):
        if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        if kind == Quartz.kCGEventKeyDown:
            # Another key while holding means a shortcut such as Cmd+C, not dictation.
            if self._held is not None:
                self._held = None
                self._commands.put("cancel")
            return event
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        for name, (keycode, flag) in _KEYS.items():
            if code != keycode:
                continue
            down = bool(Quartz.CGEventGetFlags(event) & flag)
            if down and self._held is None and self._key_active(name):
                self._held = name
                self._commands.put("down")
            elif not down and self._held == name:
                self._held = None
                self._commands.put("up")
        return event

    def _show(self, title: str, status: str | None = None) -> None:
        def apply():
            self.title = title
            if status is not None:
                self.status.title = status

        AppHelper.callAfter(apply)

    def _warm_providers(self) -> None:
        for provider in self.chain.providers:
            if hasattr(provider, "warm"):
                provider.warm()

    def _restore_sound(self) -> None:
        if self._muted:
            self._muted = False
            audio.unmute_output()

    def _worker(self) -> None:
        try:
            # The first stream start in a process takes seconds; pay that now, not on the first word.
            self.recorder.start()
            self.recorder.stop()
        except Exception as e:
            log.exception("microphone warm-up failed")
            self._problem = self._problem or f"Microphone: {e}"[:80]
        self._warm_providers()
        self._show(WARN if self._problem else IDLE, self._problem or "Ready")
        while True:
            command = self._commands.get()
            try:
                if command == "down":
                    # Record first so no word is lost, then silence the speakers.
                    self.recorder.start()
                    self._show(RECORDING)
                    # Reload the local model while the user is still speaking, if it was unloaded.
                    self._warm_providers()
                    if self.cfg["audio"]["mute_while_recording"]:
                        self._muted = audio.mute_output()
                elif command == "cancel":
                    self.recorder.stop()
                    self._restore_sound()
                    self._show(IDLE)
                elif command == "up":
                    clip = self.recorder.stop()
                    self._restore_sound()
                    length = audio.seconds(clip, self.recorder.sample_rate)
                    if length < self.cfg["audio"]["min_seconds"] or audio.peak_dbfs(clip) < _QUIET_DBFS:
                        self._show(IDLE, "Nothing heard")
                        continue
                    threading.Thread(target=self._dictate, args=(clip,), daemon=True).start()
            except Exception as e:
                log.exception("recording failed")
                self._show(WARN, str(e)[:80])

    def _dictate(self, clip) -> None:
        with self._paste_lock:
            self._show(BUSY)
            start = time.perf_counter()
            try:
                flac = audio.to_flac(clip, self.recorder.sample_rate)
                text, provider = self.chain.transcribe(flac, self.cfg["dictionary"]["terms"])
                heard = time.perf_counter() - start
                route = "smart"
                # Gemini's smart mode already returns cleaned text; everything else is raw.
                if not (provider == "gemini" and self.cfg["stt"]["gemini"]["smart_mode"]):
                    text, route = self.cleaner.clean(text)
                total = time.perf_counter() - start
                if text:
                    inject.paste(text)
            except (SttError, inject.InjectError) as e:
                log.warning("dictation failed: %s", e)
                self._show(WARN, str(e)[:80])
                return
            log.info(
                "%s: %.1f s audio, %d chars, speech %.2f s, cleanup %s %.2f s",
                provider, len(clip) / self.recorder.sample_rate, len(text), heard, route, total - heard,
            )
            self._show(IDLE, f"Last: {provider}, {total:.1f} s")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Menu bar only: no Dock icon.
    NSBundle.mainBundle().infoDictionary()["LSUIElement"] = "1"
    FlowApp(config.load()).run()


if __name__ == "__main__":
    main()
