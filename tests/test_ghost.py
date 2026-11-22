import threading

import numpy as np

from bolo.ghost import LiveText, tail


class Recorder:
    take, sample_rate = 1, 16000

    def snapshot(self, take, start):
        t = np.arange(16000) / 16000
        speech = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
        return np.concatenate([np.zeros(16000, dtype=np.int16), speech, np.zeros(16000, dtype=np.int16)])


def test_tail_keeps_whole_words():
    assert tail("short  text") == "short text"
    end = tail("word " * 40, 20)
    assert end.startswith("…word") and len(end) <= 21


def test_live_words_survive_a_failure_and_stop():
    calls, got = [], threading.Event()

    class Growing(Recorder):
        def snapshot(self, take, start):
            return np.concatenate([super().snapshot(take, start)] * (len(calls) + 1))

    def transcribe(_flac):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("busy")
        return "hello"

    live = LiveText(Growing(), transcribe, 0, lambda text: got.set())
    assert got.wait(4)
    live.stop()
    live._thread.join(3)
    assert not live._thread.is_alive()
