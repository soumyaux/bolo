"""The settings window, opened from the menu bar. It edits config.toml through config.save()."""

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
    NSSlider,
    NSSound,
    NSTabView,
    NSTabViewItem,
    NSTextField,
    NSTextView,
    NSView,
    NSViewWidthSizable,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSMakeRect

from . import sounds
from .learn.fix_window import _install_edit_menu
from .styles import STYLES

_WIDTH, _HEIGHT, _MARGIN = 600, 560, 20
_LABEL = 200  # width of the labels on the left
_ROW = 30
# Talk keys, as in app._KEYS, with the names shown in the window.
KEYS = [("cmd_r", "Right ⌘"), ("alt_r", "Right ⌥"), ("ctrl_r", "Right ⌃"), ("fn", "fn")]
_NO_SOUND = "None"


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

    def soundChosen_(self, sender):
        self.window.preview(sender)

    def keyChanged_(self, sender):
        self.window.check_keys()


class SettingsWindow:
    """Call show(cfg) on the main thread. on_save(changes) gets {("section", "key"): value}; it raises
    ValueError with a message for the window when the changes cannot be saved."""

    def __init__(self, on_save: Callable[[dict], None]):
        self._on_save = on_save
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
        self._talk = self._popup(view, [name for _, name in KEYS], y, action="keyChanged:")
        y -= 32
        self._warning = NSTextField.wrappingLabelWithString_("")
        self._warning.setFrame_(NSMakeRect(_MARGIN + _LABEL, y - 2, 360, 30))
        self._warning.setTextColor_(NSColor.systemOrangeColor())
        self._warning.setFont_(NSFont.systemFontOfSize_(11))
        view.addSubview_(self._warning)
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
        self._cleanup = self._check(view, "Clean up with Gemini (styles, lists, Hinglish)", y)
        y -= _ROW
        self._learning = self._check(view, "Learn from my edits automatically", y)
        y -= _ROW
        self._early = self._check(view, "Write long dictations while I speak", y)
        y -= _ROW
        self._guard = self._check(view, "Don't paste if I switch windows", y)

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
        self._prompt_key = self._popup(view, ["Off"] + [name for _, name in KEYS], y, width=140, action="keyChanged:")
        y -= _ROW
        self._in_prompt_apps = self._check(view, "Always in apps with the “prompt” style", y)

    def _build(self) -> None:
        _install_edit_menu()
        self._actions = _Actions.alloc().initWithWindow_(self)
        # macOS does not let a background app come to the front on its own. A non-activating
        # panel takes the keyboard while the other app stays in front (see fix_window.py).
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _WIDTH, _HEIGHT),
            NSWindowStyleMaskTitled | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self._panel.setTitle_("Bolo Settings")
        self._panel.setFloatingPanel_(True)
        self._panel.setBecomesKeyOnlyIfNeeded_(False)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setReleasedWhenClosed_(False)
        content = self._panel.contentView()
        tabs = NSTabView.alloc().initWithFrame_(NSMakeRect(12, 52, _WIDTH - 24, _HEIGHT - 60))
        for title, fill in (("General", self._general), ("Dictionary", self._dictionary), ("Apps and prompts", self._apps_tab)):
            item = NSTabViewItem.alloc().initWithIdentifier_(title)
            item.setLabel_(title)
            tabs.addTabViewItem_(item)
            fill(item.view(), tabs.contentRect().size.height)
        content.addSubview_(tabs)
        self._tabs = tabs
        self._message = NSTextField.wrappingLabelWithString_("")
        self._message.setFrame_(NSMakeRect(_MARGIN, 10, _WIDTH - 2 * _MARGIN - 220, 34))
        self._message.setFont_(NSFont.systemFontOfSize_(11))
        content.addSubview_(self._message)
        save = self._button(content, "Save", "save:", NSMakeRect(_WIDTH - _MARGIN - 96, 12, 96, 32), "\r")
        # Return adds a line in the text boxes, so saving is Cmd+Return.
        save.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
        self._button(content, "Cancel", "cancel:", NSMakeRect(_WIDTH - _MARGIN - 200, 12, 96, 32), "\x1b")

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
        self._early.setState_(bool(get("audio", "chunk_seconds", 20)))
        self._guard.setState_(get("audio", "guard_window", True))
        self._terms.setString_("\n".join(get("dictionary", "terms", [])))
        self._hints.setString_("\n".join(get("dictionary", "hints", [])))
        self._apps = [[key, style] for key, style in cfg.get("styles", {}).get("apps", {}).items()]
        self._show_apps()
        self._prefix.setState_(get("prompt_mode", "use_prefix", True))
        self._choose(self._prompt_key, names.get(get("prompt_mode", "key", ""), "Off"))
        self._in_prompt_apps.setState_(get("prompt_mode", "in_prompt_apps", False))
        self._fill_running()
        self._message.setStringValue_("")
        self.check_keys()

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
        self._read_styles()
        lines = lambda box: [line.strip() for line in str(box.string()).splitlines() if line.strip()]  # noqa: E731
        sound = lambda kind: "" if (t := str(self._sound_popups[kind].titleOfSelectedItem())) == _NO_SOUND else t  # noqa: E731
        old_chunk = self._cfg.get("audio", {}).get("chunk_seconds", 20) or 20
        old_apps = self._cfg.get("styles", {}).get("apps", {})
        return {
            ("hotkey", "push_to_talk"): talk,
            ("audio", "hands_free"): bool(self._hands_free.state()),
            ("audio", "mute_while_recording"): bool(self._mute.state()),
            ("audio", "chunk_seconds"): old_chunk if self._early.state() else 0,
            ("audio", "guard_window"): bool(self._guard.state()),
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

    def check_keys(self) -> None:
        """Warns when a key is also Wispr Flow's: both apps would type while Wispr runs."""
        wispr = self._cfg.get("wispr", {})
        talk, prompt_key = self._key(self._talk), self._key(self._prompt_key)
        clash = [k for k in (talk, prompt_key) if k and wispr.get("takeover") and k == wispr.get("wispr_key")]
        self._warning.setStringValue_(
            f"Wispr Flow uses {dict(KEYS)[clash[0]]} too: quit Wispr while using Bolo, or both will type." if clash else ""
        )

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

    def close(self) -> None:
        self._panel.orderOut_(None)

    def show(self, cfg: dict) -> None:
        if self._panel is None:
            self._build()
        if not self._panel.isVisible():
            self._cfg = cfg
            self._fill(cfg)
            self._tabs.selectTabViewItemAtIndex_(0)
            self._panel.center()
        self._panel.orderFrontRegardless()
        self._panel.makeKeyWindow()
