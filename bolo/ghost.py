"""Faded words at the text cursor while the user speaks.

This is only a picture floating over the app: nothing is typed, so it cannot be sent or deleted.
The normal dictation still makes the real text, and the picture fades as that text lands.
"""

import logging
import threading
from collections.abc import Callable

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSObject,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCopyParameterizedAttributeValue,
    AXValueCreate,
    AXValueGetValue,
    kAXValueCFRangeType,
    kAXValueCGPointType,
    kAXValueCGRectType,
    kAXValueCGSizeType,
)
from CoreFoundation import CFRange
from Foundation import NSMakeRect, NSRunLoop, NSRunLoopCommonModes, NSTimer

from . import audio
from .learn import ax

log = logging.getLogger(__name__)

_EVERY_SECONDS = 0.8  # how often the words are read again
_WINDOW_SECONDS = 20  # the speech heard each time; the picture shows only the end of it
_MOST_CHARS = 70
_TYPE_RATE = 95  # characters a second, as in the island
_FPS = 60
_FADE_SECONDS = 0.14
_OPACITY = 0.45
_PAD = 6
_FIELD_PAD = 10
_LEAST_WIDTH, _MOST_WIDTH = 60, 620


def tail(text: str, most: int = _MOST_CHARS) -> str:
    """The end of the text, starting at a whole word."""
    text = " ".join(text.split())
    if len(text) <= most:
        return text
    end = text[-most:]
    return "…" + (end.split(" ", 1)[1] if " " in end else end)


def _get(element, attribute: str):
    error, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return value if error == 0 else None


def _bounds(element, text_range):
    error, value = AXUIElementCopyParameterizedAttributeValue(element, "AXBoundsForRange", text_range, None)
    if error != 0 or value is None:
        return None
    ok, rect = AXValueGetValue(value, kAXValueCGRectType, None)
    # Chromium answers (0, screen height, 0, 0) when it has no real position.
    if not ok or not 6 <= rect.size.height <= 80 or (rect.origin.x <= 0 and rect.origin.y <= 0):
        return None
    return rect.origin.x, rect.origin.y, rect.size.width, rect.size.height


def _frame(element):
    position, size = _get(element, "AXPosition"), _get(element, "AXSize")
    if position is None or size is None:
        return None
    ok_p, point = AXValueGetValue(position, kAXValueCGPointType, None)
    ok_s, box = AXValueGetValue(size, kAXValueCGSizeType, None)
    return (point[0], point[1], box[0], box[1]) if ok_p and ok_s and box[0] > 40 and box[1] > 10 else None


def _caret(element):
    selected = _get(element, "AXSelectedTextRange")
    if selected is None:
        return None
    rect = _bounds(element, selected)
    if rect:
        return rect[0] + rect[2], rect[1], rect[3]
    ok, span = AXValueGetValue(selected, kAXValueCFRangeType, None)
    if ok and span[0] > 0:
        rect = _bounds(element, AXValueCreate(kAXValueCFRangeType, CFRange(span[0] - 1, 1)))
        if rect:
            return rect[0] + rect[2], rect[1], rect[3]
    return None


def locate() -> tuple[tuple[float, float, float] | None, float]:
    """((x, y, line height) inside the text field, width left there), or (None, 0) when the words
    cannot be placed inside it. Without a cursor position only an empty field is used, so the words
    never cover text. Accessibility can take up to 0.5 s, so call this off the main thread."""
    element = ax._focused_element()
    if element is None or ax._SECURE in (_get(element, "AXRole"), _get(element, "AXSubrole")):
        return None, 0
    if ax.typing_target()[0] is False:
        return None, 0
    frame = _frame(element)
    caret = _caret(element)
    if caret:
        room = frame[0] + frame[2] - caret[0] - _FIELD_PAD if frame else _MOST_WIDTH
        return (caret, min(room, _MOST_WIDTH)) if room >= _LEAST_WIDTH else (None, 0)
    if frame is None or ax.value(element) != "":
        return None, 0
    x, y, width, height = frame
    line = 20
    top = y + (height - line) / 2 if height <= 56 else y + _FIELD_PAD
    return (x + _FIELD_PAD, top, line), min(width - 2 * _FIELD_PAD, _MOST_WIDTH)


