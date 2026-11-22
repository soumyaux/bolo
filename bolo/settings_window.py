"""The settings window, opened from the menu bar. It edits config.toml through config.save()."""

import threading
from collections.abc import Callable

import objc
from AppKit import (
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelBorder,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSEventModifierFlagCommand,
    NSFileManager,
    NSFont,
    NSObject,
    NSPanel,
    NSPopUpButton,
    NSScrollView,
    NSSecureTextField,
    NSSegmentedControl,
    NSSlider,
    NSSound,
    NSTextField,
    NSTextView,
    NSView,
    NSViewWidthSizable,
    NSVisualEffectView,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL, NSMakeRect
from PyObjCTools import AppHelper

from . import audio, config, sounds
from .learn.fix_window import _install_edit_menu
from .stt.groq import key_problem
from .styles import STYLES

_WIDTH, _HEIGHT, _MARGIN = 620, 610, 20
_LABEL = 200  # width of the labels on the left
_ROW = 30
# Talk keys, as in app._KEYS, with the names shown in the window.
KEYS = [("cmd_r", "Right ⌘"), ("alt_r", "Right ⌥"), ("ctrl_r", "Right ⌃"), ("fn", "fn")]
_NO_SOUND = "None"
_DEFAULT_MIC = "Mac default"
# (local languages, Groq language, name shown).
LANGUAGES = [(["en"], "en", "English"), (["en", "hi"], "", "English + Hindi")]
# The switch in Settings › Speech: (engine order, name shown).
MODES = [(["local"], "Local"), (["groq", "local"], "AI")]
_MODE_NOTES = (
    "Speech stays on this Mac. Works offline and on a VPN.",
    "Speech goes to Groq with your own free key. This Mac takes over when Groq is out of reach.",
)
_GROQ_KEYS_URL = "https://console.groq.com/keys"


def app_name(key: str) -> str:
    """The name of the app with this bundle ID, or the key itself when it is a name already."""
    url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(key) if "." in key else None
    if url is None:
        return key
    return str(NSFileManager.defaultManager().displayNameAtPath_(url.path())).removesuffix(".app")


def running_apps() -> list[tuple[str, str]]:
    """(name, bundle ID) of the apps with a window, for the "Add running app" menu."""
    apps = {}
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() == NSApplicationActivationPolicyRegular and app.bundleIdentifier():
            apps[app.bundleIdentifier()] = app.localizedName() or app.bundleIdentifier()
    return sorted(((name, bundle) for bundle, name in apps.items()), key=lambda a: a[0].lower())


def _has_class(name: str) -> bool:
    try:
        objc.lookUpClass(name)
        return True
    except objc.nosuchclass_error:
        return False


class _Flipless(NSView):
    """The window's content, laid out from the bottom up as AppKit does by default."""


class _Flipped(NSView):
    """Lays the app rows out from the top down."""

    def isFlipped(self):
        return True


class _Actions(NSObject):
    """Receives the clicks; AppKit needs an Objective-C object for that."""

    def initWithWindow_(self, window):
        self = objc.super(_Actions, self).init()
        self.window = window
        return self

    def save_(self, sender):
        self.window.save()

    def cancel_(self, sender):
        self.window.close()

    def remove_(self, sender):
        self.window.remove_app(sender.tag())

    def addRunning_(self, sender):
        self.window.add_running()

    def addTyped_(self, sender):
        self.window.add_typed()

    def section_(self, sender):
        self.window.select(sender.selectedSegment())

    def restart_(self, sender):
        self.window.restart()

    def modeChanged_(self, sender):
        self.window.show_mode()

    def getKey_(self, sender):
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(_GROQ_KEYS_URL))

    def testKey_(self, sender):
        self.window.test_key()

    def soundChosen_(self, sender):
        self.window.preview(sender)


