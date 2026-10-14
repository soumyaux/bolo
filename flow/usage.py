"""Counts how Bolo is used, for a line in the menu. Only numbers are kept, never text."""

import datetime
import json
import logging
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PATH = Path.home() / "Library/Application Support/Bolo/usage.json"
_KEEP_DAYS = 30
# A fair typing speed, to say how much time dictating saved.
_TYPING_WPM = 40
# gemini-3.5-transcribe answered "limit: 25 requests per day on Free Tier" for this account.
SPEECH_LIMIT = 25
# Gemini's daily limits reset at midnight Pacific time, not at the user's midnight.
_PACIFIC = ZoneInfo("America/Los_Angeles")


def _day(pacific: bool = False) -> str:
    return datetime.datetime.now(_PACIFIC if pacific else None).date().isoformat()


def resets_at() -> str:
    """The local time when Gemini's daily limits next reset, such as "12:30"."""
    now = datetime.datetime.now(_PACIFIC)
    midnight = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(), _PACIFIC)
    return midnight.astimezone().strftime("%H:%M")


class Usage:
    def __init__(self, path: Path = PATH):
        self._path = path
        self._lock = threading.Lock()
        # {"local": {day: {name: number}}, "pacific": {...}}
        self._days = {"local": {}, "pacific": {}}
        try:
            loaded = json.loads(path.read_text())
            self._days.update({k: v for k, v in loaded.items() if k in self._days and isinstance(v, dict)})
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("usage counts could not be read, starting again: %s", e)

    def add(self, name: str, amount: float = 1, pacific: bool = False) -> None:
        clock = "pacific" if pacific else "local"
        with self._lock:
            today = self._days[clock].setdefault(_day(pacific), {})
            today[name] = today.get(name, 0) + amount
            for old in sorted(self._days[clock])[:-_KEEP_DAYS]:
                del self._days[clock][old]
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        part = self._path.with_suffix(".tmp")
        part.write_text(json.dumps(self._days))
        part.chmod(0o600)
        os.replace(part, self._path)

    def today(self, pacific: bool = False) -> dict:
        with self._lock:
            return dict(self._days["pacific" if pacific else "local"].get(_day(pacific), {}))

    def lines(self) -> tuple[str, str]:
        """Two lines for the menu: today's dictation, and Gemini requests since midnight Pacific."""
        today, gemini = self.today(), self.today(pacific=True)
        count, words = int(today.get("dictations", 0)), int(today.get("words", 0))
        spent = today.get("speaking_seconds", 0) + today.get("waiting_seconds", 0)
        saved = max(0.0, words / _TYPING_WPM * 60 - spent) / 60
        first = f"Today: {count} dictation{'s' * (count != 1)} · {words} words"
        if saved >= 1:
            first += f" · ~{saved:.0f} min saved"
        speech = int(gemini.get("gemini_speech", 0))
        second = f"Gemini today: speech {speech}/{SPEECH_LIMIT} · cleanup {int(gemini.get('gemini_cleanup', 0))}"
        if speech >= SPEECH_LIMIT:
            second += f" (used up until {resets_at()})"
        elif speech >= SPEECH_LIMIT - 5:
            second += " (almost used up)"
        return first, second
