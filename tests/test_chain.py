import pytest

from bolo.stt.base import RateLimited, Unavailable
from bolo.stt.chain import Chain


class Fake:
    def __init__(self, name, error=None):
        self.name, self.error, self.calls = name, error, 0

    def transcribe(self, flac, vocabulary):
        self.calls += 1
        if self.error:
            raise self.error
        return f"from {self.name}"


def transcribe(chain, monkeypatch):
    monkeypatch.setattr("bolo.stt.chain.flac_seconds", lambda _: 1.0)
    return chain.transcribe(b"", [])


def test_a_rate_limited_provider_falls_back_and_rests(monkeypatch):
    first, second = Fake("groq", RateLimited("busy")), Fake("local")
    chain = Chain([first, second])
    assert transcribe(chain, monkeypatch) == ("from local", "local")
    assert transcribe(chain, monkeypatch) == ("from local", "local")
    assert first.calls == 1


def test_all_failing_raises_unavailable(monkeypatch):
    with pytest.raises(Unavailable):
        transcribe(Chain([Fake("local", Unavailable("no model"))]), monkeypatch)
