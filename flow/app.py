"""Menu bar app: hold the hotkey, speak, release; the text is pasted at the cursor.

Run: uv run python -m flow.app
"""

import atexit
import fcntl
import logging
import queue
import sys
import threading
import time

import Quartz
import rumps
from AppKit import NSBundle, NSWorkspace
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from PyObjCTools import AppHelper

from . import audio, config, inject, login
from .cleanup import Cleaner
from .learn import Learning, ax
from .learn.ax_watch import Watcher
from .learn.fix_window import FixWindow
from .learn.store import PATH as LEARNING_DB
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
# Key codes of the letters and the modifier names a shortcut in config.toml may use.
_LETTERS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38,
    "k": 40, "n": 45, "m": 46,
}  # fmt: skip
_MODIFIERS = {
    "<ctrl>": Quartz.kCGEventFlagMaskControl,
    "<alt>": Quartz.kCGEventFlagMaskAlternate,
    "<cmd>": Quartz.kCGEventFlagMaskCommand,
    "<shift>": Quartz.kCGEventFlagMaskShift,
}


def wispr_running() -> bool:
    apps = NSWorkspace.sharedWorkspace().runningApplications()
    return any((a.localizedName() or "") == "Wispr Flow" for a in apps)


def front_app() -> str:
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return (app.localizedName() or "") if app else ""


def parse_shortcut(text: str) -> tuple[int, int] | None:
    """Turns "<ctrl>+<alt>+f" into (key code, modifier flags). None when it cannot be read."""
    code, flags = None, 0
    for part in text.lower().replace(" ", "").split("+"):
        if part in _MODIFIERS:
            flags |= _MODIFIERS[part]
        elif part in _LETTERS and code is None:
            code = _LETTERS[part]
        else:
            return None
    return (code, flags) if code is not None and flags else None


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
                LocalWhisper(local["model"], local["languages"], local["unload_after_idle_minutes"])
            )
        else:
            log.info("provider %s is skipped: not set up on this Mac", name)
    return providers