class LiveText:
    """Reads the speech so far about once a second and passes the words to on_text. A failure only
    means fewer live words: this never touches the text that gets pasted."""

    def __init__(self, recorder, transcribe: Callable[[bytes], str], start: int, on_text: Callable[[str], None]):
        self._recorder = recorder
        self._transcribe = transcribe
        self._on_text = on_text
        self._take = recorder.take
        self._start = start
        self._rate = recorder.sample_rate
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-text")
        self._thread.start()

    def _run(self) -> None:
        heard = 0
        while not self._stop.wait(_EVERY_SECONDS):
            samples = self._recorder.snapshot(self._take, self._start)
            if len(samples) <= heard:
                continue
            heard = len(samples)
            speech = audio.prepare(samples[-_WINDOW_SECONDS * self._rate :], self._rate)
            if audio.seconds(speech, self._rate) < 0.3:
                continue
            try:
                text = self._transcribe(audio.to_flac(speech, self._rate))
            except Exception as e:
                log.debug("live words skipped: %s", type(e).__name__)
                continue
            if text and not self._stop.is_set():
                self._on_text(text)

    def stop(self) -> None:
        self._stop.set()


class _Clock(NSObject):
    def initWithGhost_(self, ghost):
        self = objc.super(_Clock, self).init()
        self.ghost = ghost
        return self

    def tick_(self, _timer):
        try:
            self.ghost.tick()
        except Exception:
            log.exception("live words stopped")
            self.ghost.hide()


class Ghost:
    """The floating words. Every method runs on the main thread."""

    def __init__(self):
        self._panel = None
        self._timer = None
        self._active = False
        self._fading = False
        self._text = ""
        self._shown = 0.0
        self._spot = None
        self._room = 0.0
        # True once words were on screen in this dictation: the real text then lands at once.
        self.was_shown = False

    def _build(self) -> None:
        self._clock = _Clock.alloc().initWithGhost_(self)
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 300, 30),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setHasShadow_(False)
        self._panel.setIgnoresMouseEvents_(True)
        self._panel.setLevel_(NSStatusWindowLevel)
        self._panel.setReleasedWhenClosed_(False)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self._label = NSTextField.labelWithString_("")
        self._label.setTextColor_(NSColor.labelColor().colorWithAlphaComponent_(_OPACITY))
        self._panel.contentView().addSubview_(self._label)

    def begin(self) -> None:
        if self._panel is None:
            self._build()
        self._stop_clock()
        self._panel.orderOut_(None)
        self._panel.setAlphaValue_(1.0)
        self._active, self._fading = True, False
        self._text, self._shown, self._spot = "", 0.0, None
        self.was_shown = False

    def update(self, text: str, spot, room: float) -> None:
        if not self._active:
            return
        if spot is None:
            # The field cannot be placed now: show nothing rather than words somewhere else.
            self._spot = None
            self._panel.orderOut_(None)
            self._stop_clock()
            return
        self._spot, self._room = spot, room
        text = tail(text)
        # Words that stayed the same are not typed again.
        same = 0
        for a, b in zip(self._text, text):
            if a != b:
                break
            same += 1
        self._text, self._shown = text, min(self._shown, same)
        self._start_clock()

    def end(self) -> None:
        """Fades the words away. Safe to call more than once."""
        self._active = False
        if self._panel is None or not self._panel.isVisible():
            self._stop_clock()
            return
        self._fading = True
        self._start_clock()

    def hide(self) -> None:
        self._active = self._fading = False
        self._stop_clock()
        if self._panel is not None:
            self._panel.orderOut_(None)

    def tick(self) -> None:
        if self._fading:
            alpha = self._panel.alphaValue() - 1 / (_FADE_SECONDS * _FPS)
            if alpha <= 0:
                self.hide()
            else:
                self._panel.setAlphaValue_(alpha)
            return
        if self._shown < len(self._text):
            self._shown = min(len(self._text), self._shown + _TYPE_RATE / _FPS)
            self._draw()
        else:
            self._stop_clock()

    def _draw(self) -> None:
        x, y, line = self._spot
        self._label.setFont_(NSFont.systemFontOfSize_(min(max(line * 0.82, 12), 22)))
        words = self._text[: int(self._shown)]
        self._label.setStringValue_(words)
        self._label.sizeToFit()
        # Only the newest words that fit stay, so the text never leaves the field.
        while self._label.frame().size.width > self._room and " " in words:
            words = "…" + words.split(" ", 1)[1]
            self._label.setStringValue_(words)
            self._label.sizeToFit()
        size = self._label.frame().size
        words_width = min(size.width, self._room)
        width, height = words_width + 2 * _PAD, size.height + 4
        self._label.setFrame_(NSMakeRect(_PAD, 2, words_width, size.height))
        top = NSScreen.screens()[0].frame().size.height
        self._panel.setFrame_display_(NSMakeRect(x - _PAD + 2, top - y - line + (line - height) / 2, width, height), True)
        self._panel.orderFrontRegardless()
        self.was_shown = True

    def _start_clock(self) -> None:
        if self._timer is None:
            self._timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / _FPS, self._clock, "tick:", None, True
            )
            NSRunLoop.currentRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)

    def _stop_clock(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
