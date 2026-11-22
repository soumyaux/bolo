"""The island's measurements, colors, text helpers and Core Animation helpers."""


import math

from AppKit import (
    NSAttributedString,
    NSEvent,
    NSFont,
    NSFontAttributeName,
    NSFontDescriptorSystemDesignRounded,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSImage,
    NSImageSymbolConfiguration,
    NSScreen,
    NSStringDrawingUsesLineFragmentOrigin,
    NSWorkspace,
)
from Foundation import (
    NSMakeRect,
    NSNumber,
    NSPointInRect,
)
from Quartz import (
    CABasicAnimation,
    CACurrentMediaTime,
    CAGradientLayer,
    CALayer,
    CAMediaTimingFunction,
    CASpringAnimation,
    CATextLayer,
    CATransition,
    CGColorCreateSRGB,
    CGPathAddCurveToPoint,
    CGPathAddLineToPoint,
    CGPathAddQuadCurveToPoint,
    CGPathCloseSubpath,
    CGPathCreateMutable,
    CGPathMoveToPoint,
    kCAAlignmentLeft,
    kCAFillModeBackwards,
    kCAGravityResizeAspect,
    kCAMediaTimingFunctionEaseIn,
    kCAMediaTimingFunctionEaseInEaseOut,
    kCAMediaTimingFunctionEaseOut,
    kCATransitionFade,
    kCATruncationEnd,
)

# The panel is this big and mostly transparent; the largest island fits inside it.
_W, _H = 640, 260
_PAD = 14  # between an ear's content and the island's side
_BODY_PAD = 18
_COMPACT_R, _OPEN_R = 12, 24  # bottom corner radius
_SHOULDER = 7  # the small inward curve where the notch meets the top edge
# A screen without a notch: the island hangs from the top edge as if it had a notch this wide.
_FLOAT_GAP = 36
_MESSAGE_SECONDS = 1.6
_DONE_SECONDS = 1.3
_PEEK_CLOSE_SECONDS = 0.3
# The meter: 9 bars, mirrored around the middle one. Each pair follows one pitch band of the voice
# (low in the middle, bright consonants at the edges) scaled by how loud it is, so it jumps up on
# a loud syllable, drops between words and moves differently as the voice changes.
_BARS = 9
_SHAPE = [1.0, 0.92, 0.8, 0.68, 0.56]  # by steps from the middle
_METER_W, _METER_H = 48, 20
_SAMPLES_PER_SECOND = 120
# How fast the bars follow the voice: up in about 25 ms, down in about 110 ms.
_RISE, _FALL = 0.025, 0.11
_TYPE_RATE = 95  # characters a second
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
_ACCENT = _c(0.12, 0.42, 1)
_GREEN = _c(0.2, 0.84, 0.42)
_AMBER = _c(1, 0.72, 0.2)
_BLUE = _c(0.32, 0.62, 1)
_SKY = _c(0.55, 0.75, 1)


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
    """The island's outline. s curves the top corners outward into the screen edge, as the notch
    does. Every outline has the same elements in the same order, so a spring can morph any one into
    any other."""
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
    "copy": ("doc.on.clipboard.fill", [_SKY, _BLUE]),
}
