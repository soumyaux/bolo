"""The island: a black shape that grows out of the MacBook notch and shows what Bolo is doing.

It stays hidden until something happens; hovering on the notch peeks it open to say whether Bolo
is ready. While the key is held it shows an orb that pulses with the voice and a live meter; while
the speech becomes text, a shimmering label; while the cleanup model works, the words themselves;
and a check mark that draws itself once the text is pasted. On a screen without a notch it floats
from the top edge. Everything moves with Core Animation springs, so Python only sets targets and
the window server animates. Call every method on the main thread.
"""

import math
import time
from collections.abc import Callable

import objc
from AppKit import (
    NSApplicationDidChangeScreenParametersNotification,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSColor,
    NSEvent,
    NSEventMaskMouseMoved,
    NSFont,
    NSFontAttributeName,
    NSFontDescriptorSystemDesignRounded,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSHapticFeedbackManager,
    NSImage,
    NSImageSymbolConfiguration,
    NSMainMenuWindowLevel,
    NSPanel,
    NSScreen,
    NSStringDrawingUsesLineFragmentOrigin,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSWorkspace,
)
from Foundation import (
    NSMakeRect,
    NSNotificationCenter,
    NSNumber,
    NSObject,
    NSPointInRect,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSTimer,
    NSValue,
)
from Quartz import (
    CABasicAnimation,
    CACurrentMediaTime,
    CAGradientLayer,
    CAKeyframeAnimation,
    CALayer,
    CAMediaTimingFunction,
    CAShapeLayer,
    CASpringAnimation,
    CATextLayer,
    CATransaction,
    CATransition,
    CGColorCreateSRGB,
    CGPathAddCurveToPoint,
    CGPathAddEllipseInRect,
    CGPathAddLineToPoint,
    CGPathAddQuadCurveToPoint,
    CGPathCloseSubpath,
    CGPathCreateMutable,
    CGPathMoveToPoint,
    CIFilter,
    kCAAlignmentCenter,
    kCAAlignmentLeft,
    kCAAlignmentRight,
    kCAFillModeBackwards,
    kCAGradientLayerRadial,
    kCAGravityResizeAspect,
    kCALineCapRound,
    kCALineJoinRound,
    kCAMediaTimingFunctionEaseIn,
    kCAMediaTimingFunctionEaseInEaseOut,
    kCAMediaTimingFunctionEaseOut,
    kCATransitionFade,
    kCATruncationEnd,
    kCATruncationNone,
)

# The panel is this big and mostly transparent; the largest island fits inside it.
_W, _H = 640, 260
_PAD = 14  # between an ear's content and the island's side
_BODY_PAD = 18
_COMPACT_R, _OPEN_R = 12, 24  # bottom corner radius
_SHOULDER = 7  # the small inward curve where the notch meets the top edge
# A screen without a notch: the island floats this far from the top, around a small centre gap.
_FLOAT_TOP, _FLOAT_GAP, _FLOAT_BAR = 6, 36, 32
_MESSAGE_SECONDS = 1.6
_DONE_SECONDS = 1.3
_PEEK_CLOSE_SECONDS = 0.3
_BARS = 9
_ENVELOPE = [0.35, 0.55, 0.78, 0.92, 1.0, 0.92, 0.78, 0.55, 0.35]
_TYPE_RATE = 1.6  # characters per frame at 60 fps, about 95 a second
_POLISH_WIDTH, _WORD_LINES = 440, 3
_ACTIVE = {"listening", "transcribing", "polishing"}


def _screen_with_mouse():
    """The display the user is working on; with two screens the main one may be the other."""
    mouse = NSEvent.mouseLocation()
    for screen in NSScreen.screens():
        if NSPointInRect(mouse, _with_top_edge(screen.frame())):
            return screen
    return NSScreen.mainScreen()


def _with_top_edge(frame):
    """frame, counting its top edge in: a mouse pushed against the top of the screen sits on it."""
    return NSMakeRect(frame.origin.x, frame.origin.y, frame.size.width, frame.size.height + 1)


def _calm() -> bool:
    """True when the user asked macOS to reduce motion: fades instead of springs."""
    return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())


def _c(r: float, g: float, b: float, a: float = 1.0):
    return CGColorCreateSRGB(r, g, b, a)


_WHITE = _c(1, 1, 1)
_RED = _c(1, 0.24, 0.33)
_GREEN = _c(0.2, 0.84, 0.42)
_AMBER = _c(1, 0.72, 0.2)
_BLUE = _c(0.32, 0.62, 1)


def _font(size: float, weight: float = NSFontWeightRegular):
    base = NSFont.systemFontOfSize_weight_(size, weight)
    rounded = base.fontDescriptor().fontDescriptorWithDesign_(NSFontDescriptorSystemDesignRounded)
    return (NSFont.fontWithDescriptor_size_(rounded, size) if rounded else None) or base


_TITLE = _font(13, NSFontWeightSemibold)
_BODY = _font(13, NSFontWeightRegular)
_SMALL = _font(12, NSFontWeightMedium)
_CHIP = _font(10.5, NSFontWeightMedium)
_CLOCK = NSFont.monospacedDigitSystemFontOfSize_weight_(12, NSFontWeightMedium)


def _line(font) -> float:
    return math.ceil(font.ascender() - font.descender() + font.leading())


