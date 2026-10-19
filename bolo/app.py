"""Menu bar app: hold the hotkey, speak, release; the text is pasted at the cursor.

Run: uv run python -m bolo.app
"""

import atexit
import fcntl
import logging
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

import objc
import Quartz
import rumps
from AppKit import NSBundle, NSRunningApplication, NSWorkspace
from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from PyObjCTools import AppHelper

from . import audio, config, inject, login, sounds, styles
from .usage import Usage
from .cleanup import Cleaner, prompt_prefix
from .early import EarlyText
from .learn import Learning, ax
from .learn.ax_watch import Watcher
from .learn.fix_window import FixWindow
from .learn.store import PATH as LEARNING_DB
from .learn.voice import Voice
from .overlay import Overlay
from .pending import Pending, Saved
from .settings_window import SettingsWindow
from .stt.base import BadResult, RateLimited, SttError, Unavailable, flac_seconds
from .stt.groq import GroqWhisper
from .stt.local_whisper import LocalWhisper, ensure_model

log = logging.getLogger("bolo")

IDLE, RECORDING, BUSY, WARN = "🎙", "🔴", "⏳", "⚠️"

# Modifier keys that can be held to talk: name -> (keycode, flag bit set while it is down).
_KEYS = {
    "cmd_r": (54, 0x10),
    "alt_r": (61, 0x40),
    "ctrl_r": (62, 0x2000),
    "fn": (63, Quartz.kCGEventFlagMaskSecondaryFn),
}
_ESCAPE = 53
# Said at the start of a dictation, these turn it into a structured prompt.
_PROMPT_PREFIXES = ["as a prompt", "prompt mode"]
# A press shorter than this is a tap; two taps this close together start hands-free recording.
_TAP_SECONDS, _DOUBLE_TAP_SECONDS = 0.3, 0.4
_KEY_LABELS = {"cmd_r": "right ⌘", "alt_r": "right ⌥", "ctrl_r": "right ⌃", "fn": "fn"}
# The general modifier flag each talk key also sets, so it is not mistaken for another modifier.
_KEY_FAMILY = {
    "cmd_r": Quartz.kCGEventFlagMaskCommand,
    "alt_r": Quartz.kCGEventFlagMaskAlternate,
    "ctrl_r": Quartz.kCGEventFlagMaskControl,
    "fn": 0,
}
# Clips quieter than this are treated as silence and never sent.
_QUIET_DBFS = -50
# A played sound reaches the built-in speakers about 80 ms later (measured), and its echo dies
# away soon after. Muting waits for the first; both are cut from the start of the clip.
_SPEAKER_SECONDS, _ECHO_SECONDS = 0.1, 0.06
# No step of a key press takes this long (opening a stuck microphone gives up after 8 s).
_STUCK_SECONDS = 15
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


def _sees_window_titles() -> bool:
    """macOS hides other apps' window titles without Screen Recording. The preflight call can
    answer no for an unsigned app that has it, so this checks what the app can actually see."""
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    me = NSRunningApplication.currentApplication().processIdentifier()
    # The Dock's and the menu bar's window titles are visible to every app.
    return any(
        w.get("kCGWindowName")
        for w in windows or []
        if w.get("kCGWindowOwnerPID") != me and w.get("kCGWindowOwnerName") not in ("Dock", "Window Server")
    )


def permissions() -> dict[str, bool]:
    """Which macOS privacy permissions this app has. Asks for none of them."""
    NSBundle.bundleWithPath_("/System/Library/Frameworks/AVFoundation.framework").load()
    microphone = objc.lookUpClass("AVCaptureDevice").authorizationStatusForMediaType_("soun") == 3
    # Only an app with Full Disk Access can open the privacy database.
    try:
        open(Path.home() / "Library/Application Support/com.apple.TCC/TCC.db", "rb").close()
        full_disk = True
    except OSError:
        full_disk = False
    return {
        "Accessibility": bool(AXIsProcessTrusted()),
        "Input Monitoring": bool(Quartz.CGPreflightListenEventAccess()),
        "Microphone": microphone,
        "Screen Recording": bool(Quartz.CGPreflightScreenCaptureAccess()) or _sees_window_titles(),
        "Full Disk Access": full_disk,
    }