class FlowApp(rumps.App):
    def __init__(self, cfg: dict):
        super().__init__("Flow Backup", title=IDLE, quit_button="Quit")
        self.cfg = cfg
        self.status = rumps.MenuItem("Starting...")
        self.learned = rumps.MenuItem("Learned words")
        self.at_login = rumps.MenuItem("Start at login", callback=self._toggle_login)
        self.at_login.state = login.starts_at_login()
        self.menu = [
            self.status,
            rumps.MenuItem("Fix last dictation...", callback=lambda _: self._open_fix()),
            self.learned,
            self.at_login,
        ]
        self.recorder = audio.Recorder(cfg["audio"]["sample_rate"], cfg["audio"]["max_seconds"])
        self.chain = Chain(build_providers(cfg))
        self.cleaner = Cleaner(cfg, config.key("GEMINI_API_KEY"))
        learning = cfg["learning"]
        self.learning = Learning(learning["enabled"])
        self.fix_window = FixWindow(self._fix_saved)
        self.watcher = Watcher(self._learn) if learning["enabled"] and learning["auto_capture"] else None
        self._fix_key = parse_shortcut(cfg["hotkey"]["fix_last"])
        if self._fix_key is None:
            log.warning("fix_last shortcut %r cannot be read", cfg["hotkey"]["fix_last"])
        self._refresh_learned()
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
            elif self._fix_key and not Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventAutorepeat
            ):
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                held = Quartz.CGEventGetFlags(event) & sum(_MODIFIERS.values())
                if (code, held) == self._fix_key:
                    AppHelper.callAfter(self._open_fix)
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

    def _toggle_login(self, item) -> None:
        login.set_start_at_login(not item.state)
        item.state = login.starts_at_login()
        if item.state:
            # The login copy runs as its own app and needs the permissions granted to it once.
            self.status.title = f"Open {login.NAME} from Applications once and allow access"

    def _refresh_learned(self) -> None:
        """Rebuilds the "Learned words" menu. Main thread only."""
        for title in list(self.learned.keys()):
            del self.learned[title]
        rows = self.learning.store.vocabulary() if self.learning.store else []
        if not rows:
            self.learned.add(rumps.MenuItem("Nothing learned yet"))
        for row in rows:
            heard, intended, enabled = row["heard"], row["intended"], bool(row["enabled"])
            # An ordinary word is used only after the same correction is seen twice.
            waiting = "" if row["count"] >= 2 else "  (seen once)"
            entry = rumps.MenuItem(f"{heard} → {intended}{waiting}")
            entry.state = enabled
            use = rumps.MenuItem(
                "Use this word",
                callback=lambda _, h=heard, i=intended, on=enabled: self._edit_learned(h, i, not on),
            )
            use.state = enabled
            entry.add(use)
            entry.add(
                rumps.MenuItem("Delete", callback=lambda _, h=heard, i=intended: self._edit_learned(h, i, None))
            )
            self.learned.add(entry)

    def _edit_learned(self, heard: str, intended: str, enabled: bool | None) -> None:
        """Switches a learned word on or off, or deletes it when enabled is None."""
        if enabled is None:
            self.learning.store.delete_swap(heard, intended)
        else:
            self.learning.store.set_enabled(heard, intended, enabled)
        self._refresh_learned()

    def _open_fix(self) -> None:
        last = self.learning.last()
        if last is None:
            self.status.title = "Nothing to fix yet"
            return
        if self.watcher:
            self.watcher.drop()
        self.fix_window.show(*last)

    def _fix_saved(self, dictation_id: int, before: str, after: str) -> None:
        threading.Thread(target=self._apply_fix, args=(dictation_id, before, after), daemon=True).start()

    def _apply_fix(self, dictation_id: int, before: str, after: str) -> None:
        learned = self._learn(dictation_id, before, after)
        # The field the text was pasted into needs a moment to take the keyboard back.
        time.sleep(0.3)
        try:
            with self._paste_lock:
                field = ax.focused()
                at = field.value.rfind(before) if field else -1
                if at >= 0 and ax.select(field, at, len(before)):
                    inject.paste(after)
                    self._show(IDLE, learned or "Fixed")
                else:
                    # The old text cannot be found from here, so the user pastes over it.
                    inject.copy(after)
                    self._show(IDLE, "Fix copied: select the old text and paste")
        except inject.InjectError as e:
            self._show(WARN, str(e)[:80])

    def _learn(self, dictation_id: int, before: str, after: str) -> str:
        """Saves a correction. Returns a line for the menu naming the words learned, or ''."""
        swaps = self.learning.learn(dictation_id, before, after)
        if not swaps:
            return ""
        AppHelper.callAfter(self._refresh_learned)
        summary = "Learned: " + ", ".join(f"{s.heard} → {s.intended}" for s in swaps)
        self._show(self.title, summary[:80])
        return summary[:80]

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
        # Started at login there is no terminal, so the log file is where a missing permission shows.
        if self._problem:
            log.warning("not ready: %s", self._problem)
        else:
            log.info("ready")
        while True:
            command = self._commands.get()
            try:
                if command == "down":
                    # Record first so no word is lost, then silence the speakers.
                    self.recorder.start()
                    self._show(RECORDING)
                    # Reload the local model while the user is still speaking, if it was unloaded.
                    self._warm_providers()
                    if self.watcher:
                        self.watcher.finish()
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
                app = front_app()
                flac = audio.to_flac(clip, self.recorder.sample_rate)
                terms = self.learning.terms(self.cfg["dictionary"]["terms"])
                raw, provider = self.chain.transcribe(flac, terms)
                heard = time.perf_counter() - start
                # Gemini's smart mode already returns cleaned text, but it can still be in Hindi script.
                smart = provider == "gemini" and self.cfg["stt"]["gemini"]["smart_mode"]
                text = self.learning.apply(raw)
                text, route = self.cleaner.clean(
                    text, already_clean=smart, terms=terms, examples=self.learning.examples(text)
                )
                total = time.perf_counter() - start
                if text:
                    inject.paste(text)
                    dictation_id = self.learning.record(app, raw, text)
                    if self.watcher and dictation_id:
                        self.watcher.watch(dictation_id, text)
            except (SttError, inject.InjectError) as e:
                log.warning("dictation failed: %s", e)
                self._show(WARN, str(e)[:80])
                return
            log.info(
                "%s: %.1f s audio, %d chars, speech %.2f s, cleanup %s %.2f s",
                provider, len(clip) / self.recorder.sample_rate, len(text), heard, route, total - heard,
            )
            self._show(IDLE, f"Last: {provider}, {total:.1f} s")


def only_copy():
    """Holds a lock for as long as the app runs. None when another copy already has it."""
    LEARNING_DB.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LEARNING_DB.parent / "running.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return lock


def main() -> None:
    # Two copies would both answer the key and paste the text twice.
    lock = only_copy()
    if lock is None:
        sys.exit("Flow Backup is already running (look for its icon in the menu bar).")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Menu bar only: no Dock icon.
    NSBundle.mainBundle().infoDictionary()["LSUIElement"] = "1"
    FlowApp(config.load()).run()


if __name__ == "__main__":
    main()
