"""The anonymous user count, only for people who said yes.

Sends one random ID, at most once a day. No name, IP is not stored, and nothing about what was said.
The ID is made on this Mac and means nothing outside this count.
"""

import logging
import os
import uuid
from datetime import UTC, datetime

import httpx

from . import config

log = logging.getLogger(__name__)
URL = "https://bolo-count.soumyasahoo.in/v1/seen"


def _id_file():
    return config.USER_DIR / "stats_id"


def _day_file():
    return config.USER_DIR / "stats_day"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _random_id() -> str:
    path = _id_file()
    try:
        value = path.read_text().strip()
        if str(uuid.UUID(value, version=4)) == value:
            return value
    except (OSError, ValueError):
        pass
    value = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(value)
    return value


def send() -> bool:
    """Counts this Mac for today. A failure is ignored and tried again at the next check."""
    try:
        response = httpx.post(URL, json={"id": _random_id()}, timeout=4)
        if response.status_code != 204:
            return False
        _day_file().write_text(_today())
        return True
    except (httpx.HTTPError, OSError) as e:
        log.info("user count not sent: %s", type(e).__name__)
        return False


def daily(cfg: dict) -> None:
    """Sends the count once a day, and only when the user said yes."""
    if not cfg.get("stats", {}).get("share_count", False):
        return
    try:
        if _day_file().read_text().strip() == _today():
            return
    except OSError:
        pass
    send()