def open_up_front_app(learning: bool) -> None:
    """Lets the learning read the text field in the app the user is dictating into, and a browser
    show the address of its page, which picks the writing style."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app and (learning or (app.bundleIdentifier() or "") in styles.BROWSERS):
        ax.open_up(app.processIdentifier(), app.bundleIdentifier() or "")


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
    """Tries providers in order and rests one that failed while another can answer. A provider
    with its own reachability check (Groq) is skipped at once while it is known to be down."""

    def __init__(self, providers: list):
        self.providers = providers
        self._rest_until: dict[str, float] = {}

    def _ready(self) -> list:
        now = time.monotonic()
        return [
            p for p in self.providers if now >= self._rest_until.get(p.name, 0) and getattr(p, "available", True)
        ]

    def first_ready(self):
        """The provider that would be tried first now, or None."""
        return (self._ready() or self.providers or [None])[0]

    def transcribe(self, flac: bytes, vocabulary: list[str]) -> tuple[str, str]:
        errors = []
        ready = self._ready() or self.providers
        # A provider can pass on a clip (Groq: too short, or near its free limit) while another can take it.
        seconds = flac_seconds(flac)
        ready = [p for p in ready if getattr(p, "takes", lambda _: True)(seconds)] or ready
        for provider in ready:
            try:
                return provider.transcribe(flac, vocabulary), provider.name
            except RateLimited as e:
                self._rest_until[provider.name] = time.monotonic() + (e.retry_after or 60)
                errors.append(str(e))
            except BadResult as e:
                errors.append(str(e))
            except Unavailable as e:
                if hasattr(provider, "went_down"):
                    # Its own check brings it back as soon as it answers again.
                    provider.went_down()
                else:
                    self._rest_until[provider.name] = time.monotonic() + 30
                errors.append(str(e))
            log.warning("%s", errors[-1])
        raise Unavailable("; ".join(errors) or "no speech provider is set up")


def build_providers(cfg: dict) -> list:
    providers = []
    for name in cfg["stt"]["order"]:
        if name == "groq" and config.key("GROQ_API_KEY"):
            groq = cfg["stt"].get("groq", {})
            providers.append(
                GroqWhisper(
                    config.key("GROQ_API_KEY"),
                    model=groq.get("model", "whisper-large-v3"),
                    language=groq.get("language", ""),
                    timeout=cfg["stt"]["timeout_seconds"],
                    local_under=groq.get("local_under_seconds", 0),
                    saving_from=groq.get("saving_from", 0.7),
                    usage_file=LEARNING_DB.parent / "groq_usage.json",
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


class BoloApp(rumps.App):
    def __init__(self, cfg: dict):
        super().__init__("Bolo", title=IDLE, quit_button="Quit")
        self.cfg = cfg
        self.status = rumps.MenuItem("Starting...")
        self.usage = Usage()
        self.used_today = rumps.MenuItem("Today: 0 dictations")
        self.voice_line = rumps.MenuItem("Voice training")
        self.learned = rumps.MenuItem("Learned words")
        self.saved = rumps.MenuItem("Saved dictations")
        self.at_login = rumps.MenuItem("Start at login", callback=self._toggle_login)
        self.at_login.state = login.starts_at_login()
        self.menu = [
            self.status,
            self.used_today,
            self.voice_line,
            None,
            rumps.MenuItem("Fix last dictation...", callback=lambda _: self._open_fix()),
            rumps.MenuItem("Settings…", callback=lambda _: self.settings_window.show(self.cfg)),
            self.saved,
            self.learned,
            self.at_login,
        ]
        self.recorder = audio.Recorder(cfg["audio"]["sample_rate"], cfg["audio"]["max_seconds"])
        self.chain = Chain(build_providers(cfg))
        self.cleaner = Cleaner(cfg)
        learning = cfg["learning"]
        self.learning = Learning(learning["enabled"])
        self.voice = None
        if self.learning.store and learning.get("save_voice", False):
            self.voice = Voice(self.learning.store, keep_days=learning.get("voice_keep_days", 14))
            self.voice.prune()
        self.voice_line.hidden = self.voice is None
        self.fix_window = FixWindow(self._fix_saved)
        self.settings_window = SettingsWindow(self._save_settings)
        self.watcher = Watcher(self._learn) if learning["enabled"] and learning["auto_capture"] else None
        self.pending = Pending(LEARNING_DB.parent / "pending", cfg["recovery"]["keep_hours"])
        # Shortcut (key code, modifiers) -> what it does. Both run on the main thread.
        self._shortcuts = {}
        for setting, action in (("fix_last", self._open_fix), ("paste_saved", self._paste_oldest_saved)):
            shortcut = parse_shortcut(cfg["hotkey"].get(setting, ""))
            if shortcut is None:
                log.warning("%s shortcut %r cannot be read", setting, cfg["hotkey"].get(setting))
            else:
                self._shortcuts[shortcut] = action
        self._refresh_learned()
        self._refresh_saved()
        self._refresh_usage()
        self._commands: queue.Queue[str] = queue.Queue()
        self._paste_lock = threading.Lock()
        self._held: str | None = None
        # Hands-free: recording goes on after the key is let go, until the next tap.
        self._locked = False
        self._down_at = 0.0
        self._last_tap = 0.0
        self._ignore_up: str | None = None
        self._lock_key = ""
        self._lock_session = 0
        # True when the dictation being recorded was started with the prompt-mode key.
        self._prompt_key_used = False
        # Turns a long recording into text while the user still speaks; None when not in use.
        self._early: EarlyText | None = None
        # True while a clip is being turned into text; Esc then cancels the paste.
        self._writing = False
        self._cancelled = threading.Event()
        self.overlay = Overlay(
            lambda: self.recorder.level, self._cancel, cfg.get("overlay", {}).get("show_words", True)
        )
        # The output device Bolo muted, or 0 when it muted none.
        self._muted = 0
        self.sounds = sounds.Sounds(cfg)
        # Where the start sound is in the clip being recorded: (first sample, samples to cut).
        self._beep = (0, 0)
        atexit.register(self._restore_sound)
        self._tap = None
        self._problem = self._check_setup()
        # The command the worker is on and when it began, or None while it waits for the next.
        self._busy: tuple[str, float] | None = None
        self._stuck_reported = False
        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()
        self._worker_id = worker.ident
        threading.Thread(target=self._retry_saved, daemon=True).start()
        self._watchdog_timer = rumps.Timer(self._watchdog, 0.25)
        self._watchdog_timer.start()

    def _check_setup(self) -> str:
        """Asks macOS for the permissions and starts the key listener. Returns a problem or ''."""
        trusted = AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(
            Quartz.kCGEventKeyDown
        )
        # A tap that can hold back keys lets Esc cancel without also reaching the focused app.
        # It needs Accessibility; without it, listen only and Esc reaches the app as well.
        for option in (Quartz.kCGEventTapOptionDefault, Quartz.kCGEventTapOptionListenOnly):
            self._tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, option, mask, self._on_event, None
            )
            if self._tap is not None:
                break
        if self._tap is None:
            return "Allow Input Monitoring, then restart"
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        if not trusted:
            return "Allow Accessibility, then restart"
        if not self.chain.providers:
            return "No speech provider is set up in config.toml"
        return ""

    def _prompt_key(self) -> str:
        """The key that records in prompt mode, or "" when that trigger is off."""
        key = self.cfg.get("prompt_mode", {}).get("key", "")
        return key if key in _KEYS and key != self.cfg["hotkey"]["push_to_talk"] else ""

    def _key_active(self, name: str) -> bool:
        return name in (self.cfg["hotkey"]["push_to_talk"], self._prompt_key())

    def _on_event(self, proxy, kind, event, refcon):
        if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            log.warning("key listener was switched off by macOS (%s); switching it back on", kind)
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        try:
            return self._handle_event(kind, event)
        except Exception:
            # An error here must not swallow the user's keystroke.
            log.exception("key event failed")
            return event

    def _handle_event(self, kind, event):
        if kind == Quartz.kCGEventKeyDown:
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if code == _ESCAPE and (self._held is not None or self._locked or self._writing):
                # Esc belongs to Bolo while it listens or writes, so it is held back from the app.
                self._cancel()
                return None
            # Another key while holding means a shortcut such as Cmd+C, not dictation.
            if self._held is not None:
                self._held = None
                self._commands.put("cancel")
            if not Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                held = Quartz.CGEventGetFlags(event) & sum(_MODIFIERS.values())
                action = self._shortcuts.get((code, held))
                if action:
                    AppHelper.callAfter(action)
            return event
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        for name, (keycode, flag) in _KEYS.items():
            if code != keycode:
                continue
            flags = Quartz.CGEventGetFlags(event)
            down = bool(flags & flag)
            # Ctrl, Shift, Option or Cmd already held: the start of a shortcut such as ⌃⌥F, not speech.
            chord = flags & sum(_MODIFIERS.values()) & ~_KEY_FAMILY[name]
            now = time.monotonic()
            if down and self._locked and self._key_active(name):
                # A tap during hands-free recording finishes it.
                self._locked = False
                self._held = None
                self._ignore_up = name
                self._commands.put("up")
            elif down and self._held is None and not chord and self._key_active(name):
                self._held = name
                self._down_at = now
                # A release missed earlier must not swallow the release of this press.
                self._ignore_up = None
                self._prompt_key_used = name == self._prompt_key()
                log.debug("talk key %s down", name)
                self._commands.put("down")
                if self.cfg["audio"]["hands_free"] and now - self._last_tap < _DOUBLE_TAP_SECONDS:
                    self._locked = True
                    self._lock_key = name
                    self._commands.put("lock")
            elif not down and self._held == name:
                self._released(now)
            elif not down and self._ignore_up == name:
                self._ignore_up = None
        return event

    def _released(self, now: float) -> None:
        """The talk key went up. Main thread."""
        self._held = None
        log.debug("talk key up after %.2f s%s", now - self._down_at, " (hands-free)" if self._locked else "")
        if self._locked:
            return
        if now - self._down_at < _TAP_SECONDS:
            # Too short to be speech; it may be the first half of a double tap.
            self._last_tap = now
            self._commands.put("tap")
        else:
            self._commands.put("up")

    def _watchdog(self, _timer=None) -> None:
        """Main thread, a few times a second. Undoes the ways the app could stop listening."""
        now = time.monotonic()
        # macOS can switch the key listener off without telling it, after sleep for example.
        if self._tap is not None and not Quartz.CGEventTapIsEnabled(self._tap):
            log.warning("key listener was off; switching it back on")
            Quartz.CGEventTapEnable(self._tap, True)
        # A key release can be missed while the listener is off. Without this, the next press
        # would be ignored and the recording would run on with the speakers muted.
        name = self._held
        if name and not self._locked and now - self._down_at > 0.5:
            flag = _KEY_FAMILY[name] or _KEYS[name][1]
            if not Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState) & flag:
                log.warning("missed the release of %s; finishing the recording", name)
                self._released(now)
        busy = self._busy
        if busy is None:
            self._stuck_reported = False
        elif now - busy[1] > _STUCK_SECONDS and not self._stuck_reported:
            # Every key press waits behind this one, so say so and record where it is stuck.
            self._stuck_reported = True
            frame = sys._current_frames().get(self._worker_id)
            where = "".join(traceback.format_stack(frame)) if frame else "unknown"
            log.warning("stuck for %.0f s on %r at:\n%s", now - busy[1], busy[0], where)
            self.title = WARN
            self.status.title = "Bolo is stuck: restart it (details in ~/Library/Logs/Bolo.log)"

    def _hands_free_limit(self, session: int) -> None:
        if self._locked and session == self._lock_session:
            self._locked = False
            self._commands.put("up")

    def _cancel(self) -> None:
        """Esc or the pill's ×: drops the recording, or the text still being written."""
        if self._held is not None or self._locked:
            self._held = None
            self._locked = False
            self._commands.put("cancel")
        elif self._writing:
            self._cancelled.set()
            self._pill("message", "Cancelled", 1.2, "cancel")

    def _pill(self, method: str, *args) -> None:
        AppHelper.callAfter(getattr(self.overlay, method), *args)

    def _show(self, title: str, status: str | None = None) -> None:
        def apply():
            self.title = title
            if status is not None:
                self.status.title = status
                # What the island shows when the user hovers on the notch.
                if title in (IDLE, WARN):
                    self.overlay.ready(status, title == IDLE)

        AppHelper.callAfter(apply)

    def _toggle_login(self, item) -> None:
        login.set_start_at_login(not item.state)
        item.state = login.starts_at_login()
        if item.state:
            # The login copy runs as its own app and needs the permissions granted to it once.
            self.status.title = f"Open {login.NAME} from Applications once and allow access"

    def _save_settings(self, changes: dict) -> None:
        """Main thread. Writes the settings window's changes to config.toml and applies them now."""
        try:
            config.save(changes)
            cfg = config.load()
        except (OSError, ValueError) as e:
            log.warning("settings not saved: %s", e)
            raise ValueError(f"config.toml could not be saved: {e}"[:120]) from e
        self._apply_settings(cfg)
        log.info("settings saved")
        self._pill("message", "Settings saved")

    def _apply_settings(self, cfg: dict) -> None:
        """Uses new settings without a restart. Keys, hands-free, mute, styles and prompt mode are
        read from self.cfg each time; the rest is rebuilt here."""
        self.cfg = cfg
        self.cleaner = Cleaner(cfg)
        threading.Thread(target=self._check_cleanup, daemon=True).start()
        self.sounds.update(cfg)
        self.overlay.show_words = cfg.get("overlay", {}).get("show_words", True)
        learning = cfg["learning"]
        if learning["enabled"] and learning["auto_capture"]:
            self.watcher = self.watcher or Watcher(self._learn)
        elif self.watcher:
            self.watcher.finish()
            self.watcher = None

    def _check_cleanup(self) -> None:
        note = self.cleaner.check()
        if note and not self._problem:
            self._show(IDLE, note)

    def _refresh_usage(self) -> None:
        """Updates the usage lines in the menu. Main thread only."""
        self.used_today.title = self.usage.line()
        if self.voice:
            self.voice_line.title = self.voice.line()

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
        self._pill("message", summary[:60])
        return summary[:80]

    def _warm_providers(self, prompt: bool = False) -> None:
        """Loads the speech and cleanup models while the user speaks. prompt also loads the bigger
        prompt-mode model, for apps where prompts are dictated."""
        for provider in self.chain.providers:
            if hasattr(provider, "warm"):
                provider.warm()
        self.cleaner.warm(prompt)

    def _prompt_likely(self) -> bool:
        """True when this dictation may well be a prompt: the prompt key is held, or the front app's
        style is "prompt" (Cursor, VS Code). Read from the app list only, so it costs nothing."""
        if self._prompt_key_used:
            return True
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return False
        return styles.for_app(self.cfg, app.localizedName() or "", app.bundleIdentifier() or "") == "prompt"

    def _restore_sound(self) -> None:
        if self._muted:
            device, self._muted = self._muted, 0
            audio.unmute_output(device)

    def _fetch_models(self) -> None:
        """Downloads the local speech model in the background the first time, so a new user is
        told why it is not ready yet. Groq, when set up, works meanwhile."""
        for provider in self.chain.providers:
            if isinstance(provider, LocalWhisper) and not provider.cached():
                message = "Downloading speech model (1.5 GB)…"
                self._show(BUSY, message)
                self._pill("message", message, 5)

                def fetch(provider=provider):
                    try:
                        ensure_model(provider.model)
                    except Exception as e:
                        log.warning("speech model not downloaded: %s", e)
                        self._show(WARN, "Speech model download failed; check the network")
                        return
                    self._show(WARN if self._problem else IDLE, self._problem or "Speech model ready")

                threading.Thread(target=fetch, daemon=True).start()

    def _worker(self) -> None:
        try:
            # The first stream start in a process takes seconds; pay that now, not on the first word.
            self.recorder.start()
            self.recorder.stop()
        except Exception as e:
            log.exception("microphone warm-up failed")
            self._problem = self._problem or f"Microphone: {e}"[:80]
        self._fetch_models()
        note = self.cleaner.check()
        self._warm_providers()
        self._show(WARN if self._problem else IDLE, self._problem or note or "Ready")
        # Started at login there is no terminal, so the log file is where a missing permission shows.
        if self._problem:
            log.warning("not ready: %s", self._problem)
        else:
            log.info("ready")
        log.info("permissions: %s", ", ".join(f"{name} {'yes' if ok else 'NO'}" for name, ok in permissions().items()))
        while True:
            command = self._commands.get()
            self._busy = (command, time.monotonic())
            try:
                if command == "down":
                    # Record first so no word is lost, then the start sound, then silence the speakers.
                    self.recorder.start()
                    at = self.recorder.frames
                    audible = self.sounds.play("start")
                    played = time.monotonic()
                    rate = self.recorder.sample_rate
                    self._beep = (at, int((audible + _SPEAKER_SECONDS + _ECHO_SECONDS) * rate) if audible else 0)
                    self._early = self._start_early(sum(self._beep))
                    self._show(RECORDING)
                    self._pill("listening")
                    open_up_front_app(learning=self.watcher is not None)
                    # Reload the models while the user is still speaking, if they were unloaded.
                    self._warm_providers(self._prompt_likely())
                    if self.watcher:
                        self.watcher.finish()
                    if self.cfg["audio"]["mute_while_recording"]:
                        # Muting at once would cut the start sound off.
                        if audible:
                            time.sleep(max(0.0, played + audible + _SPEAKER_SECONDS - time.monotonic()))
                        self._muted = audio.mute_output()
                elif command == "lock":
                    key = _KEY_LABELS.get(self._lock_key, "the key")
                    self._pill("listening", f"hands-free · tap {key} to stop")
                    # The recorder keeps nothing past max_seconds, so finish there.
                    self._lock_session += 1
                    limit = threading.Timer(
                        self.cfg["audio"]["max_seconds"], self._hands_free_limit, args=(self._lock_session,)
                    )
                    limit.daemon = True
                    limit.start()
                elif command == "tap":
                    self._stop_early()
                    self.recorder.stop()
                    self._restore_sound()
                    self._show(IDLE)
                    # It may be the first half of a double tap; a second press replaces the message.
                    log.info("dropped: too short (key held under %.1f s)", _TAP_SECONDS)
                    key = _KEY_LABELS.get(self.cfg["hotkey"]["push_to_talk"], "the key")
                    self._pill("message", f"Too short: hold {key} while you speak", 1.5, "warn")
                elif command == "cancel":
                    self._stop_early()
                    self.recorder.stop()
                    self._restore_sound()
                    self._show(IDLE)
                    self._pill("message", "Cancelled", 1.2, "cancel")
                elif command == "up":
                    if not self.recorder.recording:
                        # The microphone never opened; its error is already showing.
                        log.info("dropped: the microphone never opened")
                        self._stop_early()
                        self._restore_sound()
                        continue
                    early, self._early = self._early, None
                    if early:
                        early.stop()
                    recording = clip = self.recorder.stop()
                    # Where the user is when they let go is where the text belongs.
                    place = ax.place() if self.cfg["audio"].get("guard_window", True) else None
                    self._restore_sound()
                    self.sounds.play("stop")
                    # The microphone heard the start sound; Whisper must not, and it would also make
                    # a silent clip look loud and lower the gain prepare() gives the speech.
                    start, frames = self._beep
                    clip = audio.cut(clip, start, start + frames)
                    length = audio.seconds(clip, self.recorder.sample_rate)
                    peak = audio.peak_dbfs(clip)
                    if self.recorder.silent:
                        log.info("dropped: the microphone sent no audio")
                        self._show(WARN, "The microphone sent no audio: try again")
                        self._pill("message", "The microphone sent no audio. Try again.", 3, "warn")
                        continue
                    if length < self.cfg["audio"]["min_seconds"]:
                        log.info("dropped: too short (%.2f s of audio)", length)
                        self._show(IDLE, "Too short")
                        self._pill("message", "Too short: nothing heard", 1.6, "warn")
                        continue
                    if peak < _QUIET_DBFS:
                        log.info("dropped: too quiet (peak %.0f dBFS, %.1f s)", peak, length)
                        self._show(IDLE, "Too quiet: nothing heard")
                        self._pill("message", "Too quiet: nothing heard. Is the right mic on?", 2.5, "warn")
                        continue
                    threading.Thread(
                        target=self._dictate,
                        args=(clip, self._prompt_key_used, early, recording, place),
                        daemon=True,
                    ).start()
            except Exception as e:
                log.exception("recording failed")
                if command == "down" and self.recorder.recording:
                    # The microphone is on, so the dictation still works; only a side step failed.
                    self._show(RECORDING)
                    self._pill("listening")
                    continue
                self.sounds.play("error")
                problem = str(e) if isinstance(e, audio.MicError) else "Something went wrong. Details are in Bolo.log"
                self._show(WARN, problem[:80])
                self._pill("message", problem[:80], 4, "error")
                if command != "down":
                    # Never leave the speakers muted after a failure.
                    try:
                        self._restore_sound()
                    except Exception:
                        log.exception("sound not restored")
            finally:
                self._busy = None

    def _start_early(self, start: int) -> EarlyText | None:
        """Starts turning speech into text during the recording. Pieces go through the chain, so
        Groq hears them while it can, and a rate limit sends the rest to the local model."""
        seconds = self.cfg["audio"].get("chunk_seconds", 6)
        if not seconds or not self.chain.providers:
            return None
        terms = self.learning.terms(self.cfg["dictionary"]["terms"])
        vocabulary = terms + self.cfg["dictionary"].get("hints", [])
        return EarlyText(
            self.recorder, self.chain.transcribe, vocabulary, start, seconds, lambda text: self._pill("heard", text)
        )

    def _stop_early(self) -> None:
        if self._early:
            self._early.stop()
            self._early = None

    def _dictate(
        self, clip, prompt_key: bool = False, early: EarlyText | None = None, recording=None, place=None
    ) -> None:
        with self._paste_lock:
            self._show(BUSY)
            self._pill("transcribing")
            self._cancelled.clear()
            self._writing = True
            try:
                self._write(clip, prompt_key, early, recording, place)
            finally:
                self._writing = False

    def _write(
        self, clip, prompt_key: bool = False, early: EarlyText | None = None, recording=None, place=None
    ) -> None:
        """Turns the clip into text and pastes it, unless cancelled first. Nothing said is lost:
        if the speech cannot be turned into text the audio is saved and tried again later.
        early holds the text already made during the recording; recording is the clip before the
        start sound was cut, which the early text's sample positions refer to. place is where the
        user was on letting go; if they have moved since, the text is kept instead of pasted."""
        start = time.perf_counter()
        rate = self.recorder.sample_rate
        target = styles.detect(self.cfg)
        app = target.app
        before = early.finish() if early else None
        if before:
            # Only the speech after the last early piece is left to hear.
            rest = audio.prepare(recording[before[1] :], rate)
            flac = audio.to_flac(rest, rate) if audio.seconds(rest, rate) >= self.cfg["audio"]["min_seconds"] else b""
        else:
            speech = audio.prepare(clip, rate)
            if audio.seconds(speech, rate) < self.cfg["audio"]["min_seconds"]:
                log.info("dropped: no speech found in %.1f s of audio", len(clip) / rate)
                self._show(IDLE, "Nothing heard")
                self._pill("message", "Nothing heard", 1.6, "warn")
                return
            flac = audio.to_flac(speech, rate)
        try:
            raw, text, provider, route, heard = self._to_text(flac, target, prompt_key, before[0] if before else "")
        except Exception as e:
            log.warning("dictation failed, audio saved: %s", e)
            if before:
                # Save all of it, not only the part left after the early text.
                flac = audio.to_flac(audio.prepare(clip, rate), rate)
            self.pending.add_audio(flac, app)
            AppHelper.callAfter(self._refresh_saved)
            self.sounds.play("error")
            self._show(WARN, "Could not transcribe: audio saved, trying again")
            self._pill("message", "Couldn't transcribe. Don't worry, your audio is saved and will be tried again.", 4, "error")
            return
        total = time.perf_counter() - start
        if self._cancelled.is_set():
            log.info("cancelled after %.1f s", total)
            self._show(IDLE, "Cancelled")
            return
        if not text:
            self._pill("hide")
            return
        if place is not None and not ax.same_place(place, ax.place()):
            log.info("window changed while writing, text saved")
            self.pending.add_text(text, raw, app)
            AppHelper.callAfter(self._refresh_saved)
            self._show(IDLE, f"You switched windows: {self._paste_label()} to paste")
            self._pill("message", f"You switched windows. Press {self._paste_label()} to paste it here.", 4, "warn")
            return
        # With no text box to take it, the text goes on the clipboard and the island says so.
        typing, where = ax.typing_target()
        try:
            if typing is False:
                inject.copy(text)
            else:
                inject.paste(text)
        except inject.InjectError as e:
            log.warning("paste failed, text saved: %s", e)
            self.pending.add_text(text, raw, app)
            AppHelper.callAfter(self._refresh_saved)
            self.sounds.play("error")
            self._show(WARN, "Could not paste: text saved")
            self._pill("message", f"Couldn't paste. Text saved: press {self._paste_label()} to paste it.", 4, "error")
            return
        dictation_id = self.learning.record(app, raw, text, "prompt" if route == "prompt" else "")
        # A prompt is laid out unlike what was said, so it cannot teach the speech model.
        if self.voice and dictation_id and route != "prompt":
            threading.Thread(
                target=self._save_voice, args=(dictation_id, clip), daemon=True
            ).start()
        self.usage.add("dictations")
        self.usage.add("words", len(text.split()))
        self.usage.add("speaking_seconds", len(clip) / rate)
        self.usage.add("waiting_seconds", time.perf_counter() - start)
        if self.watcher and dictation_id and typing is not False:
            self.watcher.watch(dictation_id, text)
        log.info(
            "%s: %.1f s audio (%.0f s heard early), %d chars, speech %.2f s, cleanup %s %.2f s, style=%s, %s %s",
            provider, len(clip) / rate, before[1] / rate if before else 0, len(text), heard, route, total - heard,
            target.style, "copied, focus on" if typing is False else "pasted into", where,
        )
        AppHelper.callAfter(self._refresh_usage)
        if typing is False:
            self._show(IDLE, "Copied: no text box was selected")
            self._pill("message", "Copied. Click a text box and press ⌘V", 4, "copy")
            return
        self._show(IDLE, f"Last: {provider}, {total:.1f} s")
        if route in ("prompt-failed", "prompt-timeout"):
            self.sounds.play("error")
            self._pill("message", "Couldn't write the prompt: pasted plain text", 3, "error")
        else:
            words = len(text.split())
            self._pill("done", f"Pasted · {words} word{'' if words == 1 else 's'}")

    def _save_voice(self, dictation_id: int, clip) -> None:
        """Keeps the clip for voice training, in the form the speech models hear it. Whisper hears
        at most 30 s at once, so only clips up to that long can be trained on."""
        rate = self.recorder.sample_rate
        try:
            speech = audio.prepare(clip, rate)
            seconds = audio.seconds(speech, rate)
            if 0 < seconds <= 30:
                self.voice.save(dictation_id, audio.to_flac(speech, rate), seconds)
        except Exception:
            log.exception("voice clip not saved")

    def _to_text(
        self, flac: bytes, target: styles.Target | None = None, prompt_key: bool = False, before: str = ""
    ) -> tuple[str, str, str, str, float]:
        """Returns (raw transcript, text to paste, provider, cleanup route, seconds to hear it).
        target is the app the text goes into; a saved dictation has none and is written plainly.
        prompt_key is True when the prompt-mode key was held. before is text the local model already
        made from the start of this recording, and flac is what came after it (b"" for nothing)."""
        start = time.perf_counter()
        terms = self.learning.terms(self.cfg["dictionary"]["terms"])
        raw, provider = ("", "local")
        if flac:
            raw, provider = self.chain.transcribe(flac, terms + self.cfg["dictionary"].get("hints", []))
        raw = f"{before} {raw}".strip()
        heard = time.perf_counter() - start
        text = self.learning.apply(raw)
        app, style = (target.app, target.style) if target else ("", "default")
        prompt_mode = self.cfg.get("prompt_mode", {})
        rest = prompt_prefix(text, prompt_mode.get("prefixes", _PROMPT_PREFIXES)) if prompt_mode.get(
            "use_prefix", True) else None
        if prompt_key or rest is not None or (style == "prompt" and prompt_mode.get("in_prompt_apps", False)):
            text = text if rest is None else rest
            if not text.strip():
                return raw, "", provider, "prompt", heard
            text, route = self.cleaner.to_prompt(
                text, terms=terms, examples=self.learning.examples(text, app), style=style,
                on_llm=self._polishing(text, True) if target else None,
            )
        else:
            text, route = self.cleaner.clean(
                text, terms=terms, examples=self.learning.examples(text, app), style=style,
                on_llm=self._polishing(text, False) if target else None,
            )
        return raw, text, provider, route, heard

    def _polishing(self, text: str, prompt: bool):
        """Tells the island the cleanup model has started on text, unless the user cancelled."""

        def started(model: str) -> None:
            if not self._cancelled.is_set():
                self._pill("polishing", text, model, prompt)

        return started

    def _paste_label(self) -> str:
        return self.cfg["hotkey"].get("paste_saved", "").replace("<ctrl>", "⌃").replace("<alt>", "⌥").replace(
            "<cmd>", "⌘").replace("<shift>", "⇧").replace("+", "").upper()

    def _retry_saved(self) -> None:
        """Tries saved audio again in the background, waiting longer after each failure."""
        pruned = time.monotonic()
        while True:
            time.sleep(15)
            # The day's count starts again at midnight.
            AppHelper.callAfter(self._refresh_usage)
            if self.voice and time.monotonic() - pruned > 3600:
                pruned = time.monotonic()
                self.voice.prune()
            # Never compete with a dictation the user is making now.
            if self._held or self._locked or self._writing:
                continue
            for saved in self.pending.all():
                if self._held or self._locked or self._writing:
                    break
                if saved.due(time.time()):
                    self._try_saved(saved)

    def _try_saved(self, saved: Saved) -> bool:
        path = self.pending.audio_path(saved.id)
        if not path.exists():
            self.pending.remove(saved.id)
            return False
        try:
            # No lock: a dictation started meanwhile must not wait for this one.
            raw, text, provider, _, _ = self._to_text(path.read_bytes())
        except Exception as e:
            self.pending.tried(saved)
            log.info("saved dictation %s still failing (try %d): %s", saved.id, saved.tries, str(e)[:120])
            AppHelper.callAfter(self._refresh_saved)
            return False
        if self._held or self._locked or self._writing:
            # The user started dictating: keep it for the next round, as if not tried.
            return False
        if not text:
            self.pending.remove(saved.id)
        else:
            self.pending.tried(saved, text, raw)
            log.info("saved dictation %s transcribed by %s", saved.id, provider)
            self._show(IDLE, f"Saved dictation ready: {self._paste_label()} to paste")
            self._pill("message", f"Your saved dictation is ready. Press {self._paste_label()} to paste it.", 5)
        AppHelper.callAfter(self._refresh_saved)
        return bool(text)

    def _paste_oldest_saved(self) -> None:
        ready = [saved for saved in self.pending.all() if saved.ready]
        if ready:
            self._paste_saved(ready[0])
        else:
            self._pill("message", "No saved dictation is ready")

    def _paste_saved(self, saved: Saved) -> None:
        """Main thread. Pastes a ready saved dictation where the cursor is, then forgets it."""

        def paste():
            # Let the menu close and the keyboard go back to the app first.
            time.sleep(0.3)
            try:
                with self._paste_lock:
                    inject.paste(saved.text)
            except inject.InjectError as e:
                self._show(WARN, str(e)[:80])
                return
            self.pending.remove(saved.id)
            self.learning.record(saved.app, saved.raw, saved.text)
            AppHelper.callAfter(self._refresh_saved)

        threading.Thread(target=paste, daemon=True).start()

    def _refresh_saved(self) -> None:
        """Rebuilds the "Saved dictations" menu. Main thread only."""
        for title in list(self.saved.keys()):
            del self.saved[title]
        items = self.pending.all()
        waiting = sum(not saved.ready for saved in items)
        ready = len(items) - waiting
        self.saved.title = "Saved dictations" + (f" ({ready} ready)" if ready else f" ({waiting})" if waiting else "")
        if not items:
            self.saved.add(rumps.MenuItem("Nothing saved: every dictation went through"))
        for saved in reversed(items):
            when = time.strftime("%H:%M", time.localtime(saved.created))
            if saved.ready:
                preview = saved.text[:40] + ("…" if len(saved.text) > 40 else "")
                entry = rumps.MenuItem(f"{when} · {saved.app or 'unknown app'} · {preview}")
                entry.add(rumps.MenuItem("Paste at cursor", callback=lambda _, x=saved: self._paste_saved(x)))
                entry.add(rumps.MenuItem("Copy", callback=lambda _, x=saved: inject.copy(x.text)))
            else:
                tries = f"tried {saved.tries}×" if saved.tries else "waiting"
                entry = rumps.MenuItem(f"{when} · {saved.app or 'unknown app'} · audio saved, {tries}")
                entry.add(
                    rumps.MenuItem(
                        "Try now",
                        callback=lambda _, x=saved: threading.Thread(
                            target=self._try_saved, args=(x,), daemon=True
                        ).start(),
                    )
                )
            entry.add(rumps.MenuItem("Delete", callback=lambda _, x=saved: self._delete_saved(x)))
            self.saved.add(entry)

    def _delete_saved(self, saved: Saved) -> None:
        self.pending.remove(saved.id)
        self._refresh_saved()

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
        sys.exit("Bolo is already running (look for its icon in the menu bar).")
    # BOLO_DEBUG=1 also logs each talk-key press and release with its timing.
    level = logging.DEBUG if os.environ.get("BOLO_DEBUG") else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Menu bar only: no Dock icon.
    NSBundle.mainBundle().infoDictionary()["LSUIElement"] = "1"
    BoloApp(config.load()).run()


if __name__ == "__main__":
    main()
