"""Whisper running on this Mac through MLX. Needs no network once the model is on disk."""

import io
import logging
import os
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf

from .base import BadResult, Unavailable

# The model is already downloaded; never contact Hugging Face while the app runs.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

log = logging.getLogger(__name__)

_WORD = re.compile(r"\w+")


def _broken(text: str, prompt: str) -> str:
    """Why a transcript is nonsense, or "" when it looks like real speech."""
    words = _WORD.findall(text.lower())
    if not words:
        return ""
    # Whisper stuck in a loop: "1 1 1 1 1" or the same phrase over and over.
    if len(text) >= 40 and len(text) / len(zlib.compress(text.encode())) > 2.4:
        return "repeats itself"
    if re.search(r"\b(\w+)(?:\W+\1\b){5}", text.lower()):
        return "repeats itself"
    # Whisper read the prompt back instead of hearing speech: four prompt words in the prompt's order.
    said = " " + " ".join(words) + " "
    listed = _WORD.findall(prompt.lower())
    if any(" " + " ".join(listed[i : i + 4]) + " " in said for i in range(len(listed) - 3)):
        return "repeats the glossary"
    return ""


class LocalWhisper:
    name = "local"

    def __init__(
        self,
        model: str = "mlx-community/whisper-large-v3-turbo",
        languages: list[str] | None = None,
        unload_after_idle_minutes: float = 10,
    ):
        self.model = model
        # One language is used as given. With several, each clip uses the one that fits best.
        self.languages = languages or ["en"]
        self._idle_seconds = unload_after_idle_minutes * 60
        self._loaded = False
        self.last_language = ""
        self._idle_timer: threading.Timer | None = None
        # MLX keeps its GPU state per thread, so loading and every transcription share one thread.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

    def warm(self) -> None:
        """Starts loading the model without waiting. Call when the key goes down."""
        self._pool.submit(self._load)

    def _load(self) -> None:
        if self._loaded:
            return
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper.transcribe import ModelHolder

        start = time.perf_counter()
        ModelHolder.get_model(self.model, mx.float16)
        # The first transcription after loading is slower, so spend it on one second of silence.
        mlx_whisper.transcribe(
            np.zeros(16000, dtype=np.float32),
            path_or_hf_repo=self.model,
            language=self.languages[0],
            verbose=None,
        )
        self._loaded = True
        self._rest_later()
        log.info("local model loaded in %.1f s", time.perf_counter() - start)

    def _unload(self) -> None:
        import mlx.core as mx
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.model = None
        ModelHolder.model_path = None
        mx.clear_cache()
        self._loaded = False
        log.info("local model unloaded after idle")

    def _rest_later(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        if self._idle_seconds > 0:
            self._idle_timer = threading.Timer(self._idle_seconds, lambda: self._pool.submit(self._unload))
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _pick_language(self, samples: np.ndarray) -> str:
        """Chooses among the configured languages only, so an accent is never taken for a third one."""
        if len(self.languages) == 1:
            return self.languages[0]
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
        from mlx_whisper.transcribe import ModelHolder

        model = ModelHolder.get_model(self.model, mx.float16)
        mel = log_mel_spectrogram(samples, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        _, probs = model.detect_language(pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16))
        first = self.languages[0]
        best = max(self.languages, key=lambda code: probs.get(code, 0.0))
        # An Indian accent can look a little like Hindi. Switch only when the other language clearly wins.
        return best if probs.get(best, 0.0) > 2 * probs.get(first, 0.0) else first

    def _run(self, flac: bytes, vocabulary: list[str] | None) -> str:
        self._load()
        samples, _ = sf.read(io.BytesIO(flac), dtype="float32")
        self.last_language = self._pick_language(samples)
        # Whisper treats the prompt as what was said just before, so it spells these words this way.
        # A plain list, without a label: a label like "Glossary:" was read back as speech.
        prompt = ", ".join(dict.fromkeys(vocabulary or [])) + "." if vocabulary else ""
        text = self._decode(samples, prompt)
        reason = _broken(text, prompt)
        if reason and prompt:
            # Try once more without the prompt, which is often what it got stuck on.
            text = self._decode(samples, "")
            reason = _broken(text, "")
        if reason:
            raise BadResult(f"{self.name}: transcript {reason}: {text[:60]!r}")
        return text

    def _decode(self, samples: np.ndarray, prompt: str) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self.model,
            language=self.last_language,
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
            verbose=None,
        )
        return result["text"].strip()

    def transcribe(self, flac: bytes, vocabulary: list[str] | None = None) -> str:
        try:
            return self._pool.submit(self._run, flac, vocabulary).result()
        except BadResult:
            raise
        except Exception as e:
            raise Unavailable(f"{self.name}: {type(e).__name__}: {e}") from e
        finally:
            self._rest_later()
