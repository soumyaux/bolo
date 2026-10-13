"""The "fix last dictation" window: shows the last pasted text so the user can correct it."""

from collections.abc import Callable

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezelBorder,
    NSBezelStyleRounded,
    NSButton,
    NSEventModifierFlagCommand,
    NSFont,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSPanel,
    NSScrollView,
    NSTextView,
    NSViewHeightSizable,
    NSViewMaxYMargin,
    NSViewMinXMargin,
    NSViewWidthSizable,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakeRect

_WIDTH, _HEIGHT, _MARGIN, _BUTTONS = 520, 260, 16, 44


def _install_edit_menu() -> None:
    """A menu bar app has no Edit menu, and without one Cmd+C, Cmd+V and Cmd+Z do nothing."""
    if NSApp.mainMenu() is not None:
        return
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (
        ("Undo", "undo:", "z"),
        ("Redo", "redo:", "Z"),
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
        ("Select All", "selectAll:", "a"),
    ):
        edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    holder = NSMenuItem.alloc().init()
    holder.setSubmenu_(edit)
    main = NSMenu.alloc().init()
    main.addItem_(holder)
    NSApp.setMainMenu_(main)


class _Buttons(NSObject):
    """Receives the button clicks; AppKit needs an Objective-C object for that."""

    def initWithWindow_(self, window):
        self = objc.super(_Buttons, self).init()
        self.window = window
        return self

    def save_(self, sender):
        self.window.close(save=True)

    def cancel_(self, sender):
        self.window.close(save=False)


class FixWindow:
    """Call show() on the main thread. on_save(dictation_id, before, after) runs if the text changed."""

    def __init__(self, on_save: Callable[[int, str, str], None]):
        self._on_save = on_save
        self._panel = None
        self._dictation_id = 0
        self._before = ""

    def _button(self, title: str, action: str, key: str, x: float):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, _MARGIN - 4, 96, 32))
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(self._buttons)
        button.setAction_(action)
        button.setKeyEquivalent_(key)
        button.setAutoresizingMask_(NSViewMinXMargin | NSViewMaxYMargin)
        return button

    def _build(self) -> None:
        _install_edit_menu()
        # macOS does not let a background app come to the front on its own. A non-activating
        # panel takes the keyboard while the other app stays in front, and gives it back on close.
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskResizable | NSWindowStyleMaskNonactivatingPanel
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _WIDTH, _HEIGHT), style, NSBackingStoreBuffered, False
        )
        self._panel.setTitle_("Fix last dictation")
        self._panel.setFloatingPanel_(True)
        self._panel.setBecomesKeyOnlyIfNeeded_(False)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setReleasedWhenClosed_(False)
        content = self._panel.contentView()

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(_MARGIN, _BUTTONS + _MARGIN, _WIDTH - 2 * _MARGIN, _HEIGHT - _BUTTONS - 2 * _MARGIN)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._text = NSTextView.alloc().initWithFrame_(scroll.contentView().bounds())
        self._text.setAutoresizingMask_(NSViewWidthSizable)
        self._text.setRichText_(False)
        self._text.setAllowsUndo_(True)
        self._text.setFont_(NSFont.systemFontOfSize_(14))
        # The correction must be saved exactly as typed.
        self._text.setAutomaticQuoteSubstitutionEnabled_(False)
        self._text.setAutomaticDashSubstitutionEnabled_(False)
        self._text.setAutomaticSpellingCorrectionEnabled_(False)
        self._text.setAutomaticTextReplacementEnabled_(False)
        scroll.setDocumentView_(self._text)
        content.addSubview_(scroll)

        self._buttons = _Buttons.alloc().initWithWindow_(self)
        save = self._button("Save", "save:", "\r", _WIDTH - _MARGIN - 96)
        # Return adds a line in the text, so saving is Cmd+Return.
        save.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
        content.addSubview_(save)
        content.addSubview_(self._button("Cancel", "cancel:", "\x1b", _WIDTH - _MARGIN - 200))

    def show(self, dictation_id: int, text: str) -> None:
        if self._panel is None:
            self._build()
        if not self._panel.isVisible():
            self._dictation_id, self._before = dictation_id, text
            self._text.setString_(text)
            self._panel.center()
        self._panel.orderFrontRegardless()
        self._panel.makeKeyWindow()
        self._panel.makeFirstResponder_(self._text)

    def close(self, save: bool) -> None:
        after = str(self._text.string()).strip()
        self._panel.orderOut_(None)
        if save and after and after != self._before:
            self._on_save(self._dictation_id, self._before, after)
