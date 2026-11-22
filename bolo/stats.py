"""Anonymous analytics: about once an hour, one number, the words dictated since the last report.

Never sends words, audio, a name or any ID. The counter only adds the number to a public total.
"""

import json
import logging
import threading
from datetime import date

import httpx

from . import config, usage

log = logging.getLogger(__name__)
URL = "https://bolo-count.soumyasahoo.in/v1/words"
EVERY_SECONDS = 60 * 60
MAX_WORDS = 200_000  # the counter refuses more in one report
_lock = threading.Lock()


def _sent_file():
    return config.USER_DIR / "words_sent_through"


def _forget_old_id() -> None:
    """Removes what the earlier opt-in count left on this Mac."""
    for name in ("stats_id", "stats_day"):
        (config.USER_DIR / name).unlink(missing_ok=True)


def _sent() -> tuple[date, int | None] | None:
    """The last day reported and its words so far. No number (from 1.1.0) means that day is done."""
    try:
        parts = _sent_file().read_text().split()
        return date.fromisoformat(parts[0]), int(parts[1]) if len(parts) > 1 else None
    except (OSError, ValueError, IndexError):
        return None


def _mark_sent(day: date, words: int) -> None:
    _sent_file().parent.mkdir(parents=True, exist_ok=True)
    _sent_file().write_text(f"{day.isoformat()} {words}")


def _words_by_day() -> dict[date, int]:
    try:
        days = json.loads(usage.PATH.read_text()).get("local", {})
    except (OSError, ValueError, AttributeError):
        return {}
    found = {}
    for name, numbers in days.items():
        try:
            found[date.fromisoformat(name)] = max(0, int(numbers.get("words", 0)))
        except (ValueError, TypeError, AttributeError):
            continue
    return found


def report() -> None:
    """Sends the words not reported yet, if any. A failure is tried again at the next check."""
    with _lock:
        try:
            _forget_old_id()
            today = date.today()
            days = _words_by_day()
            sent = _sent()
            if sent is None:  # counting starts now, earlier words are not reported
                _mark_sent(today, days.get(today, 0))
                return
            last, last_words = sent
            if last > today:
                return
            new = sum(words for day, words in days.items() if last < day <= today)
            if last_words is not None:
                new += max(0, days.get(last, 0) - last_words)
            if new:
                response = httpx.post(URL, json={"words": min(new, MAX_WORDS)}, timeout=4)
                if response.status_code != 204:
                    return
            _mark_sent(today, days.get(today, 0))
        except (httpx.HTTPError, OSError) as e:
            log.info("analytics not sent: %s", type(e).__name__)
