"""A small pill near the bottom of the screen that shows what the app is doing.

The menu bar icon is easy to miss, and on a Mac with a notch it can be hidden. The pill shows
"Listening" with a live sound meter while the key is held, "Writing" while the speech is turned
into text, and a short message after. Its × button cancels. Call every method on the main thread.
"""

from collections.abc import Callable

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSFont,
    NSPanel,
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSMakeRect, NSObject, NSPointInRect, NSTimer

_HEIGHT, _BOTTOM = 40, 28
_BARS = 5
# How long a message stays before the pill hides.
_MESSAGE_SECONDS = 1.6


def _screen_with_mouse():
    """The display the user is working on; with two screens the main one may be the other."""
    mouse = NSEvent.mouseLocation()
    for screen in NSScreen.screens():
        if NSPointInRect(mouse, screen.frame()):
            return screen
    return NSScreen.mainScreen()


class _Meter(NSView):
    """A red dot and bars that follow the microphone level."""

    def initWithFrame_(self, frame):
        self = objc.super(_Meter, self).initWithFrame_(frame)
        self.levels = [0.0] * _BARS
        return self

    def drawRect_(self, rect):
        height = self.bounds().size.height
        NSColor.systemRedColor().set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(0, height / 2 - 5, 10, 10)).fill()
        NSColor.labelColor().set()
        for i, level in enumerate(self.levels):
            bar = max(3.0, level * (height - 8))
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(18 + i * 6, (height - bar) / 2, 3, bar), 1.5, 1.5
            ).fill()


class _Target(NSObject):
    """Receives the timer ticks and the × click; AppKit needs an Objective-C object for that."""

    def initWithOverlay_(self, overlay):
        self = objc.super(_Target, self).init()
        self.overlay = overlay
        return self

    def tick_(self, timer):
        self.overlay._tick()

    def hide_(self, timer):
        self.overlay.hide()

    def cancel_(self, sender):
        self.overlay._on_cancel()


class Overlay:
    """level() returns the microphone level from 0 to 1; on_cancel runs when × is clicked."""

    def __init__(self, level: Callable[[], float], on_cancel: Callable[[], None]):
        self._level = level
        self._on_cancel = on_cancel
        self._panel = None
        self._ticker = None
        self._hider = None

    def _build(self) -> None:
        self._target = _Target.alloc().initWithOverlay_(self)
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 200, _HEIGHT),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        # Above other windows and full-screen apps, on every desktop, and never takes the keyboard.
        self._panel.setLevel_(NSStatusWindowLevel)
        self._panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setHasShadow_(True)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setReleasedWhenClosed_(False)

        self._background = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, _HEIGHT))
        self._background.setMaterial_(NSVisualEffectMaterialHUDWindow)
        self._background.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        self._background.setState_(NSVisualEffectStateActive)
        self._background.setWantsLayer_(True)
        self._background.layer().setCornerRadius_(_HEIGHT / 2)
        self._background.layer().setMasksToBounds_(True)
        self._panel.setContentView_(self._background)

        self._meter = _Meter.alloc().initWithFrame_(NSMakeRect(16, 6, 48, _HEIGHT - 12))
        self._spinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(16, 12, 16, 16))
        self._spinner.setStyle_(NSProgressIndicatorStyleSpinning)
        self._spinner.setDisplayedWhenStopped_(False)
        self._label = NSTextField.labelWithString_("")
        self._label.setFont_(NSFont.systemFontOfSize_weight_(13, 0.3))
        self._hint = NSTextField.labelWithString_("")
        self._hint.setFont_(NSFont.systemFontOfSize_(11))
        self._hint.setTextColor_(NSColor.secondaryLabelColor())
        self._close = NSButton.buttonWithTitle_target_action_("✕", self._target, "cancel:")
        self._close.setBordered_(False)
        self._close.setFont_(NSFont.systemFontOfSize_(13))
        self._close.setToolTip_("Cancel (Esc)")
        for view in (self._meter, self._spinner, self._label, self._hint, self._close):
            self._background.addSubview_(view)

    def _layout(self, lead: float, cancel: bool) -> None:
        """Places the label after the meter or spinner, sizes the pill to fit, and centres it."""
        self._label.sizeToFit()
        self._hint.sizeToFit()
        label, hint = self._label.frame().size, self._hint.frame().size
        x = 16 + lead
        self._label.setFrameOrigin_((x, (_HEIGHT - label.height) / 2))
        x += label.width
        if hint.width:
            self._hint.setFrameOrigin_((x + 8, (_HEIGHT - hint.height) / 2))
            x += 8 + hint.width
        self._close.setHidden_(not cancel)
        if cancel:
            self._close.setFrame_(NSMakeRect(x + 6, (_HEIGHT - 24) / 2, 24, 24))
            x += 30
        width = x + 14
        screen = _screen_with_mouse().visibleFrame()
        self._panel.setFrame_display_(
            NSMakeRect(
                screen.origin.x + (screen.size.width - width) / 2, screen.origin.y + _BOTTOM, width, _HEIGHT
            ),
            True,
        )
        self._background.setFrame_(NSMakeRect(0, 0, width, _HEIGHT))

    def _stop_timers(self) -> None:
        for timer in (self._ticker, self._hider):
            if timer is not None:
                timer.invalidate()
        self._ticker = self._hider = None

    def _show(self, label: str, hint: str, mode: str) -> None:
        if self._panel is None:
            self._build()
        self._stop_timers()
        self._label.setStringValue_(label)
        self._hint.setStringValue_(hint)
        self._meter.setHidden_(mode != "listening")
        if mode == "busy":
            self._spinner.startAnimation_(None)
        else:
            self._spinner.stopAnimation_(None)
        lead = {"listening": 56, "busy": 24}.get(mode, 0)
        self._layout(lead, cancel=mode in ("listening", "busy"))
        self._panel.orderFrontRegardless()

    def listening(self, hint: str = "esc to cancel") -> None:
        self._show("Listening", hint, "listening")
        self._meter.levels = [0.0] * _BARS
        self._ticker = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 30, self._target, "tick:", None, True
        )

    def busy(self, label: str = "Writing…") -> None:
        self._show(label, "", "busy")

    def message(self, text: str, seconds: float = _MESSAGE_SECONDS) -> None:
        """Shows text for a few seconds, then hides."""
        self._show(text, "", "message")
        self._hider = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            seconds, self._target, "hide:", None, False
        )

    def hide(self) -> None:
        self._stop_timers()
        if self._panel is not None:
            self._spinner.stopAnimation_(None)
            self._panel.orderOut_(None)

    def _tick(self) -> None:
        # The newest level enters on the right; the bars scroll left like a small waveform.
        self._meter.levels = [*self._meter.levels[1:], min(1.0, self._level())]
        self._meter.setNeedsDisplay_(True)
