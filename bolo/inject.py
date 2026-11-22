"""Pastes text into the focused app through the clipboard, then puts the old clipboard back."""

import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString
from ApplicationServices import AXIsProcessTrusted

_KEY_V = 9
# Tells clipboard managers not to keep the dictated text in their history.
_TRANSIENT_TYPE = "org.nspasteboard.TransientType"


class InjectError(Exception):
    pass


def can_paste() -> bool:
    """True when the launching app (VS Code or Terminal) has Accessibility permission."""
    return bool(AXIsProcessTrusted())


def _snapshot(pb) -> list[dict]:
    items = []
    for item in pb.pasteboardItems() or []:
        data = {}
        for kind in item.types():
            value = item.dataForType_(kind)
            if value is not None:
                data[kind] = value
        items.append(data)
    return items


def _restore(pb, items: list[dict]) -> None:
    pb.clearContents()
    restored = []
    for data in items:
        item = NSPasteboardItem.alloc().init()
        for kind, value in data.items():
            item.setData_forType_(value, kind)
        restored.append(item)
    if restored:
        pb.writeObjects_(restored)


def _press_cmd_v() -> None:
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, _KEY_V, down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


# A keyboard event carries at most 20 UTF-16 units of text.
_MAX_UNITS = 20
# The whole text appears within this time however long it is, so typing never feels slow.
_TYPE_SECONDS = 0.45
_STEP_SECONDS = 0.025


def _pieces(text: str) -> list[str]:
    """The text cut into words, each with the space after it, and long words cut to fit an event."""
    pieces = []
    for word in text.split(" "):
        word += " "
        while len(word.encode("utf-16-le")) // 2 > _MAX_UNITS:
            cut = _MAX_UNITS
            while len(word[:cut].encode("utf-16-le")) // 2 > _MAX_UNITS:
                cut -= 1
            pieces.append(word[:cut])
            word = word[cut:]
        pieces.append(word)
    pieces[-1] = pieces[-1][:-1]
    return [p for p in pieces if p]


def can_type(text: str) -> bool:
    """Typing suits one line of text. A new line would be a Return, which sends a chat message."""
    return bool(text) and "\n" not in text and "\r" not in text


def type_text(text: str) -> None:
    """Types text at the cursor word by word, so it appears as if written. No clipboard is used.
    Call only when can_type(text)."""
    if not can_paste():
        raise InjectError(
            "Bolo needs Accessibility: System Settings › Privacy & Security › Accessibility."
        )
    pieces = _pieces(text)
    step = min(_STEP_SECONDS, _TYPE_SECONDS / max(len(pieces), 1))
    for i, piece in enumerate(pieces):
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            # No modifier: a key still held (the talk key) must not turn the text into shortcuts.
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(piece.encode("utf-16-le")) // 2, piece)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        if i < len(pieces) - 1:
            time.sleep(step)


def copy(text: str) -> None:
    """Puts text on the clipboard and leaves it there."""
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def paste(text: str, restore_delay: float = 0.4) -> None:
    """Pastes text at the cursor. Blocks for restore_delay while the target app reads the clipboard."""
    if not text:
        return
    if not can_paste():
        raise InjectError(
            "Bolo needs Accessibility: System Settings › Privacy & Security › Accessibility."
        )
    pb = NSPasteboard.generalPasteboard()
    saved = _snapshot(pb)

    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    pb.setString_forType_("", _TRANSIENT_TYPE)
    ours = pb.changeCount()

    _press_cmd_v()
    time.sleep(restore_delay)

    # If something else was copied in the meantime, leave that in place.
    if pb.changeCount() == ours:
        _restore(pb, saved)
