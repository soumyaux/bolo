"""Microphone capture held in memory, and FLAC encoding. Nothing is written to disk."""

import io
import subprocess
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf


class Recorder:
    """Records 16-bit mono audio between start() and stop()."""

    def __init__(self, sample_rate: int = 16000, max_seconds: float = 300):
        self.sample_rate = sample_rate
        self._max_frames = int(max_seconds * sample_rate)
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:
        with self._lock:
            if self._frames < self._max_frames:
                self._chunks.append(indata.copy())
                self._frames += frames

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._chunks = []
            self._frames = 0
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stops recording and returns the samples. The buffer is cleared."""
        stream, self._stream = self._stream, None
        if stream is None:
            return np.zeros(0, dtype=np.int16)
        stream.stop()
        stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
            self._frames = 0
        if not chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(chunks)[:, 0]


def mute_output() -> bool:
    """Mutes the Mac's speakers. Returns True if sound was on before, so the caller should undo it."""
    script = (
        "set wasMuted to output muted of (get volume settings)\n"
        "set volume output muted true\n"
        "return wasMuted"
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
    return result.returncode == 0 and result.stdout.strip() == "false"


def unmute_output() -> None:
    subprocess.run(["osascript", "-e", "set volume output muted false"], capture_output=True, timeout=3)


def seconds(audio: np.ndarray, sample_rate: int = 16000) -> float:
    return len(audio) / sample_rate


def peak_dbfs(audio: np.ndarray) -> float:
    """Loudest sample relative to full scale. Silence is about -90, speech is above -30."""
    if len(audio) == 0:
        return -96.0
    peak = int(np.abs(audio.astype(np.int32)).max())
    return 20 * np.log10(max(peak, 1) / 32768)


def to_flac(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="FLAC", subtype="PCM_16")
    return buf.getvalue()
