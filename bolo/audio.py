"""Microphone capture held in memory, and FLAC encoding. Nothing is written to disk."""

import ctypes
import io
import logging
import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger(__name__)

# Opening or closing the microphone takes well under a second. Longer means Core Audio is stuck,
# usually on a Bluetooth headset that just went away; the caller is told instead of waiting forever.
_DEVICE_SECONDS = 4


class MicError(Exception):
    pass


def _within(seconds: float, work) -> None:
    """Runs work, raising MicError if it does not finish in time. A stuck call is left behind on
    its own thread, so the caller can go on."""
    errors: list[BaseException] = []

    def run():
        try:
            work()
        except BaseException as e:
            errors.append(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise MicError("Microphone not responding: reconnect it or restart Bolo")
    if errors:
        raise errors[0]


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]


# Core Audio's four-letter codes: 'dIn ', 'dOut', 'glob', 'outp' and 'mute'.
_DEFAULT_INPUT, _DEFAULT_OUTPUT = 0x64496E20, 0x644F7574
_GLOBAL, _OUTPUT, _MUTE = 0x676C6F62, 0x6F757470, 0x6D757465


def _core_audio():
    return ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")


def _default_device(selector: int) -> int:
    """Core Audio's id for the Mac's current input or output device, or 0 when it cannot be read."""
    try:
        address = _Address(selector, _GLOBAL, 0)
        device, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
        # kAudioObjectSystemObject is 1.
        error = _core_audio().AudioObjectGetPropertyData(
            1, ctypes.byref(address), 0, None, ctypes.byref(size), ctypes.byref(device)
        )
        return device.value if error == 0 else 0
    except OSError:
        return 0


def default_input_id() -> int:
    """Core Audio's id for the Mac's current input device, or 0 when it cannot be read. A headset
    that connects or reconnects gets a new id."""
    return _default_device(_DEFAULT_INPUT)


# The meter: speech starts this many dB above the room's noise and fills it this many dB higher.
_ABOVE_NOISE, _VOICE_RANGE = 6.0, 38.0
# How fast the noise estimate may rise, in dB a second, so speech is not taken for noise.
_FLOOR_RISE = 0.5

