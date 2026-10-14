"""Turns a long dictation into text piece by piece while the user is still speaking.

Whisper takes about 1 s for each 20 s of speech, so a 3-minute ramble would otherwise keep the
user waiting about 9 s after letting go. Pieces are cut at pauses, so no word is split, and only
the local model is used: the Gemini free tier allows too few requests for this.
"""

import logging
import threading

from . import audio

log = logging.getLogger(__name__)

# Never wait longer than this for a pause; Whisper hears at most 30 s at once.
_LONGEST_SECONDS = 28
# Pauses are looked for in this last stretch of a piece, so pieces stay near the wanted length.
_SEARCH_SECONDS = 6


class EarlyText:
    """Runs from key down to key up. start is the first sample to use, after the start sound."""

    def __init__(self, recorder: audio.Recorder, provider, vocabulary: list[str], start: int, seconds: float):
        self._recorder = recorder
        self._provider = provider
        self._vocabulary = vocabulary
        self._take = recorder.take
        self._rate = recorder.sample_rate
        self._piece = int(seconds * self._rate)
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
            at = audio.pause_at(samples, self._rate, after=max(0, len(samples) - _SEARCH_SECONDS * self._rate))
            if at is None:
                if len(samples) < _LONGEST_SECONDS * self._rate:
                    continue
                # No pause for half a minute: cut at the quietest moment of the last few seconds.
                at = audio.quietest(samples, self._rate, after=len(samples) - _SEARCH_SECONDS * self._rate)
            speech = audio.prepare(samples[:at], self._rate)
            if audio.seconds(speech, self._rate) >= 0.3:
                try:
                    text = self._provider.transcribe(audio.to_flac(speech, self._rate), self._vocabulary)
                except Exception as e:
                    # The whole clip is then turned into text the normal way, so nothing is lost.
                    log.warning("early transcription failed, the whole clip will be used: %s", str(e)[:120])
                    self.failed = True
                    return
                if text:
                    self.texts.append(text)
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
