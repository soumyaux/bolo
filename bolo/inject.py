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