class Recorder:
    """Records 16-bit mono audio between start() and stop()."""

    def __init__(self, sample_rate: int = 16000, max_seconds: float = 300):
        self.sample_rate = sample_rate
        self._max_frames = int(max_seconds * sample_rate)
        self._chunks: list[np.ndarray] = []
        self._frames = 0
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        # Counts recordings, so a reader of an old one never gets samples from the next.
        self.take = 0
        # How far the voice is above the room's noise, from 0 to 1, for the on-screen meter.
        self.level = 0.0
        # The room's noise in dBFS: follows the quietest blocks, and creeps up slowly in between.
        self._floor: float | None = None
        # PortAudio reads the list of devices once. It must read it again after the Mac's input
        # changes (AirPods on or off), or it keeps opening a device that is gone and hears nothing.
        self._device = default_input_id()
        self._refresh = False
        self._callbacks = 0
        self._started_at = 0.0
        # True when the last stop() found the microphone had sent nothing at all.
        self.silent = False

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def frames(self) -> int:
        """Samples recorded so far in this clip."""
        with self._lock:
            return self._frames

    def _callback(self, indata, frames, time_info, status) -> None:
        self._callbacks += 1
        self.level = self._loudness(indata, frames)
        with self._lock:
            if self._frames < self._max_frames:
                self._chunks.append(indata.copy())
                self._frames += frames

    def _loudness(self, indata, frames: int) -> float:
        """The block's average loudness above the room's noise. Measured against the noise and not
        as a fixed level, so the meter means the same with any microphone and its gain: a soft voice
        lifts it a little, a loud one fills it. The average, not the peak, so a click does not."""
        if not frames:
            return 0.0
        rms = float(np.sqrt(np.mean(np.square(indata[:, 0].astype(np.float32)))))
        db = 20 * np.log10(max(rms, 1.0) / 32768)
        seconds = frames / self.sample_rate
        if self._floor is None or db < self._floor:
            self._floor = db
        else:
            self._floor = min(self._floor + _FLOOR_RISE * seconds, -30.0)
        return min(1.0, max(0.0, (db - self._floor - _ABOVE_NOISE) / _VOICE_RANGE))

    def start(self) -> None:
        if self._stream is not None:
            return
        self._floor = None
        with self._lock:
            self._chunks = []
            self._frames = 0
            self.take += 1
        device = default_input_id()
        if device != self._device:
            log.info("microphone changed, reading the device list again")
            self._device, self._refresh = device, True
        try:
            self._open()
        except Exception as e:
            # Once more with a fresh device list: the one PortAudio holds may be out of date.
            log.warning("microphone did not open (%s), trying again", e)
            self._refresh = True
            self._open()

    def _open(self) -> None:
        opened: list[sd.InputStream] = []
        gave_up = threading.Event()

        def work():
            if self._refresh:
                sd._terminate()
                sd._initialize()
                self._refresh = False
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
            stream.start()
            if gave_up.is_set():
                # Opened after the caller stopped waiting; it must not record into the next clip.
                stream.close()
            else:
                opened.append(stream)

        self._callbacks = 0
        self._started_at = time.monotonic()
        try:
            _within(_DEVICE_SECONDS, work)
        except MicError:
            gave_up.set()
            for stream in opened:
                stream.close()
            self._refresh = True
            raise
        self._stream = opened[0]

    def snapshot(self, take: int, start: int) -> np.ndarray:
        """The samples of recording take from start on, without stopping. Empty once it has ended."""
        with self._lock:
            if take != self.take or self._stream is None or not self._chunks:
                return np.zeros(0, dtype=np.int16)
            return np.concatenate(self._chunks)[start:, 0]

    def stop(self) -> np.ndarray:
        """Stops recording and returns the samples. The buffer is cleared."""
        stream, self._stream = self._stream, None
        if stream is None:
            return np.zeros(0, dtype=np.int16)
        self.silent = not self._callbacks and time.monotonic() - self._started_at > 0.5
        if self.silent:
            # The stream ran but the device sent nothing: it went away. Start fresh next time.
            log.warning("the microphone sent no audio; the device list will be read again")
            self._refresh = True

        def close():
            stream.stop()
            stream.close()

        try:
            _within(_DEVICE_SECONDS, close)
        except MicError:
            log.warning("the microphone did not close; leaving it and starting fresh next time")
            self._refresh = True
        self.level = 0.0
        with self._lock:
            chunks, self._chunks = self._chunks, []
            self._frames = 0
        if not chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(chunks)[:, 0]


def _set_mute(device: int, on: bool) -> bool:
    address = _Address(_MUTE, _OUTPUT, 0)
    value = ctypes.c_uint32(1 if on else 0)
    return _core_audio().AudioObjectSetPropertyData(device, ctypes.byref(address), 0, None, 4, ctypes.byref(value)) == 0


def mute_output() -> int:
    """Mutes the Mac's speakers through Core Audio, which answers at once (AppleScript could take
    seconds). Returns the device it muted, for unmute_output, or 0 when nothing was changed: sound
    was already off, or the device has no mute switch. Never raises."""
    try:
        device = _default_device(_DEFAULT_OUTPUT)
        if not device:
            return 0
        address = _Address(_MUTE, _OUTPUT, 0)
        muted, size = ctypes.c_uint32(0), ctypes.c_uint32(4)
        error = _core_audio().AudioObjectGetPropertyData(
            device, ctypes.byref(address), 0, None, ctypes.byref(size), ctypes.byref(muted)
        )
        if error:
            log.info("the speakers have no mute switch (Core Audio error %d); sound is left on", error)
            return 0
        if muted.value or not _set_mute(device, True):
            return 0
        return device
    except OSError as e:
        log.warning("speakers not muted: %s", e)
        return 0


