"""Turns a long dictation into text piece by piece while the user is still speaking.

Whisper takes about 1 s for each 20 s of speech, so a 3-minute ramble would otherwise keep the
user waiting about 9 s after letting go. Pieces are cut at pauses, so no word is split.
"""

import logging
import threading
from collections.abc import Callable

from . import audio

log = logging.getLogger(__name__)

# Never wait longer than this for a pause; Whisper hears at most 30 s at once.
_LONGEST_SECONDS = 28
# Pauses are looked for in this last stretch of a piece (at most its second half), so pieces stay
# near the wanted length.
_SEARCH_SECONDS = 6


class EarlyText:
    """Runs from key down to key up. start is the first sample to use, after the start sound.
    transcribe takes (FLAC, vocabulary) and returns (text, provider name), as Chain.transcribe does.
    on_text gets the text so far each time a piece is done."""

    def __init__(
        self,
        recorder: audio.Recorder,
        transcribe: Callable[[bytes, list[str]], tuple[str, str]],
        vocabulary: list[str],
        start: int,
        seconds: float,
        on_text: Callable[[str], None] | None = None,
    ):
        self._recorder = recorder
        self._transcribe = transcribe
        self._vocabulary = vocabulary
        self._on_text = on_text
        self._take = recorder.take
        self._rate = recorder.sample_rate
        self._piece = int(seconds * self._rate)
        self._search = int(min(_SEARCH_SECONDS, seconds / 2) * self._rate)
        self.done = start
        self.texts: list[str] = []
        self.failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            samples = self._recorder.snapshot(self._take, self.done)
            if len(samples) < self._piece:
                continue
            # Cut at the first pause after a full piece: Groq bills a shorter piece as 10 s anyway.
            at = audio.pause_at(samples, self._rate, after=max(self._piece, len(samples) - self._search))
            if at is None:
                if len(samples) < _LONGEST_SECONDS * self._rate:
                    continue
                # No pause for half a minute: cut at the quietest moment of the last few seconds.
                at = audio.quietest(samples, self._rate, after=len(samples) - self._search)
            speech = audio.prepare(samples[:at], self._rate)
            if audio.seconds(speech, self._rate) >= 0.3:
                try:
                    text, _ = self._transcribe(audio.to_flac(speech, self._rate), self._vocabulary)
                except Exception as e:
                    # The whole clip is then turned into text the normal way, so nothing is lost.
                    log.warning("early transcription failed, the whole clip will be used: %s", str(e)[:120])
                    self.failed = True
                    return
                if text:
                    self.texts.append(text)
                    if self._on_text:
                        self._on_text(" ".join(self.texts))
            self.done += at

    def stop(self) -> None:
        """Stops taking new pieces. Call before the recorder stops."""
        self._stop.set()

    def finish(self) -> tuple[str, int] | None:
        """Waits for the piece in progress. Returns (text so far, first sample not yet heard), or
        None when nothing was done early or something failed."""
        self._stop.set()
        self._thread.join()
        if self.failed or not self.texts:
            return None
        return " ".join(self.texts), self.done
