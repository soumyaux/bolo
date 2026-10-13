"""Reads and selects text in the focused field through the macOS Accessibility API.

It works in native text fields and fails in many browsers, Electron apps and terminals, so every
function returns None or False instead of raising. Password fields are never read.
"""

from typing import NamedTuple

from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateSystemWide,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
    AXValueCreate,
    kAXValueCFRangeType,
)
from CoreFoundation import CFRange

_SECURE = "AXSecureTextField"
# An app that is busy must not hold up dictation.
_TIMEOUT_SECONDS = 0.5


class Field(NamedTuple):
    element: object
    value: str


def _get(element, attribute: str):
    error, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return value if error == 0 else None


def focused() -> Field | None:
    """The text field that has the keyboard focus, with its text."""
    system = AXUIElementCreateSystemWide()
    AXUIElementSetMessagingTimeout(system, _TIMEOUT_SECONDS)
    element = _get(system, "AXFocusedUIElement")
    if element is None:
        return None
    AXUIElementSetMessagingTimeout(element, _TIMEOUT_SECONDS)
    if _SECURE in (_get(element, "AXRole"), _get(element, "AXSubrole")):
        return None
    text = value(element)
    return Field(element, text) if text is not None else None


def value(element) -> str | None:
    text = _get(element, "AXValue")
    return str(text) if isinstance(text, str) else None


def _utf16(text: str) -> int:
    """Length as macOS counts it; an emoji is two units there and one in Python."""
    return len(text.encode("utf-16-le")) // 2


def select(field: Field, start: int, length: int) -> bool:
    """Selects part of the field's text. True only when the app really selected that text."""
    wanted = field.value[start : start + length]
    span = CFRange(_utf16(field.value[:start]), _utf16(wanted))
    error = AXUIElementSetAttributeValue(
        field.element, "AXSelectedTextRange", AXValueCreate(kAXValueCFRangeType, span)
    )
    return error == 0 and _get(field.element, "AXSelectedText") == wanted