def unmute_output(device: int) -> None:
    """Undoes mute_output on the device it muted, even if the Mac has since switched to another."""
    try:
        if device and not _set_mute(device, False):
            log.warning("speakers not unmuted")
    except OSError as e:
        log.warning("speakers not unmuted: %s", e)


def seconds(audio: np.ndarray, sample_rate: int = 16000) -> float:
    return len(audio) / sample_rate


def peak_dbfs(audio: np.ndarray) -> float:
    """Loudest sample relative to full scale. Silence is about -90, speech is above -30."""
    if len(audio) == 0:
        return -96.0
    peak = int(np.abs(audio.astype(np.int32)).max())
    return 20 * np.log10(max(peak, 1) / 32768)


def pause_at(audio: np.ndarray, sample_rate: int, after: int, pause: float = 0.3) -> int | None:
    """The middle of the last pause in speech at or after sample after, or None if there is none.

    A pause is a stretch of at least pause seconds where the level stays near the room's noise
    floor, so cutting there splits no word.
    """
    frame = sample_rate // 50  # 20 ms
    usable = len(audio) // frame * frame
    if usable <= after:
        return None
    rms = np.sqrt((audio[:usable].astype(np.float32).reshape(-1, frame) ** 2).mean(axis=1))
    quiet = rms < max(np.percentile(rms, 10) * 3, 150)
    need = int(pause * 50)
    run = 0
    for i in range(len(quiet) - 1, after // frame - 1, -1):
        run = run + 1 if quiet[i] else 0
        if run >= need:
            # Walk to the end of this quiet stretch, then cut in its middle.
            end = i + run
            while end < len(quiet) and quiet[end]:
                end += 1
            return (i + end) // 2 * frame
    return None


def quietest(audio: np.ndarray, sample_rate: int, after: int) -> int:
    """The start of the quietest 100 ms at or after sample after: the least bad place to cut."""
    frame = sample_rate // 10
    after = max(0, after) // frame * frame
    usable = (len(audio) - after) // frame * frame
    if usable <= 0:
        return len(audio)
    rms = (audio[after : after + usable].astype(np.float32).reshape(-1, frame) ** 2).mean(axis=1)
    return after + int(rms.argmin()) * frame


def cut(audio: np.ndarray, start: int, end: int) -> np.ndarray:
    """Removes samples start to end, such as Bolo's own start sound heard by the microphone.

    Cut, not silenced: a stretch of perfect silence would pull down the noise floor prepare()
    measures, and room noise would then count as speech.
    """
    if end <= start or start >= len(audio):
        return audio
    return np.concatenate([audio[:start], audio[end:]])


def prepare(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Cuts the silence before and after speech and brings quiet speech up to a steady level.

    Whisper invents text ("Thank you.", the glossary, repeated words) to fill long silences, and
    hears quiet speech less well. Returns no samples when nothing was said.
    """
    frame = sample_rate // 50  # 20 ms
    usable = len(audio) // frame * frame
    if usable == 0:
        return audio[:0]
    frames = audio[:usable].astype(np.float32).reshape(-1, frame)
    rms = np.sqrt((frames**2).mean(axis=1))
    # Speech is a frame well above the room's noise floor.
    voiced = np.flatnonzero(rms > max(np.percentile(rms, 10) * 4, 200))
    # Fewer than 0.1 s of speech is a cough, a click or room noise.
    if len(voiced) < 5:
        return audio[:0]
    pad = sample_rate // 4  # keep soft first and last sounds
    start = max(voiced[0] * frame - pad, 0)
    end = min((voiced[-1] + 1) * frame + pad, len(audio))
    clip = audio[start:end].astype(np.float32)
    peak = float(np.abs(clip).max()) or 1.0
    # Up to -1 dBFS, but never more than 20 dB of gain so room noise is not blown up.
    gain = min(29000 / peak, 10.0)
    return np.clip(clip * max(gain, 1.0), -32768, 32767).astype(np.int16)


def to_flac(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="FLAC", subtype="PCM_16")
    return buf.getvalue()
