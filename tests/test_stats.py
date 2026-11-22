import pytest

from bolo import config, stats


@pytest.fixture
def calls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    sent = []

    class Answer:
        status_code = 204

    monkeypatch.setattr(stats.httpx, "post", lambda url, json, timeout: sent.append(json) or Answer())
    return sent


def test_nothing_is_sent_without_a_yes(calls):
    stats.daily({})
    stats.daily({"stats": {"share_count": False}})
    assert calls == []


def test_sends_only_a_random_id_once_a_day(calls):
    on = {"stats": {"share_count": True}}
    stats.daily(on)
    stats.daily(on)
    assert len(calls) == 1 and list(calls[0]) == ["id"]
    assert stats._id_file().stat().st_mode & 0o777 == 0o600


def test_the_id_stays_the_same(calls):
    assert stats._random_id() == stats._random_id()