class SettingsWindow:
    """Call show(cfg) on the main thread. on_save(changes) gets {("section", "key"): value}; it raises
    ValueError with a message for the window when the changes cannot be saved."""

    def __init__(self, on_save: Callable[[dict], None], on_restart: Callable[[], None] | None = None,
                 on_key_saved: Callable[[], None] | None = None):
        self._on_save = on_save
        self._on_key_saved = on_key_saved
        self._on_restart = on_restart
        self._panel = None
        self._cfg: dict = {}
        self._apps: list[list[str]] = []  # [key, style]

    # Building

    def _label(self, view, text: str, y: float, x: float = _MARGIN, width: float = _LABEL):
        label = NSTextField.labelWithString_(text)
        label.setFrame_(NSMakeRect(x, y + 4, width, 18))
        view.addSubview_(label)
        return label

    def _check(self, view, title: str, y: float, x: float = _MARGIN + _LABEL):
        box = NSButton.checkboxWithTitle_target_action_(title, None, None)
        box.setFrame_(NSMakeRect(x, y, 340, 24))
        view.addSubview_(box)
        return box

    def _popup(self, view, items: list[str], y: float, x: float = _MARGIN + _LABEL, width: float = 180, action=None):
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(x, y, width, 26), False)
        popup.addItemsWithTitles_(items)
        if action:
            popup.setTarget_(self._actions)
            popup.setAction_(action)
        view.addSubview_(popup)
        return popup

    def _text_box(self, view, frame):
        scroll = NSScrollView.alloc().initWithFrame_(frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        text = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        text.setAutoresizingMask_(NSViewWidthSizable)
        text.setRichText_(False)
        text.setAllowsUndo_(True)
        text.setFont_(NSFont.systemFontOfSize_(13))
        # Terms must be saved exactly as typed.
        text.setAutomaticQuoteSubstitutionEnabled_(False)
        text.setAutomaticDashSubstitutionEnabled_(False)
        text.setAutomaticSpellingCorrectionEnabled_(False)
        text.setAutomaticTextReplacementEnabled_(False)
        scroll.setDocumentView_(text)
        view.addSubview_(scroll)
        return text

    def _button(self, view, title: str, action: str, frame, key: str = ""):
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(self._actions)
        button.setAction_(action)
        if key:
            button.setKeyEquivalent_(key)
        view.addSubview_(button)
        return button

    def _general(self, view, top: float) -> None:
        y = top - _ROW
        self._label(view, "Talk key (hold to speak)", y)
        self._talk = self._popup(view, [name for _, name in KEYS], y)
        y -= 34
        self._hands_free = self._check(view, "Double-tap the talk key for hands-free", y)
        y -= _ROW
        self._mute = self._check(view, "Mute the speakers while recording", y)
        y -= _ROW + 8
        self._label(view, "Sounds", y)
        self._sounds_on = self._check(view, "Play a sound on start and stop", y)
        y -= _ROW
        self._label(view, "Volume", y, x=_MARGIN + 18, width=_LABEL - 18)
        self._volume = NSSlider.sliderWithValue_minValue_maxValue_target_action_(0.5, 0, 1, None, None)
        self._volume.setFrame_(NSMakeRect(_MARGIN + _LABEL, y, 200, 24))
        view.addSubview_(self._volume)
        names = sounds.names()
        self._sound_popups = {}
        for kind, title in (("start", "Start sound"), ("stop", "Stop sound"), ("error", "Error sound")):
            y -= _ROW
            self._label(view, title, y, x=_MARGIN + 18, width=_LABEL - 18)
            choices = ([_NO_SOUND] if kind == "error" else []) + names
            self._sound_popups[kind] = self._popup(view, choices, y, width=140, action="soundChosen:")
        y -= _ROW + 8
        self._label(view, "Writing", y)
        self._cleanup = self._check(view, "Clean up on this Mac (styles, lists, Hinglish)", y)
        y -= _ROW
        self._learning = self._check(view, "Learn from my edits automatically", y)
        y -= _ROW
        self._early = self._check(view, "Write long dictations while I speak", y)
        y -= _ROW
        self._guard = self._check(view, "Don't paste if I switch windows", y)
        y -= _ROW
        self._show_words = self._check(view, "Show my words in the island while writing", y)

    def _speech(self, view, top: float) -> None:
        y = top - _ROW
        self._label(view, "Microphone", y)
        self._mic = self._popup(view, [_DEFAULT_MIC], y, width=300)
        y -= _ROW + 8
        self._label(view, "Language you speak", y)
        self._language = self._popup(view, [name for _, _, name in LANGUAGES], y, width=300)
        note = NSTextField.wrappingLabelWithString_("English only is about twice as fast on this Mac.")
        note.setFrame_(NSMakeRect(_MARGIN + _LABEL, y - 22, 320, 18))
        note.setFont_(NSFont.systemFontOfSize_(11))
        note.setTextColor_(NSColor.secondaryLabelColor())
        view.addSubview_(note)
        y -= _ROW + 26
        self._label(view, "Writing the text", y)
        self._animate = self._check(view, "Type it word by word into the text box", y)
        y -= _ROW
        self._live_preview = self._check(view, "Show faded words while I speak", y)
        y -= _ROW + 8
        self._label(view, "Speech runs on", y)
        self._mode = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            [name for _, name in MODES], 0, self._actions, "modeChanged:"
        )
        self._mode.setFrame_(NSMakeRect(_MARGIN + _LABEL, y, 160, 26))
        view.addSubview_(self._mode)
        self._mode_note = self._note(view, _MARGIN + _LABEL, y - 38, 320, 34)
        y -= 76
        self._key_label = self._label(view, "Groq API key", y)
        self._key_field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(_MARGIN + _LABEL, y + 1, 320, 24))
        view.addSubview_(self._key_field)
        y -= 36
        self._get_key = self._button(view, "Get a free key", "getKey:", NSMakeRect(_MARGIN + _LABEL - 4, y, 136, 30))
        self._test_key = self._button(view, "Test and save", "testKey:", NSMakeRect(_MARGIN + _LABEL + 136, y, 136, 30))
        self._key_note = self._note(view, _MARGIN + _LABEL, y - 36, 320, 34)

    def _note(self, view, x: float, y: float, width: float, height: float):
        note = NSTextField.wrappingLabelWithString_("")
        note.setFrame_(NSMakeRect(x, y, width, height))
        note.setFont_(NSFont.systemFontOfSize_(11))
        note.setTextColor_(NSColor.secondaryLabelColor())
        view.addSubview_(note)
        return note

    def _dictionary(self, view, top: float) -> None:
        half = (_WIDTH - 2 * _MARGIN - 40 - 16) / 2
        for i, (title, note) in enumerate(
            (("Terms", "Spelled exactly as written, one per line."),
             ("Hints", "Words you say often, one per line. They help with sound-alikes."))
        ):
            x = _MARGIN + i * (half + 16)
            self._label(view, title, top - 26, x=x, width=half)
            hint = NSTextField.wrappingLabelWithString_(note)
            hint.setFrame_(NSMakeRect(x, top - 62, half, 32))
            hint.setFont_(NSFont.systemFontOfSize_(11))
            hint.setTextColor_(NSColor.secondaryLabelColor())
            view.addSubview_(hint)
            box = self._text_box(view, NSMakeRect(x, 12, half, top - 80))
            if i == 0:
                self._terms = box
            else:
                self._hints = box

    def _apps_tab(self, view, top: float) -> None:
        width = _WIDTH - 2 * _MARGIN - 40
        self._label(view, "Writing style per app", top - 26, width=width)
        list_height = 170
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(_MARGIN, top - 36 - list_height, width, list_height))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        self._rows = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, width - 18, list_height))
        scroll.setDocumentView_(self._rows)
        view.addSubview_(scroll)
        y = top - 36 - list_height - 36
        self._running = self._popup(view, ["Add running app…"], y, x=_MARGIN, width=230, action="addRunning:")
        self._typed = NSTextField.alloc().initWithFrame_(NSMakeRect(_MARGIN + 240, y + 2, 200, 22))
        self._typed.setPlaceholderString_("or type a bundle ID")
        view.addSubview_(self._typed)
        self._button(view, "Add", "addTyped:", NSMakeRect(_MARGIN + 446, y - 2, 80, 30))
        y -= 48
        self._label(view, "Prompt mode", y)
        self._prefix = self._check(view, "Start with “as a prompt” or “prompt mode”", y)
        y -= _ROW
        self._label(view, "Or hold this key instead", y, x=_MARGIN + 18, width=_LABEL - 18)
        self._prompt_key = self._popup(view, ["Off"] + [name for _, name in KEYS], y, width=140)
        y -= _ROW
        self._in_prompt_apps = self._check(view, "Always in apps with the “prompt” style", y)

    def _background(self, frame):
        """Liquid Glass on macOS 26, the frosted sidebar material before it."""
        glass = objc.lookUpClass("NSGlassEffectView") if _has_class("NSGlassEffectView") else None
        if glass is not None:
            view = glass.alloc().initWithFrame_(frame)
            view.setCornerRadius_(0)
            return view
        view = NSVisualEffectView.alloc().initWithFrame_(frame)
        view.setMaterial_(7)  # sidebar
        view.setBlendingMode_(0)  # behind the window
        view.setState_(1)  # always active
        return view

    def _build(self) -> None:
        _install_edit_menu()
        self._actions = _Actions.alloc().initWithWindow_(self)
        # macOS does not let a background app come to the front on its own. A non-activating
        # panel takes the keyboard while the other app stays in front (see fix_window.py).
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _WIDTH, _HEIGHT),
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskFullSizeContentView
            | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self._panel.setTitle_("Bolo Settings")
        self._panel.setTitlebarAppearsTransparent_(True)
        self._panel.setTitleVisibility_(1)  # hidden: the section switcher says where you are
        self._panel.setMovableByWindowBackground_(True)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setFloatingPanel_(True)
        self._panel.setBecomesKeyOnlyIfNeeded_(False)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setReleasedWhenClosed_(False)
        background = self._background(NSMakeRect(0, 0, _WIDTH, _HEIGHT))
        content = _Flipless.alloc().initWithFrame_(NSMakeRect(0, 0, _WIDTH, _HEIGHT))
        if hasattr(background, "setContentView_"):
            background.setContentView_(content)
        else:
            background.addSubview_(content)
        self._panel.setContentView_(background)

        sections = (("General", self._general), ("Speech", self._speech), ("Dictionary", self._dictionary),
                    ("Apps and prompts", self._apps_tab))
        switcher = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            [title for title, _ in sections], 0, self._actions, "section:"
        )
        switcher.sizeToFit()
        width = switcher.frame().size.width
        switcher.setFrameOrigin_(((_WIDTH - width) / 2, _HEIGHT - 44))
        content.addSubview_(switcher)
        self._switcher = switcher
        # The sections sit on a rounded card, as in System Settings.
        card_frame = NSMakeRect(16, 56, _WIDTH - 32, _HEIGHT - 112)
        self._pages = []
        for _, fill in sections:
            page = NSView.alloc().initWithFrame_(card_frame)
            page.setWantsLayer_(True)
            page.layer().setCornerRadius_(14)
            page.layer().setBackgroundColor_(NSColor.controlBackgroundColor().colorWithAlphaComponent_(0.55).CGColor())
            fill(page, card_frame.size.height)
            content.addSubview_(page)
            self._pages.append(page)
        self._message = NSTextField.wrappingLabelWithString_("")
        self._message.setFrame_(NSMakeRect(_MARGIN + 130, 10, _WIDTH - 2 * _MARGIN - 350, 34))
        self._message.setFont_(NSFont.systemFontOfSize_(11))
        content.addSubview_(self._message)
        self._button(content, "Restart Bolo", "restart:", NSMakeRect(_MARGIN - 4, 12, 124, 32))
        save = self._button(content, "Save", "save:", NSMakeRect(_WIDTH - _MARGIN - 96, 12, 96, 32), "\r")
        # Return adds a line in the text boxes, so saving is Cmd+Return.
        save.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
        save.setBezelColor_(NSColor.controlAccentColor())
        self._button(content, "Cancel", "cancel:", NSMakeRect(_WIDTH - _MARGIN - 200, 12, 96, 32), "\x1b")

    def select(self, index: int) -> None:
        for i, page in enumerate(self._pages):
            page.setHidden_(i != index)
        self._switcher.setSelectedSegment_(index)

    # Filling in and reading back

    def _choose(self, popup, title: str) -> None:
        if popup.itemWithTitle_(title) is None:
            popup.addItemWithTitle_(title)
        popup.selectItemWithTitle_(title)

    def _fill(self, cfg: dict) -> None:
        get = lambda section, key, default: cfg.get(section, {}).get(key, default)  # noqa: E731
        names = dict(KEYS)
        self._choose(self._talk, names.get(cfg["hotkey"]["push_to_talk"], "Right ⌘"))
        self._hands_free.setState_(get("audio", "hands_free", True))
        self._mute.setState_(get("audio", "mute_while_recording", True))
        self._sounds_on.setState_(get("sounds", "enabled", True))
        self._volume.setDoubleValue_(float(get("sounds", "volume", 0.5)))
        for kind, default in (("start", "Tink"), ("stop", "Pop"), ("error", "Basso")):
            self._choose(self._sound_popups[kind], get("sounds", kind, default) or _NO_SOUND)
        self._cleanup.setState_(get("cleanup", "enabled", True))
        self._learning.setState_(get("learning", "auto_capture", True))
        self._early.setState_(bool(get("audio", "chunk_seconds", 6)))
        self._guard.setState_(get("audio", "guard_window", True))
        self._show_words.setState_(get("overlay", "show_words", True))
        self._mic.removeAllItems()
        self._mic.addItemsWithTitles_([_DEFAULT_MIC] + audio.input_names())
        self._choose(self._mic, get("audio", "microphone", "") or _DEFAULT_MIC)
        languages = cfg.get("stt", {}).get("local", {}).get("languages", ["en"])
        self._choose(self._language, next((name for langs, _, name in LANGUAGES if langs == languages), LANGUAGES[0][2]))
        order = cfg.get("stt", {}).get("order", ["local"])
        has_key = bool(config.key("GROQ_API_KEY"))
        self._mode.setSelectedSegment_(1 if has_key and "groq" in order else 0)
        self._key_field.setStringValue_("")
        self._test_key.setEnabled_(True)
        self._show_key_state()
        self.show_mode()
        self._animate.setState_(get("typing", "animate", True))
        self._live_preview.setState_(get("typing", "live_preview", True))
        self._terms.setString_("\n".join(get("dictionary", "terms", [])))
        self._hints.setString_("\n".join(get("dictionary", "hints", [])))
        self._apps = [[key, style] for key, style in cfg.get("styles", {}).get("apps", {}).items()]
        self._show_apps()
        self._prefix.setState_(get("prompt_mode", "use_prefix", True))
        self._choose(self._prompt_key, names.get(get("prompt_mode", "key", ""), "Off"))
        self._in_prompt_apps.setState_(get("prompt_mode", "in_prompt_apps", False))
        self._fill_running()
        self._message.setStringValue_("")

    def _fill_running(self) -> None:
        self._running.removeAllItems()
        self._running.addItemWithTitle_("Add running app…")
        self._running_list = running_apps()
        for name, bundle in self._running_list:
            self._running.addItemWithTitle_(f"{name}  ({bundle})")

    def _show_apps(self) -> None:
        for view in list(self._rows.subviews()):
            view.removeFromSuperview()
        width = self._rows.frame().size.width
        height = max(len(self._apps) * 30 + 6, self._rows.superview().frame().size.height if self._rows.superview() else 0)
        self._rows.setFrameSize_((width, height))
        for i, (key, style) in enumerate(self._apps):
            y = 4 + i * 30
            name = app_name(key)
            label = NSTextField.labelWithString_(name if name == key else f"{name}  ({key})")
            label.setFrame_(NSMakeRect(8, y + 4, width - 240, 18))
            label.setLineBreakMode_(5)  # truncate in the middle
            self._rows.addSubview_(label)
            popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(width - 228, y, 130, 26), False)
            popup.addItemsWithTitles_(list(STYLES))
            self._choose(popup, style)
            popup.setTag_(i)
            self._rows.addSubview_(popup)
            remove = NSButton.buttonWithTitle_target_action_("Remove", self._actions, "remove:")
            remove.setFrame_(NSMakeRect(width - 92, y, 86, 26))
            remove.setTag_(i)
            self._rows.addSubview_(remove)

    def _read_styles(self) -> None:
        for view in self._rows.subviews():
            if isinstance(view, NSPopUpButton):
                self._apps[view.tag()][1] = str(view.titleOfSelectedItem())

    def _key(self, popup) -> str:
        return {name: key for key, name in KEYS}.get(str(popup.titleOfSelectedItem()), "")

    def collect(self) -> dict:
        """The window's values as changes for config.save(). Raises ValueError when they clash."""
        talk, prompt_key = self._key(self._talk), self._key(self._prompt_key)
        if prompt_key and prompt_key == talk:
            raise ValueError("The prompt-mode key must differ from the talk key.")
        ai = self._mode.selectedSegment() == 1
        if ai and str(self._key_field.stringValue()).strip():
            raise ValueError("Press Test and save for the new key first.")
        if ai and not config.key("GROQ_API_KEY"):
            raise ValueError("AI needs a Groq key. Paste one and press Test and save.")
        self._read_styles()
        lines = lambda box: [line.strip() for line in str(box.string()).splitlines() if line.strip()]  # noqa: E731
        sound = lambda kind: "" if (t := str(self._sound_popups[kind].titleOfSelectedItem())) == _NO_SOUND else t  # noqa: E731
        old_chunk = self._cfg.get("audio", {}).get("chunk_seconds", 6) or 6
        old_apps = self._cfg.get("styles", {}).get("apps", {})
        mic = str(self._mic.titleOfSelectedItem())
        local, groq, _ = LANGUAGES[self._language.indexOfSelectedItem()]
        return {
            ("audio", "microphone"): "" if mic == _DEFAULT_MIC else mic,
            ("stt", "local", "languages"): local,
            ("stt", "groq", "language"): groq,
            ("stt", "order"): MODES[self._mode.selectedSegment()][0],
            ("typing", "animate"): bool(self._animate.state()),
            ("typing", "live_preview"): bool(self._live_preview.state()),
            ("hotkey", "push_to_talk"): talk,
            ("audio", "hands_free"): bool(self._hands_free.state()),
            ("audio", "mute_while_recording"): bool(self._mute.state()),
            ("audio", "chunk_seconds"): old_chunk if self._early.state() else 0,
            ("audio", "guard_window"): bool(self._guard.state()),
            ("overlay", "show_words"): bool(self._show_words.state()),
            ("sounds", "enabled"): bool(self._sounds_on.state()),
            ("sounds", "volume"): round(self._volume.doubleValue(), 2),
            ("sounds", "start"): sound("start"),
            ("sounds", "stop"): sound("stop"),
            ("sounds", "error"): sound("error"),
            ("cleanup", "enabled"): bool(self._cleanup.state()),
            ("learning", "auto_capture"): bool(self._learning.state()),
            ("dictionary", "terms"): lines(self._terms),
            ("dictionary", "hints"): lines(self._hints),
            # A new app gets its name as a comment in config.toml.
            ("styles", "apps"): {
                key: style if key in old_apps else (style, app_name(key) if app_name(key) != key else "")
                for key, style in self._apps
            },
            ("prompt_mode", "use_prefix"): bool(self._prefix.state()),
            ("prompt_mode", "key"): prompt_key,
            ("prompt_mode", "in_prompt_apps"): bool(self._in_prompt_apps.state()),
        }

    # Actions

    def show_mode(self) -> None:
        mode = self._mode.selectedSegment()
        self._mode_note.setStringValue_(_MODE_NOTES[mode])
        for view in (self._key_label, self._key_field, self._get_key, self._test_key, self._key_note):
            view.setHidden_(mode == 0)

    def _show_key_state(self) -> None:
        has_key = bool(config.key("GROQ_API_KEY"))
        self._key_field.setPlaceholderString_("Saved. Paste a new key to replace it" if has_key else "Paste your key here")
        self._key_note.setTextColor_(NSColor.secondaryLabelColor())
        self._key_note.setStringValue_("Saved on this Mac, readable by you only." if has_key else "Free, no card needed.")

    def test_key(self) -> None:
        key = str(self._key_field.stringValue()).strip()
        if not key:
            self._key_note.setTextColor_(NSColor.systemRedColor())
            self._key_note.setStringValue_("Paste your key first.")
            return
        self._test_key.setEnabled_(False)
        self._key_note.setTextColor_(NSColor.secondaryLabelColor())
        self._key_note.setStringValue_("Testing…")
        threading.Thread(target=lambda: self._tested(key, key_problem(key)), daemon=True, name="groq-key-test").start()

    def _tested(self, key: str, problem: str) -> None:
        if not problem:
            try:
                config.save_key("GROQ_API_KEY", key)
            except OSError:
                problem = "The key could not be saved on this Mac."
        AppHelper.callAfter(self._show_tested, problem)

    def _show_tested(self, problem: str) -> None:
        self._test_key.setEnabled_(True)
        if problem:
            self._key_note.setTextColor_(NSColor.systemRedColor())
            self._key_note.setStringValue_(problem)
            return
        self._key_field.setStringValue_("")
        self._show_key_state()
        self._key_note.setTextColor_(NSColor.systemGreenColor())
        self._key_note.setStringValue_("Key works and is saved. Press Save to use AI.")
        if self._on_key_saved:
            self._on_key_saved()

    def preview(self, popup) -> None:
        title = str(popup.titleOfSelectedItem())
        sound = NSSound.soundNamed_(title) if title != _NO_SOUND else None
        if sound is not None:
            sound.setVolume_(self._volume.doubleValue())
            sound.play()

    def remove_app(self, index: int) -> None:
        self._read_styles()
        del self._apps[index]
        self._show_apps()

    def _add(self, key: str, style: str = "default") -> None:
        key = key.strip()
        self._read_styles()
        if key and all(existing != key for existing, _ in self._apps):
            self._apps.append([key, style])
            self._show_apps()

    def add_running(self) -> None:
        index = self._running.indexOfSelectedItem() - 1
        if 0 <= index < len(self._running_list):
            self._add(self._running_list[index][1])
        self._running.selectItemAtIndex_(0)

    def add_typed(self) -> None:
        self._add(str(self._typed.stringValue()))
        self._typed.setStringValue_("")

    def save(self) -> None:
        try:
            self._on_save(self.collect())
        except ValueError as e:
            self._message.setTextColor_(NSColor.systemRedColor())
            self._message.setStringValue_(str(e))
            return
        self._message.setStringValue_("")
        self._panel.orderOut_(None)

    def restart(self) -> None:
        self._panel.orderOut_(None)
        if self._on_restart:
            self._on_restart()

    def close(self) -> None:
        self._panel.orderOut_(None)

    def show(self, cfg: dict) -> None:
        if self._panel is None:
            self._build()
        if not self._panel.isVisible():
            self._cfg = cfg
            self._fill(cfg)
            self.select(0)
            self._panel.center()
        self._panel.orderFrontRegardless()
        self._panel.makeKeyWindow()