def _measure(text: str, font, width: float | None = None) -> tuple[float, float]:
    string = NSAttributedString.alloc().initWithString_attributes_(text, {NSFontAttributeName: font})
    if width is None:
        size = string.size()
    else:
        size = string.boundingRectWithSize_options_context_(
            (width, 10_000), NSStringDrawingUsesLineFragmentOrigin, None
        ).size
    return math.ceil(size.width) + 2, math.ceil(size.height)


def _fits(text: str, font, width: float, lines: int) -> bool:
    return _measure(text, font, width)[1] <= lines * _line(font) + 1


def _tail(text: str, font, width: float, lines: int) -> str:
    """The end of text that fits in the lines, after an ellipsis, so the newest words show."""
    if _fits(text, font, width, lines):
        return text
    words = text.split()
    low, high = 1, max(1, len(words) - 1)
    while low < high:
        middle = (low + high) // 2
        if _fits("…" + " ".join(words[middle:]), font, width, lines):
            high = middle
        else:
            low = middle + 1
    return "…" + " ".join(words[low:])


def _island_path(cx: float, top: float, w: float, h: float, r: float, s: float):
    """The island's outline. s > 0 curves the top corners outward into the screen edge, as the
    notch does; s < 0 rounds them, for a floating island. Every outline has the same elements in
    the same order, so a spring can morph any one into any other."""
    path = CGPathCreateMutable()
    left, right, bottom = cx - w / 2, cx + w / 2, top - h
    k = 0.448  # cubic handles that draw a quarter circle
    t = abs(s)
    CGPathMoveToPoint(path, None, left - s, top)
    CGPathAddQuadCurveToPoint(path, None, left, top, left, top - t)
    CGPathAddLineToPoint(path, None, left, bottom + r)
    CGPathAddCurveToPoint(path, None, left, bottom + r * k, left + r * k, bottom, left + r, bottom)
    CGPathAddLineToPoint(path, None, right - r, bottom)
    CGPathAddCurveToPoint(path, None, right - r * k, bottom, right, bottom + r * k, right, bottom + r)
    CGPathAddLineToPoint(path, None, right, top - t)
    CGPathAddQuadCurveToPoint(path, None, right, top, right + s, top)
    CGPathCloseSubpath(path)
    return path


# Motion

def _timed(animation, delay: float = 0.0):
    if delay:
        animation.setBeginTime_(CACurrentMediaTime() + delay)
        animation.setFillMode_(kCAFillModeBackwards)
    return animation


def _ease(name: str):
    return CAMediaTimingFunction.functionWithName_(name)


def _now_value(layer, key: str):
    """The value on screen now, so a new animation carries on from wherever the last one got to."""
    present = layer.presentationLayer()
    return (present or layer).valueForKeyPath_(key)


def _spring(layer, key: str, value, stiffness: float = 260, damping: float = 24, delay: float = 0.0) -> None:
    start = _now_value(layer, key)
    layer.setValue_forKeyPath_(value, key)
    if _calm():
        animation = CABasicAnimation.animationWithKeyPath_(key)
        animation.setDuration_(0.22)
        animation.setTimingFunction_(_ease(kCAMediaTimingFunctionEaseInEaseOut))
    else:
        animation = CASpringAnimation.animationWithKeyPath_(key)
        animation.setMass_(1)
        animation.setStiffness_(stiffness)
        animation.setDamping_(damping)
        animation.setDuration_(animation.settlingDuration())
    animation.setFromValue_(start)
    animation.setToValue_(value)
    layer.addAnimation_forKey_(_timed(animation, delay), key)


def _tween(layer, key: str, start, end, seconds: float, ease=kCAMediaTimingFunctionEaseOut, delay: float = 0.0):
    layer.setValue_forKeyPath_(end, key)
    animation = CABasicAnimation.animationWithKeyPath_(key)
    animation.setFromValue_(start)
    animation.setToValue_(end)
    animation.setDuration_(seconds)
    animation.setTimingFunction_(_ease(ease))
    layer.addAnimation_forKey_(_timed(animation, delay), key)


def _reveal(layer, delay: float) -> None:
    """Fades in from a blur while growing to size: the Dynamic Island's entrance."""
    _tween(layer, "opacity", 0.0, 1.0, 0.28, delay=delay)
    if _calm():
        layer.setValue_forKeyPath_(0, "filters.blur.inputRadius")
        layer.setValue_forKeyPath_(1, "transform.scale")
        return
    _tween(layer, "filters.blur.inputRadius", 8.0, 0.0, 0.34, delay=delay)
    layer.setValue_forKeyPath_(0.7, "transform.scale")
    _spring(layer, "transform.scale", NSNumber.numberWithDouble_(1.0), 300, 20, delay)


def _conceal(layer) -> None:
    _tween(layer, "opacity", _now_value(layer, "opacity"), 0.0, 0.16, kCAMediaTimingFunctionEaseIn)
    if not _calm():
        _tween(layer, "filters.blur.inputRadius", 0.0, 6.0, 0.18, kCAMediaTimingFunctionEaseIn)
        _tween(layer, "transform.scale", _now_value(layer, "transform.scale"), 0.85, 0.2, kCAMediaTimingFunctionEaseIn)


def _swap(layer) -> None:
    """Crossfades a layer's next change of content."""
    fade = CATransition.animation()
    fade.setType_(kCATransitionFade)
    fade.setDuration_(0.22)
    layer.addAnimation_forKey_(fade, "swap")


