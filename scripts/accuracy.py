"""Scores every speech model on your own saved dictations: word error rate and time per clip.

Each saved clip is labelled with the text you kept: your correction, or else what was pasted.
Every fifth clip is held out from voice training, and by default only those are scored, so a
trained model is never tested on what it learned from.

Run: uv run scripts/accuracy.py                 (held-out clips)
     uv run scripts/accuracy.py --all           (every saved clip)
     uv run scripts/accuracy.py --all --fixed   (only clips you corrected: the fairest score, since an
                                                 uncorrected label is just what one model pasted)
     add --parakeet to also score NVIDIA Parakeet (needs: uv run --with parakeet-mlx ...)
     add --vad to keep only the speech Silero VAD finds, with long pauses shortened
"""

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bolo import config
from bolo.learn import Learning
from bolo.learn.voice import TRAINED, Voice, errors, held_out
from bolo.stt.base import RateLimited, SttError
from bolo.stt.groq import GroqWhisper
from bolo.stt.local_whisper import LocalWhisper

STOCK = "mlx-community/whisper-large-v3-turbo"


class Parakeet:
    """NVIDIA Parakeet TDT through parakeet-mlx, English only, no vocabulary prompt. For comparison."""

    name = "parakeet"
    REPO = "mlx-community/parakeet-tdt-0.6b-v3"

    def __init__(self):
        self._model = None

    def transcribe(self, flac: bytes, vocabulary=None) -> str:
        import io

        import mlx.core as mx
        import soundfile as sf
        from parakeet_mlx import from_pretrained
        from parakeet_mlx.audio import get_logmel

        if self._model is None:
            self._model = from_pretrained(self.REPO)
            self.transcribe(flac)  # the first run compiles; keep it out of the timings
        samples, _ = sf.read(io.BytesIO(flac), dtype="float32")
        mel = get_logmel(mx.array(samples), self._model.preprocessor_config)
        return self._model.generate(mel)[0].text.strip()


def providers(cfg: dict, trained: dict[str, Path] | None = None) -> list:
    """Groq large-v3 (with a key), the stock local turbo, and trained models by name (by default
    the one scripts/train_voice.py made, if there is one)."""
    found = []
    if config.key("GROQ_API_KEY"):
        groq = GroqWhisper(config.key("GROQ_API_KEY"), cfg["stt"]["groq"]["model"], cfg["stt"]["groq"].get("language", ""))
        if groq.check():
            found.append(groq)
        else:
            print("groq is not reachable now; scoring the local models only")
    languages = cfg["stt"]["local"]["languages"]
    stock = LocalWhisper(STOCK, languages, 0)
    stock.name = "local turbo"
    found.append(stock)
    for name, path in (trained if trained is not None else {"trained": TRAINED}).items():
        if (path / "config.json").exists():
            model = LocalWhisper(str(path), languages, 0)
            model.name = name
            found.append(model)
    if "--parakeet" in sys.argv:
        found.append(Parakeet())
    return found


VAD = "--vad" in sys.argv


def _speech_only(flac: bytes) -> bytes:
    """Keeps only the speech Silero VAD finds; pauses become 0.6 s (the best of the settings tried).
    Needs: uv run --with silero-vad ..."""
    import io

    import numpy as np
    import soundfile as sf
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad

    from bolo import audio

    global _vad
    _vad = _vad or load_silero_vad()
    samples, rate = sf.read(io.BytesIO(flac), dtype="int16")
    stamps = get_speech_timestamps(torch.from_numpy(samples / 32768).float(), _vad, sampling_rate=rate, speech_pad_ms=300)
    gap = np.zeros(int(0.6 * rate), dtype=np.int16)
    parts = [part for stamp in stamps for part in (gap, samples[stamp["start"] : stamp["end"]])][1:]
    return audio.to_flac(np.concatenate(parts) if parts else samples[:0], rate)


_vad = None


def _hear(provider, flac: bytes, vocabulary: list[str]) -> str:
    while True:
        try:
            return provider.transcribe(flac, vocabulary)
        except RateLimited as e:
            wait = e.retry_after or 10
            print(f"  {provider.name} rate limited, waiting {wait:.0f} s")
            time.sleep(wait)


def evaluate(models: list, voice: Voice, rows, vocabulary: list[str], quiet: bool = False) -> dict[str, float]:
    """Scores each model on these clips and prints a scoreboard. Returns {model name: WER}.
    One model at a time, because the local models share one loaded model."""
    scores, seconds = {}, {}
    for provider in models:
        wrong = total = 0
        times = seconds[provider.name] = []
        for row in rows:
            flac = voice.path(row).read_bytes()
            if VAD:
                flac = _speech_only(flac)
            start = time.perf_counter()
            try:
                heard = _hear(provider, flac, vocabulary)
            except SttError as e:
                heard = ""
                print(f"  {provider.name} failed on clip {row['id']}: {str(e)[:100]}")
            times.append(time.perf_counter() - start)
            bad, count = errors(heard, row["label"])
            wrong, total = wrong + bad, total + count
            if not quiet and bad:
                print(f"  {provider.name} #{row['id']}: {bad} error(s)\n    heard: {heard}\n    label: {row['label']}")
        scores[provider.name] = wrong / max(total, 1)
    print(f"\n{'model':<14}{'clips':>6}{'WER':>8}{'median s':>10}{'mean s':>8}")
    for provider in models:
        print(f"{provider.name:<14}{len(rows):>6}{scores[provider.name]:>8.1%}", end="")
        times = seconds[provider.name] or [0]
        print(f"{statistics.median(times):>10.2f}{statistics.mean(times):>8.2f}")
    return scores


def main() -> None:
    cfg = config.load()
    learning = Learning(True)
    voice = Voice(learning.store, keep_days=cfg["learning"].get("voice_keep_days", 14))
    voice.prune()
    rows = [row for row in learning.store.with_audio() if "--all" in sys.argv or held_out(row)]
    if "--fixed" in sys.argv:
        rows = [row for row in rows if row["corrected"]]
    if not rows:
        sys.exit(f"No saved clips to score yet ({voice.line()}). Dictate with save_voice = true first.")
    minutes = sum(row["audio_seconds"] for row in rows) / 60
    print(f"Scoring {len(rows)} clip(s), {minutes:.1f} min of speech")
    vocabulary = learning.terms(cfg["dictionary"]["terms"]) + cfg["dictionary"].get("hints", [])
    evaluate(providers(cfg), voice, rows, vocabulary, quiet="--quiet" in sys.argv)


if __name__ == "__main__":
    main()
