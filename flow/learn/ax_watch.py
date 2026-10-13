"""Opt-in: after a paste, watches the text field to see how the user edits the dictated text.

Only the pasted stretch and its edit are kept, never the rest of the field. Where the field
cannot be read (see ax.py) nothing happens.
"""

import logging
import threading
import time
from collections.abc import Callable

from . import ax, diff

log = logging.getLogger(__name__)

_INTERVAL_SECONDS = 1.0
# A chat box is emptied on send; less overlap than this means the dictated text is gone.
_SAME_TEXT = 0.5


class Watcher:
    def __init__(self, on_edit: Callable[[int, str, str], None], seconds: float = 90):
        self._on_edit = on_edit
        self._seconds = seconds
        self._stop = threading.Event()
        self._dropped = threading.Event()

    def watch(self, dictation_id: int, pasted: str) -> None:
        """Call right after a paste. Reports through on_edit(id, pasted, edited) if it was edited."""
        self.finish()
        self._stop, self._dropped = threading.Event(), threading.Event()
        args = (dictation_id, pasted, self._stop, self._dropped)
        threading.Thread(target=self._follow, args=args, daemon=True).start()

    def finish(self) -> None:
        """Stops watching the last paste, for example because the next dictation starts."""
        self._stop.set()

    def drop(self) -> None:
        """Stops watching and reports nothing: the fix window is handling this correction."""
        self._dropped.set()
        self._stop.set()

    def _follow(
        self, dictation_id: int, pasted: str, stop: threading.Event, dropped: threading.Event
    ) -> None:
        # Give the app a moment to put the pasted text in the field.
        time.sleep(0.3)
        field = ax.focused()
        at = field.value.rfind(pasted) if field else -1
        if at < 0:
            return
        before, after = field.value[:at], field.value[at + len(pasted) :]
        edited = pasted
        deadline = time.monotonic() + self._seconds
        while time.monotonic() < deadline and not stop.wait(_INTERVAL_SECONDS):
            text = ax.value(field.element)
            # Typing outside the dictated text moves it but leaves both sides in place.
            if text is None or len(text) < len(before) + len(after):
                break
            if not (text.startswith(before) and text.endswith(after)):
                break
            now = text[len(before) : len(text) - len(after)]
            if diff.similarity(pasted, now) < _SAME_TEXT:
                break
            edited = now
        edited = diff.trim(pasted, edited)
        if edited != pasted and not dropped.is_set():
            self._on_edit(dictation_id, pasted, edited)
