"""Records a few seconds, transcribes it and prints the text with per-stage timings.

Run: uv run scripts/try_transcribe.py            (5 seconds)
     uv run scripts/try_transcribe.py 8 --verbatim
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow import audio, config
from flow.stt.base import SttError
from flow.stt.gemini import GeminiTranscribe


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    duration = float(args[0]) if args else 5.0
    cfg = config.load()
    api_key = config.key("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY is not set in .env")
    gemini = cfg["stt"]["gemini"]
    provider = GeminiTranscribe(
        api_key,
        model=gemini["model"],
        language_codes=gemini["language_codes"],
        smart_mode=gemini["smart_mode"] and "--verbatim" not in sys.argv,
        timeout=30,
    )

    recorder = audio.Recorder(cfg["audio"]["sample_rate"])
    recorder.start()
    print(f"Speak now ({duration:.0f} s)...")
    time.sleep(duration)
    clip = recorder.stop()
    print(f"recorded {audio.seconds(clip):.1f} s, peak {audio.peak_dbfs(clip):.0f} dBFS")

    t0 = time.perf_counter()
    flac = audio.to_flac(clip)
    t1 = time.perf_counter()
    # Two runs: the first includes the TLS handshake, the second shows a warm connection.
    for run in ("cold", "warm"):
        start = time.perf_counter()
        try:
            text = provider.transcribe(flac, cfg["dictionary"]["terms"])
        except SttError as e:
            sys.exit(f"failed: {e}")
        took = time.perf_counter() - start
        print(f"{run}: {took * 1000:.0f} ms ({provider.last_route}, smart={provider.smart_mode})")
    print(f"encode: {(t1 - t0) * 1000:.0f} ms, FLAC {len(flac) / 1024:.0f} KB")
    print(f"\n{text}")


if __name__ == "__main__":
    main()
