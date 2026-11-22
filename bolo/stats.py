"""Anonymous analytics: one number a day, the words dictated on finished days.

Never sends words, audio, a name or any ID. The counter only adds the number to a public total.
"""

import json
import logging
import threading
from datetime import date, timedelta

import httpx

from . import config, usage

log = logging.getLogger(__name__)
URL = "https://bolo-count.soumyasahoo.in/v1/words"
MAX_WORDS = 200_000  # the counter refuses more in one report
_lock = threading.Lock()


def _sent_file():
    return config.USER_DIR / "words_sent_through"


def _forget_old_id() -> None:
    """Removes what the earlier opt-in count left on this Mac."""
    for name in ("stats_id", "stats_day"):
        (config.USER_DIR / name).unlink(missing_ok=True)


def _sent_through() -> date | None:
    try:
        return date.fromisoformat(_sent_file().read_text().strip())
    except (OSError, ValueError):
        return None


def _mark_sent(day: date) -> None:
    _sent_file().parent.mkdir(parents=True, exist_ok=True)
    _sent_file().write_text(day.isoformat())


def _words_between(after: date, through: date) -> int:
    try:
        days = json.loads(usage.PATH.read_text()).get("local", {})
    except (OSError, ValueError, AttributeError):
        return 0
    total = 0
    for name, numbers in days.items():
        try:
            if after < date.fromisoformat(name) <= through:
                total += max(0, int(numbers.get("words", 0)))
        except (ValueError, TypeError, AttributeError):
            continue
    return total


def daily() -> None:
    """Reports finished days once. A failure is ignored and tried again at the next check."""
    with _lock:
        try:
            _forget_old_id()
            yesterday = date.today() - timedelta(days=1)
            sent = _sent_through()
            if sent is None:  # counting starts now, earlier days are not reported
                _mark_sent(yesterday)
                return
            if sent >= yesterday:
                return
            words = min(_words_between(sent, yesterday), MAX_WORDS)
            if words:
                response = httpx.post(URL, json={"words": words}, timeout=4)
                if response.status_code != 204:
                    return
            _mark_sent(yesterday)
        except (httpx.HTTPError, OSError) as e:
            log.info("analytics not sent: %s", type(e).__name__)
