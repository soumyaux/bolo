"""Audio handling on made-up clips, so no recordings are needed."""

import numpy as np

from bolo import audio
from bolo.stt.base import flac_seconds

RATE = 16000


def tone(seconds: float, level: int) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (np.sin(2 * np.pi * 220 * t) * level).astype(np.int16)


def quiet(seconds: float) -> np.ndarray:
    return np.random.default_rng(1).integers(-5, 6, int(seconds * RATE)).astype(np.int16)


def test_silence_has_nothing_to_send():
    assert len(audio.prepare(quiet(2))) == 0


def test_a_click_is_not_speech():
    clip = np.concatenate([quiet(1), tone(0.04, 12000), quiet(1)])
    assert len(audio.prepare(clip)) == 0


def test_silence_around_speech_is_cut_with_a_small_margin():
    clip = np.concatenate([quiet(2), tone(1, 8000), quiet(2)])
    kept = audio.seconds(audio.prepare(clip))
    assert 1.0 <= kept <= 1.6


def test_quiet_speech_is_raised_but_never_clips():
    clip = np.concatenate([quiet(1), tone(1, 4000), quiet(1)])
    peak = np.abs(audio.prepare(clip).astype(np.int32)).max()
    assert 28000 <= peak <= 29000


def test_flac_keeps_the_length():
    assert abs(flac_seconds(audio.to_flac(tone(1.5, 8000))) - 1.5) < 0.01
