"""Reads and selects text in the focused field through the macOS Accessibility API.

It works in native text fields, and in Electron apps and Chromium browsers once open_up() has
asked them to expose their text. It fails in terminals and some web editors, so every function
returns None or False instead of raising. Password fields are never read.
"""

from typing import NamedTuple

from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
    AXValueCreate,
    kAXValueCFRangeType,
)
from CoreFoundation import CFEqual, CFRange, kCFBooleanTrue

_SECURE = "AXSecureTextField"
# An app that is busy must not hold up dictation.
_TIMEOUT_SECONDS = 0.5


# Chromium browsers build their text tree only when a screen reader asks with this attribute.
CHROMIUM = {
    "com.google.Chrome",
    "com.brave.Browser",
    "com.microsoft.edgemac",
    "company.thebrowser.Browser",
    "ai.perplexity.comet",
}
_opened: set[int] = set()


def open_up(pid: int, bundle_id: str) -> None:
    """Asks an app to expose its text fields, as screen readers do. Once per running app.

    Electron apps (Cursor, VS Code, Slack) answer to AXManualAccessibility; Chromium browsers to
    AXEnhancedUserInterface, which is set only for them because it slows window animations elsewhere.
    The app needs a moment to build the tree, so call this before the paste, not after.
    """
    if pid in _opened:
        return
    _opened.add(pid)
    app = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app, _TIMEOUT_SECONDS)
    AXUIElementSetAttributeValue(app, "AXManualAccessibility", kCFBooleanTrue)
    if bundle_id in CHROMIUM:
        AXUIElementSetAttributeValue(app, "AXEnhancedUserInterface", kCFBooleanTrue)


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
        # Electron apps (Cursor, VS Code) refuse the system-wide question (-25204) but answer
        # when asked directly.
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None:
            return None
        app = AXUIElementCreateApplication(front.processIdentifier())
        AXUIElementSetMessagingTimeout(app, _TIMEOUT_SECONDS)
        element = _get(app, "AXFocusedUIElement")
    if element is None:
        return None
    AXUIElementSetMessagingTimeout(element, _TIMEOUT_SECONDS)
    if _SECURE in (_get(element, "AXRole"), _get(element, "AXSubrole")):
        return None
    text = value(element)
    return Field(element, text) if text is not None else None


class Place(NamedTuple):
    """Where the user is: the front app and its focused window (None when it cannot be read)."""

    pid: int
    window: object


def place() -> Place:
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None:
        return Place(0, None)
    app = AXUIElementCreateApplication(front.processIdentifier())
    AXUIElementSetMessagingTimeout(app, _TIMEOUT_SECONDS)
    return Place(front.processIdentifier(), _get(app, "AXFocusedWindow"))


def same_place(a: Place, b: Place) -> bool:
    """True when both are the same app and, where both windows could be read, the same window.
    Tabs are not told apart: a browser keeps one window for all of them."""
    if a.pid != b.pid:
        return False
    return a.window is None or b.window is None or bool(CFEqual(a.window, b.window))


def page(pid: int) -> tuple[str, str]:
    """The web address and window title of the app's focused window, for choosing a writing
    style. Either is "" when it cannot be read. Accessibility only: window titles from the window
    server would need Screen Recording permission."""
    app = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app, _TIMEOUT_SECONDS)
    window = _get(app, "AXFocusedWindow")
    title = _get(window, "AXTitle") if window is not None else None
    url = ""
    # The page's address is on the web area that holds the focused field.
    element = _get(app, "AXFocusedUIElement")
    for _ in range(50):
        if element is None:
            break
        if _get(element, "AXRole") == "AXWebArea":
            address = _get(element, "AXURL")
            url = str(address.absoluteString() if hasattr(address, "absoluteString") else address or "")
            break
        element = _get(element, "AXParent")
    return url, str(title) if isinstance(title, str) else ""


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
