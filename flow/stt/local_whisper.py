"""Whisper running on this Mac through MLX. Needs no network once the model is on disk."""

import io
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf

from .base import Unavailable

# The model is already downloaded; never contact Hugging Face while the app runs.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

log = logging.getLogger(__name__)


class LocalWhisper:
    name = "local"

    def __init__(
        self,
        model: str = "mlx-community/whisper-large-v3-turbo",
        language: str = "en",
        unload_after_idle_minutes: float = 10,
    ):
        self.model = model
        self.language = language or None
        self._idle_seconds = unload_after_idle_minutes * 60
        self._loaded = False
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
            language=self.language,
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

    def _run(self, flac: bytes, vocabulary: list[str] | None) -> str:
        import mlx_whisper

        self._load()
        samples, _ = sf.read(io.BytesIO(flac), dtype="float32")
        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self.model,
            language=self.language,
            # Whisper spells words the way they appear in this text.
            initial_prompt="Glossary: " + ", ".join(vocabulary) + "." if vocabulary else None,
            condition_on_previous_text=False,
            verbose=None,
        )
        return result["text"].strip()

    def transcribe(self, flac: bytes, vocabulary: list[str] | None = None) -> str:
        try:
            return self._pool.submit(self._run, flac, vocabulary).result()
        except Exception as e:
            raise Unavailable(f"{self.name}: {type(e).__name__}: {e}") from e
        finally:
            self._rest_later()
