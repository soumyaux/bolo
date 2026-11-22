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


def words(days):
    usage.PATH.write_text(json.dumps({"local": {k.isoformat(): {"words": v, "dictations": 3} for k, v in days.items()}}))


def test_first_run_sends_nothing_and_skips_earlier_words(calls):
    words({YESTERDAY: 50, TODAY: 5})
    stats.report()
    assert calls == []
    assert stats._sent() == (TODAY, 5)


def test_todays_new_words_are_sent_once(calls):
    stats._mark_sent(TODAY, 10)
    words({TODAY: 25})
    stats.report()
    stats.report()
    assert calls == [{"words": 15}]


def test_the_rest_of_an_earlier_day_is_included(calls):
    stats._mark_sent(EARLIER, 10)
    words({EARLIER: 12, YESTERDAY: 20, TODAY: 3})
    stats.report()
    assert calls == [{"words": 25}]


def test_a_marker_from_1_1_0_does_not_count_that_day_again(calls):
    stats._sent_file().write_text(YESTERDAY.isoformat())
    words({YESTERDAY: 40, TODAY: 6})
    stats.report()
    assert calls == [{"words": 6}]


def test_a_failed_send_is_tried_again(calls, monkeypatch):
    stats._mark_sent(TODAY, 1)
    words({TODAY: 8})

    def down(*args, **kwargs):
        raise stats.httpx.ConnectError("down")

    monkeypatch.setattr(stats.httpx, "post", down)
    stats.report()
    assert stats._sent() == (TODAY, 1)


def test_no_new_words_sends_nothing(calls):
    stats._mark_sent(EARLIER, 0)
    stats.report()
    assert calls == [] and stats._sent() == (TODAY, 0)


def test_the_old_id_is_deleted(calls):
    (config.USER_DIR / "stats_id").write_text("old")
    (config.USER_DIR / "stats_day").write_text("old")
    stats.report()
    assert not (config.USER_DIR / "stats_id").exists()
    assert not (config.USER_DIR / "stats_day").exists()


def test_the_report_is_capped(calls):
    stats._mark_sent(TODAY, 0)
    words({TODAY: stats.MAX_WORDS + 1})
    stats.report()
    assert calls == [{"words": stats.MAX_WORDS}]


def test_setup_has_no_count_step():
    assert "count-me" not in setup.__doc__
