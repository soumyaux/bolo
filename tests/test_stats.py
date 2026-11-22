import json
from datetime import date, timedelta

import pytest

from bolo import config, setup, stats, usage

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
EARLIER = TODAY - timedelta(days=2)


@pytest.fixture
def calls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    monkeypatch.setattr(usage, "PATH", tmp_path / "usage.json")
    sent = []

    class Answer:
        status_code = 204

    monkeypatch.setattr(stats.httpx, "post", lambda url, json, timeout: sent.append(json) or Answer())
    return sent


def words(**days):
    usage.PATH.write_text(json.dumps({"local": {k: {"words": v, "dictations": 3} for k, v in days.items()}}))


def test_first_run_sends_nothing_and_skips_old_days(calls):
    words(**{YESTERDAY.isoformat(): 50})
    stats.daily()
    assert calls == []
    assert stats._sent_through() == YESTERDAY


def test_sends_only_one_number_for_finished_days_once(calls):
    stats._mark_sent(EARLIER - timedelta(days=1))
    words(**{EARLIER.isoformat(): 10, YESTERDAY.isoformat(): 20, TODAY.isoformat(): 99})
    stats.daily()
    stats.daily()
    assert calls == [{"words": 30}]


def test_a_failed_send_is_tried_again(calls, monkeypatch):
    stats._mark_sent(EARLIER)
    words(**{YESTERDAY.isoformat(): 7})

    def down(*args, **kwargs):
        raise stats.httpx.ConnectError("down")

    monkeypatch.setattr(stats.httpx, "post", down)
    stats.daily()
    assert stats._sent_through() == EARLIER


def test_a_day_without_words_sends_nothing(calls):
    stats._mark_sent(EARLIER)
    stats.daily()
    assert calls == [] and stats._sent_through() == YESTERDAY


def test_the_old_id_is_deleted(calls):
    (config.USER_DIR / "stats_id").write_text("old")
    (config.USER_DIR / "stats_day").write_text("old")
    stats.daily()
    assert not (config.USER_DIR / "stats_id").exists()
    assert not (config.USER_DIR / "stats_day").exists()


def test_the_report_is_capped(calls):
    stats._mark_sent(EARLIER)
    words(**{YESTERDAY.isoformat(): stats.MAX_WORDS + 1})
    stats.daily()
    assert calls == [{"words": stats.MAX_WORDS}]


def test_setup_has_no_count_step():
    assert "count-me" not in setup.__doc__