def _shimmer(layer, low: float) -> None:
    """A band of light that sweeps across the layer for as long as it shows."""
    mask = CAGradientLayer.layer()
    mask.setStartPoint_((0, 0.5))
    mask.setEndPoint_((1, 0.5))
    mask.setColors_([_c(1, 1, 1, low), _c(1, 1, 1, 1), _c(1, 1, 1, low)])
    sweep = CABasicAnimation.animationWithKeyPath_("locations")
    sweep.setFromValue_([-0.3, -0.15, 0.0])
    sweep.setToValue_([1.0, 1.15, 1.3])
    sweep.setDuration_(1.6)
    sweep.setRepeatCount_(1e9)
    mask.addAnimation_forKey_(sweep, "shimmer")
    layer.setMask_(mask)


# Layers

def _text(font, color, align=kCAAlignmentLeft):
    layer = CATextLayer.layer()
    layer.setFont_(font)
    layer.setFontSize_(font.pointSize())
    layer.setForegroundColor_(color)
    layer.setAlignmentMode_(align)
    layer.setTruncationMode_(kCATruncationEnd)
    return layer


def _circle(size: float, color):
    layer = CALayer.layer()
    layer.setBounds_(((0, 0), (size, size)))
    layer.setCornerRadius_(size / 2)
    layer.setBackgroundColor_(color)
    return layer


def _symbol_image(name: str, size: float, weight: float):
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    return image.imageWithSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight))


def _symbol(name: str, size: float, weight: float, colors: list):
    """An SF Symbol painted with a gradient: the symbol is the mask of a gradient layer."""
    layer = CAGradientLayer.layer()
    layer.setStartPoint_((0, 1))
    layer.setEndPoint_((1, 0))
    layer.setMask_(CALayer.layer())
    layer.mask().setContentsGravity_(kCAGravityResizeAspect)
    _set_symbol(layer, name, size, weight, colors)
    return layer


def _set_symbol(layer, name: str, size: float, weight: float, colors: list) -> None:
    image = _symbol_image(name, size, weight)
    width, height = image.size()
    layer.setBounds_(((0, 0), (width, height)))
    layer.setColors_(colors if len(colors) > 1 else colors * 2)
    layer.mask().setContents_(image)
    layer.mask().setFrame_(((0, 0), (width, height)))


def _scale_all(layer, scale: float) -> None:
    layer.setContentsScale_(scale)
    for sub in layer.sublayers() or []:
        _scale_all(sub, scale)
    if layer.mask() is not None:
        _scale_all(layer.mask(), scale)


_MESSAGE_LOOK = {
    "info": ("info.circle.fill", [_BLUE]),
    "warn": ("exclamationmark.triangle.fill", [_AMBER]),
    "error": ("xmark.octagon.fill", [_RED]),
    "cancel": ("xmark.circle.fill", [_c(1, 1, 1, 0.5)]),
    "copy": ("doc.on.clipboard.fill", [_c(0.35, 0.88, 1), _BLUE]),
}


class _IslandView(NSView):
    """The panel's content; it receives the click on ×."""

    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        self.overlay._click(self.convertPoint_fromView_(event.locationInWindow(), None))


class _Target(NSObject):
    """Receives timers and notifications; AppKit needs an Objective-C object for that."""

    def initWithOverlay_(self, overlay):
        self = objc.super(_Target, self).init()
        self.overlay = overlay
        return self

    def tick_(self, timer):
        self.overlay._tick()

    def later_(self, timer):
        self.overlay._fire_later()

    def screensChanged_(self, note):
        self.overlay._screens_changed()


class _Part:
    """One piece of content; shown says whether it is on screen or fading out."""

    def __init__(self, layer):
        self.layer = layer
        self.shown = False


