import queue

import numpy as np

from bolo.app import BoloApp as App
from bolo.audio import Recorder


def _app():
    """Just the state _released() reads, without starting the menu bar app."""
    app = App.__new__(App)
    app._commands = queue.Queue()
    app._held, app._locked, app._lock_pending, app._lock_key = "fn", False, False, ""
    app._last_tap = app._down_at = 0.0
    return app


def _commands(app):
    return [app._commands.get_nowait() for _ in range(app._commands.qsize())]


def test_tap_then_hold_stops_when_the_key_goes_up():
    app = _app()
    app._lock_pending, app._down_at = True, 10.0
    app._released(12.0)
    assert not app._locked and _commands(app) == ["up"]


def test_double_tap_goes_hands_free():
    app = _app()
    app._lock_pending, app._down_at = True, 10.0
    app._released(10.1)
    assert app._locked and _commands(app) == ["lock"]


def test_a_single_tap_is_too_short():
    app = _app()
    app._down_at = 10.0
    app._released(10.1)
    assert not app._locked and _commands(app) == ["tap"]


def test_bands_follow_the_pitch():
    rec = Recorder.__new__(Recorder)
    rec.sample_rate, rec._band_floors = 16000, [None] * 5
    t = np.arange(256) / 16000
    quiet = (np.random.default_rng(0).normal(0, 20, 256)).astype(np.int16)[:, None]
    for _ in range(5):
        rec._spectrum(quiet, 256)
    low = (8000 * np.sin(2 * np.pi * 180 * t)).astype(np.int16)[:, None]
    high = (8000 * np.sin(2 * np.pi * 2000 * t)).astype(np.int16)[:, None]
    assert np.argmax(rec._spectrum(low, 256)) == 0
    assert np.argmax(rec._spectrum(high, 256)) == 3
