"""Speak your real prompts and see every speech provider's result side by side.

Nothing is saved: each clip is held in memory, sent to each provider, printed and dropped.

Run: uv run scripts/bakeoff.py            (all providers in config.toml)
     uv run scripts/bakeoff.py --local    (local model only, no internet)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow import audio, config
from flow.app import build_providers
from flow.cleanup import Cleaner
from flow.learn import Learning
from flow.stt.base import SttError


def main() -> None:
    cfg = config.load()
    if "--local" in sys.argv:
        cfg["stt"]["order"] = ["local"]
    providers = build_providers(cfg)
    cleaner = Cleaner(cfg)
    learning = Learning(cfg["learning"]["enabled"])
    terms = learning.terms(cfg["dictionary"]["terms"])
    recorder = audio.Recorder(cfg["audio"]["sample_rate"], cfg["audio"]["max_seconds"])

    print("Loading...")
    recorder.start()
    recorder.stop()
    for provider in providers:
        if hasattr(provider, "warm"):
            provider.warm()
    cleaner.warm(prompt=True)

    number = 0
    while True:
        if input(f"\nPrompt {number + 1}: press Enter and speak (q then Enter to finish) ").strip() == "q":
            break
        recorder.start()
        input("  recording... press Enter when done ")
        clip = recorder.stop()
        length = audio.seconds(clip, recorder.sample_rate)
        if length < cfg["audio"]["min_seconds"]:
            print("  nothing heard")
            continue
        number += 1
        flac = audio.to_flac(clip, recorder.sample_rate)
        print(f"  {length:.1f} s, peak {audio.peak_dbfs(clip):.0f} dBFS")
        for provider in providers:
            start = time.perf_counter()
            try:
                raw = provider.transcribe(flac, terms)
            except SttError as e:
                print(f"\n  {provider.name}: failed: {str(e)[:160]}")
                continue
            heard = time.perf_counter() - start
            text, route = cleaner.clean(learning.apply(raw), terms=terms, examples=learning.examples(raw))
            total = time.perf_counter() - start
            print(f"\n  {provider.name}: speech {heard:.1f} s, cleanup {route} {total - heard:.1f} s")
            print(f"    heard:  {raw}")
            print(f"    pasted: {text}")


if __name__ == "__main__":
    main()