class Overlay:
    """level() returns the microphone level from 0 to 1; on_cancel runs when × is clicked.
    show_words lets the island show the words while the cleanup model works on them."""

    def __init__(self, level: Callable[[], float], on_cancel: Callable[[], None], show_words: bool = True):
        self._level = level
        self._on_cancel = on_cancel
        self.show_words = show_words
        self._panel = None
        self._ticker = None
        self._later = None
        self._later_fn = None
        self._state = "hidden"
        self._hover = False
        self._status = ("Starting…", True)
        self._hint = ""
        self._caption = ""
        self._started = 0.0
        self._words = ""
        self._typed = 0.0
        self._model = ""
        self._prompt = False
        self._done = ""
        self._message = ("", "info")
        self._lvl = 0.0
        self._bar_levels = [0.0] * _BARS
        self._cancel_at = None
        self._rect = NSMakeRect(0, 0, 0, 0)

    # Public: one method per state

    def ready(self, status: str, ok: bool = True) -> None:
        """What a peek shows: "Ready", the last result, or the setup problem (ok False)."""
        self._status = (status, ok)
        # The first call builds the island, so hovering on the notch works before any dictation.
        if self._panel is None:
            self._build()
        if self._state == "peek":
            self._render()

    def listening(self, hint: str = "") -> None:
        if self._state != "listening":
            self._started = time.monotonic()
            self._caption = ""
            self._lvl = 0.0
            self._bar_levels = [0.0] * _BARS
        self._hint = hint
        self._go("listening")

    def heard(self, text: str) -> None:
        """The text made so far from a long recording, while the user still speaks."""
        self._caption = text
        if self._state == "listening":
            self._render()

    def transcribing(self) -> None:
        self._go("transcribing")

    def polishing(self, raw: str, model: str = "", prompt: bool = False) -> None:
        """The cleanup model is at work on raw: the words type in under a shimmer."""
        self._words, self._typed = " ".join(raw.split()), 0.0
        self._model, self._prompt = model, prompt
        self._go("polishing")

    def done(self, summary: str = "Pasted") -> None:
        self._done = summary
        self._go("done")
        self._play_check()
        self._after(_DONE_SECONDS, self.hide)

    def message(self, text: str, seconds: float = _MESSAGE_SECONDS, kind: str = "info") -> None:
        """Shows text for a few seconds, then hides. kind is info, warn, error, cancel or copy."""
        self._message = (text, kind if kind in _MESSAGE_LOOK else "info")
        self._go("message")
        if kind == "error":
            self._shake()
        self._after(seconds, self.hide)

    def hide(self) -> None:
        self._go("hidden")

    # Building

    def _build(self) -> None:
        self._target = _Target.alloc().initWithOverlay_(self)
        self._panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _W, _H),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        # Above the menu bar, on every desktop and over full-screen apps; never takes the keyboard.
        self._panel.setLevel_(NSMainMenuWindowLevel + 3)
        self._panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(NSColor.clearColor())
        self._panel.setHasShadow_(False)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setReleasedWhenClosed_(False)
        # Clicks pass through to the menu bar except on the island itself (see _mouse_moved).
        self._panel.setIgnoresMouseEvents_(True)
        self._panel.setAcceptsMouseMovedEvents_(True)

        view = _IslandView.alloc().initWithFrame_(NSMakeRect(0, 0, _W, _H))
        view.overlay = self
        view.setWantsLayer_(True)
        view.setLayerUsesCoreImageFilters_(True)
        self._panel.setContentView_(view)

        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        # island holds the black shape and the content, which the same outline clips.
        self._island = CALayer.layer()
        self._island.setFrame_(((0, 0), (_W, _H)))
        view.layer().addSublayer_(self._island)
        self._shape = CAShapeLayer.layer()
        self._shape.setFrame_(((0, 0), (_W, _H)))
        self._shape.setFillColor_(_c(0, 0, 0))
        self._shape.setShadowColor_(_c(0, 0, 0))
        self._shape.setShadowRadius_(22)
        self._shape.setShadowOffset_((0, -8))
        self._shape.setShadowOpacity_(0)
        self._shape.setOpacity_(0)
        self._island.addSublayer_(self._shape)
        self._content = CALayer.layer()
        self._content.setFrame_(((0, 0), (_W, _H)))
        self._clip = CAShapeLayer.layer()
        self._clip.setFrame_(((0, 0), (_W, _H)))
        self._content.setMask_(self._clip)
        self._island.addSublayer_(self._content)
        self._parts = self._make_parts()
        for part in self._parts.values():
            blur = CIFilter.filterWithName_("CIGaussianBlur")
            blur.setName_("blur")
            blur.setValue_forKey_(0, "inputRadius")
            part.layer.setFilters_([blur])
            part.layer.setOpacity_(0)
            self._content.addSublayer_(part.layer)
        CATransaction.commit()

        self._place(_screen_with_mouse())
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self._apply_shape(*self._closed(), animate=False)
        CATransaction.commit()
        self._panel.orderFrontRegardless()
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self._target, "screensChanged:", NSApplicationDidChangeScreenParametersNotification, None
        )
        self._monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskMouseMoved, lambda event: self._mouse_moved()),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskMouseMoved, self._local_moved),
        ]

    def _make_parts(self) -> dict[str, _Part]:
        parts = {}

        # Ready: a breathing dot.
        dot = CALayer.layer()
        dot.setBounds_(((0, 0), (12, 12)))
        self._dot = _circle(8, _GREEN)
        self._dot.setPosition_((6, 6))
        self._dot.setShadowOpacity_(0.9)
        self._dot.setShadowRadius_(4)
        self._dot.setShadowOffset_((0, 0))
        breathe = CABasicAnimation.animationWithKeyPath_("opacity")
        breathe.setFromValue_(1.0)
        breathe.setToValue_(0.45)
        breathe.setDuration_(1.4)
        breathe.setAutoreverses_(True)
        breathe.setRepeatCount_(1e9)
        breathe.setTimingFunction_(_ease(kCAMediaTimingFunctionEaseInEaseOut))
        self._dot.addAnimation_forKey_(breathe, "breathe")
        dot.addSublayer_(self._dot)
        parts["dot"] = dot
        parts["status"] = _text(_SMALL, _c(1, 1, 1, 0.85))

        # Listening: an orb in a glow that follows the voice, and a meter.
        orb = CALayer.layer()
        orb.setBounds_(((0, 0), (30, 30)))
        self._glow = CAGradientLayer.layer()
        self._glow.setType_(kCAGradientLayerRadial)
        self._glow.setColors_([_c(1, 0.3, 0.4, 0.95), _c(1, 0.24, 0.33, 0.35), _c(1, 0.24, 0.33, 0)])
        self._glow.setLocations_([0.0, 0.45, 1.0])
        self._glow.setStartPoint_((0.5, 0.5))
        self._glow.setEndPoint_((1, 1))
        self._glow.setFrame_(((0, 0), (30, 30)))
        self._core = _circle(11, _RED)
        self._core.setPosition_((15, 15))
        self._core.setBorderColor_(_c(1, 0.62, 0.66, 0.9))
        self._core.setBorderWidth_(1)
        orb.addSublayer_(self._glow)
        orb.addSublayer_(self._core)
        parts["orb"] = orb

        meter = CAGradientLayer.layer()
        meter.setBounds_(((0, 0), (48, 22)))
        meter.setStartPoint_((0, 0.5))
        meter.setEndPoint_((1, 0.5))
        meter.setColors_([_c(1, 0.36, 0.55), _c(1, 0.5, 0.38), _c(1, 0.66, 0.25)])
        bars = CALayer.layer()
        bars.setFrame_(((0, 0), (48, 22)))
        self._bars = []
        for i in range(_BARS):
            bar = CALayer.layer()
            bar.setBackgroundColor_(_WHITE)
            bar.setCornerRadius_(1.5)
            bar.setFrame_(((i * 5.6, 9.5), (3, 3)))
            bars.addSublayer_(bar)
            self._bars.append(bar)
        meter.setMask_(bars)
        parts["bars"] = meter

        cancel = CALayer.layer()
        cancel.setBounds_(((0, 0), (24, 24)))
        ring = _circle(24, _c(1, 1, 1, 0.16))
        ring.setPosition_((12, 12))
        cross = _symbol("xmark", 9, NSFontWeightBold, [_WHITE])
        cross.setPosition_((12, 12))
        cancel.addSublayer_(ring)
        cancel.addSublayer_(cross)
        parts["cancel"] = cancel

        parts["hint"] = _text(_SMALL, _c(1, 1, 1, 0.55))
        parts["clock"] = _text(_CLOCK, _c(1, 1, 1, 0.55), kCAAlignmentRight)
        parts["caption"] = _text(_SMALL, _c(1, 1, 1, 0.78))
        parts["caption"].setWrapped_(True)
        parts["caption"].setTruncationMode_(kCATruncationNone)

        # Transcribing and polishing: an icon, three dots in a wave, a shimmering title.
        parts["wave"] = _symbol("waveform", 15, NSFontWeightSemibold, [_c(0.35, 0.88, 1), _c(0.45, 0.48, 1)])
        parts["spark"] = _symbol("sparkles", 15, NSFontWeightSemibold, [_c(0.72, 0.5, 1), _c(1, 0.45, 0.75)])
        dots = CALayer.layer()
        dots.setBounds_(((0, 0), (26, 12)))
        for i in range(3):
            one = _circle(5, _WHITE)
            one.setPosition_((2.5 + i * 10.5, 6))
            hop = CAKeyframeAnimation.animationWithKeyPath_("transform.translation.y")
            hop.setValues_([0.0, 3.5, 0.0, 0.0])
            hop.setKeyTimes_([0.0, 0.25, 0.5, 1.0])
            hop.setDuration_(1.0)
            hop.setRepeatCount_(1e9)
            hop.setTimeOffset_(1.0 - i * 0.14)
            one.addAnimation_forKey_(hop, "hop")
            dim = CAKeyframeAnimation.animationWithKeyPath_("opacity")
            dim.setValues_([0.4, 1.0, 0.4, 0.4])
            dim.setKeyTimes_([0.0, 0.25, 0.5, 1.0])
            dim.setDuration_(1.0)
            dim.setRepeatCount_(1e9)
            dim.setTimeOffset_(1.0 - i * 0.14)
            one.addAnimation_forKey_(dim, "dim")
            dots.addSublayer_(one)
        parts["dots"] = dots
        parts["title"] = _text(_TITLE, _WHITE)
        _shimmer(parts["title"], 0.45)
        chip = CALayer.layer()
        chip.setBackgroundColor_(_c(1, 1, 1, 0.1))
        chip.setCornerRadius_(9)
        self._chip_text = _text(_CHIP, _c(1, 1, 1, 0.62), kCAAlignmentCenter)
        chip.addSublayer_(self._chip_text)
        parts["chip"] = chip
        parts["words"] = _text(_BODY, _c(1, 1, 1, 0.9))
        parts["words"].setWrapped_(True)
        parts["words"].setTruncationMode_(kCATruncationNone)
        _shimmer(parts["words"], 0.62)

        # Done: a green disc, a check mark that draws itself, and a ring that pulses out.
        check = CALayer.layer()
        check.setBounds_(((0, 0), (22, 22)))
        self._ring = CAShapeLayer.layer()
        self._ring.setFrame_(((0, 0), (22, 22)))
        circle = CGPathCreateMutable()
        CGPathAddEllipseInRect(circle, None, ((0, 0), (22, 22)))
        self._ring.setPath_(circle)
        self._ring.setFillColor_(None)
        self._ring.setStrokeColor_(_GREEN)
        self._ring.setLineWidth_(1.5)
        self._ring.setOpacity_(0)
        self._disc = _circle(22, _GREEN)
        self._disc.setPosition_((11, 11))
        self._tick_mark = CAShapeLayer.layer()
        self._tick_mark.setFrame_(((0, 0), (22, 22)))
        mark = CGPathCreateMutable()
        CGPathMoveToPoint(mark, None, 6.5, 11.5)
        CGPathAddLineToPoint(mark, None, 9.6, 8.2)
        CGPathAddLineToPoint(mark, None, 15.6, 14.6)
        self._tick_mark.setPath_(mark)
        self._tick_mark.setFillColor_(None)
        self._tick_mark.setStrokeColor_(_WHITE)
        self._tick_mark.setLineWidth_(2.3)
        self._tick_mark.setLineCap_(kCALineCapRound)
        self._tick_mark.setLineJoin_(kCALineJoinRound)
        for layer in (self._ring, self._disc, self._tick_mark):
            check.addSublayer_(layer)
        parts["check"] = check
        parts["done"] = _text(_TITLE, _WHITE)

        # Messages.
        parts["icon"] = _symbol("info.circle.fill", 15, NSFontWeightSemibold, [_BLUE])
        parts["msg"] = _text(_TITLE, _WHITE)
        return {name: _Part(layer) for name, layer in parts.items()}

    # Where the island goes

    def _place(self, screen) -> None:
        """Puts the panel over this screen's notch, or at the top centre when it has none."""
        self._screen = screen
        frame = screen.frame()
        top_inset = screen.safeAreaInsets().top
        left = screen.auxiliaryTopLeftArea() if top_inset > 0 else None
        if left is not None and left.size.width > 0:
            right = screen.auxiliaryTopRightArea()
            self._gap = frame.size.width - left.size.width - right.size.width
            self._bar = top_inset
            self._top = 0
            centre = frame.origin.x + left.size.width + self._gap / 2
            self._floating = False
        else:
            self._gap, self._bar, self._top = _FLOAT_GAP, _FLOAT_BAR, _FLOAT_TOP
            centre = frame.origin.x + frame.size.width / 2
            self._floating = True
        self._panel.setFrame_display_(
            NSMakeRect(centre - _W / 2, frame.origin.y + frame.size.height - _H, _W, _H), False
        )
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        _scale_all(self._panel.contentView().layer(), screen.backingScaleFactor())
        CATransaction.commit()

    def _screens_changed(self) -> None:
        screens = list(NSScreen.screens())
        self._place(self._screen if self._screen in screens else _screen_with_mouse())
        self._render()

    def _closed(self) -> tuple[float, float, float, float]:
        """The island folded away: the notch itself, or a small pill on a screen without one."""
        if self._floating:
            return 120.0, self._bar, self._bar / 2, -self._bar / 2
        return self._gap, self._bar, 10.0, _SHOULDER

    # State changes

    def _go(self, state: str) -> None:
        if self._panel is None:
            self._build()
        was = self._state
        if was == "hidden" and state != "hidden":
            screen = _screen_with_mouse()
            if screen != self._screen:
                self._place(screen)
        self._state = state
        self._cancel_later()
        if state not in _ACTIVE:
            self._hover = False
            self._panel.setIgnoresMouseEvents_(True)
        self._render(opening=was == "hidden")
        typing = state == "polishing" and self.show_words and self._words
        if state == "listening" or typing:
            self._start_ticker()
        else:
            self._stop_ticker()

    def _render(self, opening: bool = False) -> None:
        if self._panel is None:
            return
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        width, height, radius, shoulder, place = self._layout()
        self._apply_shape(width, height, radius, shoulder)
        order = 0
        for name, part in self._parts.items():
            layer = part.layer
            if name in place:
                cx, cy, w, h = place[name]
                layer.setBounds_(((0, 0), (w, h)))
                if layer.mask() is not None and name in ("title", "words"):
                    layer.mask().setFrame_(((0, 0), (w, h)))
                if part.shown:
                    _spring(layer, "position", NSValue.valueWithPoint_((cx, cy)), 300, 26)
                else:
                    layer.removeAnimationForKey_("swap")
                    layer.setPosition_((cx, cy))
                    _reveal(layer, (0.1 if opening else 0.04) + order * 0.045)
                    order += 1
                    part.shown = True
            elif part.shown:
                _conceal(layer)
                part.shown = False
        CATransaction.commit()
        self._cancel_at = place["cancel"][:2] if "cancel" in place else None
        self._rect = NSMakeRect(_W / 2 - width / 2, _H - self._top - height, width, height)

    def _apply_shape(self, width: float, height: float, radius: float, shoulder: float, animate: bool = True) -> None:
        path = _island_path(_W / 2, _H - self._top, width, height, radius, shoulder)
        hidden = self._state == "hidden"
        if not animate:
            for layer in (self._shape, self._clip):
                layer.setPath_(path)
            self._shape.setShadowPath_(path)
            return
        stiffness, damping = (320, 32) if hidden else (260, 22)
        for layer, key in ((self._shape, "path"), (self._clip, "path"), (self._shape, "shadowPath")):
            _spring(layer, key, path, stiffness, damping)
        shadow = 0.55 if height > self._bar + 1 else 0.0
        _tween(self._shape, "shadowOpacity", _now_value(self._shape, "shadowOpacity"), shadow, 0.3)
        showing = _now_value(self._shape, "opacity")
        if hidden:
            # The notch is black too: fade out only once the island has folded back into it.
            _tween(self._shape, "opacity", showing, 0.0, 0.14, kCAMediaTimingFunctionEaseIn, delay=0.32)
        else:
            # Also replaces a fade-out still waiting to start.
            _tween(self._shape, "opacity", showing, 1.0, 0.12 if self._floating and showing < 1 else 0.01)

    # Layout: which parts show where, and how big the island is

    def _set_text(self, name: str, text: str) -> None:
        part = self._parts[name]
        if part.layer.string() != text:
            if part.shown:
                _swap(part.layer)
            part.layer.setString_(text)

    def _wrapped(self, name: str, text: str, font, width: float, lines: int) -> tuple[float, float]:
        layer = self._parts[name].layer
        layer.setWrapped_(True)
        self._set_text(name, text)
        w, h = _measure(text, font, width)
        return min(width, w), min(h, lines * _line(font))

    def _single(self, name: str, text: str, font) -> tuple[float, float]:
        layer = self._parts[name].layer
        layer.setWrapped_(False)
        self._set_text(name, text)
        return _measure(text, font)[0], _line(font)

    def _layout(self):
        state = self._state
        left = right = None  # (name, width, height)
        rows: list[list[tuple[str, float, float, str]]] = []
        min_width = 0.0
        if state == "peek":
            status, ok = self._status
            self._dot.setBackgroundColor_(_GREEN if ok else _AMBER)
            self._dot.setShadowColor_(_GREEN if ok else _AMBER)
            left = ("dot", 12, 12)
            if _measure(status, _SMALL)[0] <= 170:
                right = ("status", *self._single("status", status, _SMALL))
            else:
                rows = [[("status", *self._wrapped("status", status, _SMALL, 320, 2), "left")]]
        elif state == "listening":
            left = ("orb", 30, 30)
            right = ("cancel", 24, 24) if self._hover else ("bars", 48, 22)
            if self._caption:
                text = _tail(self._caption, _SMALL, 320, 2)
                rows.append([("caption", *self._wrapped("caption", text, _SMALL, 320, 2), "left")])
                min_width = 360
            if self._hint:
                rows.append([
                    ("hint", *self._single("hint", self._hint, _SMALL), "left"),
                    ("clock", 44, _line(_CLOCK), "right"),
                ])
                self._set_clock()
        elif state == "transcribing":
            left = ("wave", *self._size("wave"))
            right = ("cancel", 24, 24) if self._hover else ("dots", 26, 12)
            rows = [[("title", *self._single("title", "Transcribing", _TITLE), "left")]]
        elif state == "polishing":
            left = ("spark", *self._size("spark"))
            right = ("cancel", 24, 24) if self._hover else ("dots", 26, 12)
            header = [("title", *self._single("title", "Writing prompt" if self._prompt else "Polishing", _TITLE), "left")]
            if self._model:
                self._chip_text.setString_(self._model)
                chip_w = _measure(self._model, _CHIP)[0] + 14
                self._chip_text.setFrame_(((0, (18 - _line(_CHIP)) / 2), (chip_w, _line(_CHIP))))
                header.append(("chip", chip_w, 18, "right"))
            rows = [header]
            min_width = 300
            if self.show_words and self._words:
                width = _POLISH_WIDTH - 2 * _BODY_PAD
                full = _tail(self._words, _BODY, width, _WORD_LINES)
                height = min(_measure(full, _BODY, width)[1], _WORD_LINES * _line(_BODY))
                self._parts["words"].layer.setString_(self._typed_text(width))
                rows.append([("words", width, height, "left")])
                min_width = _POLISH_WIDTH
        elif state == "done":
            left = ("check", 22, 22)
            right = ("done", *self._single("done", self._done, _TITLE))
        elif state == "message":
            text, kind = self._message
            symbol, colors = _MESSAGE_LOOK[kind]
            _set_symbol(self._parts["icon"].layer, symbol, 15, NSFontWeightSemibold, colors)
            left = ("icon", *self._size("icon"))
            if _measure(text, _TITLE)[0] <= 190:
                right = ("msg", *self._single("msg", text, _TITLE))
            else:
                rows = [[("msg", *self._wrapped("msg", text, _TITLE, 330, 2), "left")]]

        if state == "hidden":
            width, height, radius, shoulder = self._closed()
            return width, height, radius, shoulder, {}

        ear = max(left[1] if left else 0, right[1] if right else 0) + 2 * _PAD
        width = self._gap + 2 * ear
        body = 0.0
        if rows:
            row_widths = [sum(item[1] for item in row) + 12 * (len(row) - 1) for row in rows]
            width = max(width, max(row_widths) + 2 * _BODY_PAD, min_width)
            body = 6 + sum(max(item[2] for item in row) for row in rows) + 8 * (len(rows) - 1) + 16
        width = min(width, _W - 40)
        height = self._bar + body
        if rows:
            radius = _OPEN_R
            shoulder = -18 if self._floating else _SHOULDER
        else:
            radius = self._bar / 2 if self._floating else _COMPACT_R
            shoulder = -self._bar / 2 if self._floating else _SHOULDER

        top = _H - self._top
        island_left, island_right = _W / 2 - width / 2, _W / 2 + width / 2
        middle = top - self._bar / 2
        place = {}
        if left:
            place[left[0]] = (island_left + _PAD + left[1] / 2, middle, left[1], left[2])
        if right:
            place[right[0]] = (island_right - _PAD - right[1] / 2, middle, right[1], right[2])
        y = top - self._bar - 6
        for row in rows:
            row_height = max(item[2] for item in row)
            x = island_left + _BODY_PAD
            for name, w, h, align in row:
                if align == "right":
                    cx = island_right - _BODY_PAD - w / 2
                else:
                    cx = x + w / 2
                    x += w + 12
                place[name] = (cx, y - row_height / 2, w, h)
            y -= row_height + 8
        return width, height, radius, shoulder, place

    def _size(self, name: str) -> tuple[float, float]:
        size = self._parts[name].layer.bounds().size
        return size.width, size.height

    # Per-frame work: the meter while listening, the typing while polishing

    def _start_ticker(self) -> None:
        if self._ticker is None:
            self._ticker = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / 60, self._target, "tick:", None, True
            )
            # Common modes, so the meter keeps moving while the menu bar menu is open.
            NSRunLoop.currentRunLoop().addTimer_forMode_(self._ticker, NSRunLoopCommonModes)

    def _stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.invalidate()
            self._ticker = None

    def _tick(self) -> None:
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        if self._state == "listening":
            self._meter()
        elif self._state == "polishing":
            self._type()
        CATransaction.commit()

    def _meter(self) -> None:
        raw = max(0.0, min(1.0, self._level()))
        # Quick to rise, slow to fall, as a VU meter is.
        self._lvl += (raw - self._lvl) * (0.45 if raw > self._lvl else 0.12)
        level, now = self._lvl, time.monotonic()
        self._glow.setValue_forKeyPath_(0.7 + 0.8 * level, "transform.scale")
        self._glow.setOpacity_(0.35 + 0.65 * level)
        self._core.setValue_forKeyPath_(1 + 0.3 * level, "transform.scale")
        for i, bar in enumerate(self._bars):
            # Each bar wobbles at its own rate, so the meter looks alive at a steady volume.
            wobble = 0.62 + 0.38 * math.sin(now * (6.5 + i * 1.3) + i * 1.7)
            target = max(0.06 + 0.04 * math.sin(now * 3 + i), level * _ENVELOPE[i] * wobble)
            old = self._bar_levels[i]
            self._bar_levels[i] = old + (target - old) * (0.5 if target > old else 0.2)
            height = 3 + self._bar_levels[i] * 19
            bar.setFrame_(((i * 5.6, (22 - height) / 2), (3, height)))
        if self._hint:
            self._set_clock()

    def _set_clock(self) -> None:
        seconds = int(time.monotonic() - self._started)
        text = f"{seconds // 60}:{seconds % 60:02d}"
        layer = self._parts["clock"].layer
        if layer.string() != text:
            layer.setString_(text)

    def _typed_text(self, width: float) -> str:
        return _tail(self._words[: int(self._typed)], _BODY, width, _WORD_LINES) if self._typed else ""

    def _type(self) -> None:
        if not (self.show_words and self._words) or self._typed >= len(self._words):
            self._stop_ticker()
            return
        self._typed = min(len(self._words), self._typed + _TYPE_RATE)
        self._parts["words"].layer.setString_(self._typed_text(_POLISH_WIDTH - 2 * _BODY_PAD))

    # Flourishes

    def _play_check(self) -> None:
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        if _calm():
            self._tick_mark.setStrokeEnd_(1)
        else:
            self._disc.setValue_forKeyPath_(0.3, "transform.scale")
            _spring(self._disc, "transform.scale", NSNumber.numberWithDouble_(1.0), 320, 14, 0.06)
            _tween(self._tick_mark, "strokeEnd", 0.0, 1.0, 0.32, delay=0.16)
            _tween(self._ring, "transform.scale", 1.0, 2.1, 0.7, delay=0.14)
            _tween(self._ring, "opacity", 0.7, 0.0, 0.7, delay=0.14)
        CATransaction.commit()

    def _shake(self) -> None:
        NSHapticFeedbackManager.defaultPerformer().performFeedbackPattern_performanceTime_(0, 0)
        if _calm():
            return
        shake = CAKeyframeAnimation.animationWithKeyPath_("transform.translation.x")
        shake.setValues_([0.0, -8.0, 7.0, -5.0, 4.0, -2.0, 0.0])
        shake.setDuration_(0.45)
        shake.setBeginTime_(CACurrentMediaTime() + 0.12)
        self._island.addAnimation_forKey_(shake, "shake")

    # Mouse: peek on hover, × to cancel

    def _local_moved(self, event):
        self._mouse_moved()
        return event

    def _mouse_moved(self) -> None:
        if self._panel is None:
            return
        mouse = NSEvent.mouseLocation()
        if self._state == "hidden" and not NSPointInRect(mouse, _with_top_edge(self._screen.frame())):
            # Nothing shows, so the island can move to the screen the user is on.
            screen = _screen_with_mouse()
            if screen != self._screen:
                self._place(screen)
            return
        origin = self._panel.frame().origin
        point = (mouse.x - origin.x, mouse.y - origin.y)
        if self._state == "hidden":
            # Only the notch itself (or a thin strip at the top centre) opens a peek.
            width = self._gap + 12 if not self._floating else 180
            # A mouse pushed against the top edge sits exactly on it, so the zone reaches past it.
            zone = NSMakeRect(_W / 2 - width / 2, _H - self._top - self._bar, width, self._bar + self._top + 2)
            if NSPointInRect(point, zone):
                self._go("peek")
            return
        rect = self._rect
        inside = NSPointInRect(point, NSMakeRect(rect.origin.x - 4, rect.origin.y - 4, rect.size.width + 8, rect.size.height + 8))
        if self._state == "peek":
            if inside:
                self._cancel_later()
            elif self._later is None:
                self._after(_PEEK_CLOSE_SECONDS, self.hide)
        if self._state in _ACTIVE and inside != self._hover:
            self._hover = inside
            self._render()
        self._panel.setIgnoresMouseEvents_(not (inside and self._state in _ACTIVE))

    def _click(self, point) -> None:
        if self._cancel_at is None:
            return
        x, y = self._cancel_at
        if abs(point.x - x) <= 16 and abs(point.y - y) <= 16:
            self._on_cancel()

    # Timers

    def _after(self, seconds: float, fn: Callable[[], None]) -> None:
        self._cancel_later()
        self._later_fn = fn
        self._later = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            seconds, self._target, "later:", None, False
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(self._later, NSRunLoopCommonModes)

    def _cancel_later(self) -> None:
        if self._later is not None:
            self._later.invalidate()
        self._later = self._later_fn = None

    def _fire_later(self) -> None:
        fn, self._later, self._later_fn = self._later_fn, None, None
        if fn:
            fn()
