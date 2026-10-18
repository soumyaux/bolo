"""Counts how Bolo is used, for a line in the menu. Only numbers are kept, never text."""

import datetime
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

PATH = Path.home() / "Library/Application Support/Bolo/usage.json"
_KEEP_DAYS = 30
# A fair typing speed, to say how much time dictating saved.
_TYPING_WPM = 40


def _day() -> str:
    return datetime.date.today().isoformat()


class Usage:
    def __init__(self, path: Path = PATH):
        self._path = path
        self._lock = threading.Lock()
        # {"local": {day: {name: number}}}
        self._days = {"local": {}}
        try:
            loaded = json.loads(path.read_text())
            self._days.update({k: v for k, v in loaded.items() if k in self._days and isinstance(v, dict)})
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("usage counts could not be read, starting again: %s", e)

    def add(self, name: str, amount: float = 1) -> None:
        with self._lock:
            today = self._days["local"].setdefault(_day(), {})
            today[name] = today.get(name, 0) + amount
            for old in sorted(self._days["local"])[:-_KEEP_DAYS]:
                del self._days["local"][old]
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        part = self._path.with_suffix(".tmp")
        part.write_text(json.dumps(self._days))
        part.chmod(0o600)
        os.replace(part, self._path)

    def today(self) -> dict:
        with self._lock:
            return dict(self._days["local"].get(_day(), {}))

    def line(self) -> str:
        """The menu line for today's dictation."""
        today = self.today()
        count, words = int(today.get("dictations", 0)), int(today.get("words", 0))
        spent = today.get("speaking_seconds", 0) + today.get("waiting_seconds", 0)
        saved = max(0.0, words / _TYPING_WPM * 60 - spent) / 60
        line = f"Today: {count} dictation{'s' * (count != 1)} · {words} words"
        if saved >= 1:
            line += f" · ~{saved:.0f} min saved"
        return line
