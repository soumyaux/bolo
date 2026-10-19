"""Scores every speech model on your own saved dictations: word error rate and time per clip.

Each saved clip is labelled with the text you kept: your correction, or else what was pasted.
Every fifth clip is held out from voice training, and by default only those are scored, so a
trained model is never tested on what it learned from.

Run: uv run scripts/accuracy.py           (held-out clips)
     uv run scripts/accuracy.py --all     (every saved clip)
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
    return found


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
    if not rows:
        sys.exit(f"No saved clips to score yet ({voice.line()}). Dictate with save_voice = true first.")
    minutes = sum(row["audio_seconds"] for row in rows) / 60
    print(f"Scoring {len(rows)} clip(s), {minutes:.1f} min of speech")
    vocabulary = learning.terms(cfg["dictionary"]["terms"]) + cfg["dictionary"].get("hints", [])
    evaluate(providers(cfg), voice, rows, vocabulary, quiet="--quiet" in sys.argv)


if __name__ == "__main__":
    main()
