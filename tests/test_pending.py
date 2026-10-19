import time

from bolo.pending import Pending, Saved


def test_backoff_doubles_up_to_fifteen_minutes():
    now = 1_000_000.0
    saved = Saved(id="x", created=now, app="")
    assert not saved.due(now + 29)
    assert saved.due(now + 30)
    saved.tries, saved.last_try = 2, now
    assert not saved.due(now + 59) and saved.due(now + 60)
    saved.tries = 20
    assert saved.due(now + 900) and not saved.due(now + 899)


def test_a_ready_dictation_is_never_due():
    assert not Saved(id="x", created=0, app="", text="hi").due(10**9)


def test_audio_is_private_and_deleted_once_transcribed(tmp_path):
    pending = Pending(tmp_path / "p")
    saved = pending.add_audio(b"flac", "Notes")
    assert pending.audio_path(saved.id).stat().st_mode & 0o777 == 0o600
    pending.tried(saved, "hello", "hello")
    assert not pending.audio_path(saved.id).exists()
    assert [s.text for s in pending.all()] == ["hello"]


def test_old_dictations_expire(tmp_path):
    pending = Pending(tmp_path / "p", keep_hours=1)
    saved = pending.add_text("hi", "hi", "")
    saved.created = time.time() - 7200
    pending.tried(saved)
    assert pending.all() == []
