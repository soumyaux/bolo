"""One tiny request per provider. Prints whether the key works and any rate-limit headers.

Run: uv run scripts/check_limits.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import numpy as np

from flow import audio, config
from flow.stt.base import SttError, blocked_by_network
from flow.stt.gemini import GeminiTranscribe


def _limit_headers(response: httpx.Response) -> None:
    found = {k: v for k, v in response.headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
    if not found:
        print("  no rate-limit headers in the response")
    for k, v in sorted(found.items()):
        print(f"  {k}: {v}")


def check_gemini(cfg: dict) -> None:
    print("Gemini")
    api_key = config.key("GEMINI_API_KEY")
    if not api_key:
        print("  GEMINI_API_KEY is not set in .env")
        return
    model = cfg["stt"]["gemini"]["model"]
    meta = httpx.get(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}",
        headers={"x-goog-api-key": api_key},
        timeout=15,
    )
    print(f"  model lookup {model}: HTTP {meta.status_code}")
    if not meta.is_success:
        print(f"  {meta.text[:300]}")
        return
    info = meta.json()
    for field in ("displayName", "inputTokenLimit", "outputTokenLimit"):
        if field in info:
            print(f"  {field}: {info[field]}")

    # One second of silence: the smallest real transcription request.
    silence = audio.to_flac(np.zeros(16000, dtype=np.int16))
    provider = GeminiTranscribe(api_key, model=model, smart_mode=cfg["stt"]["gemini"]["smart_mode"])
    try:
        text = provider.transcribe(silence)
        print(f"  transcription request: ok, sent {provider.last_route}, returned {text!r}")
    except SttError as e:
        print(f"  transcription request failed: {e}")
    print("  The free daily cap is not in the API. Read it in Google AI Studio > Rate limits.")


def check_groq(cfg: dict) -> None:
    print("Groq")
    api_key = config.key("GROQ_API_KEY")
    try:
        response = httpx.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key or 'none'}"},
            timeout=15,
        )
    except httpx.HTTPError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            print("  this network's web filter intercepts the connection, so it cannot be used here")
        else:
            print(f"  cannot connect: {type(e).__name__}: {str(e)[:200]}")
        return
    if blocked_by_network(response):
        print("  blocked by this network's web filter, so it cannot be used here")
        return
    if not api_key:
        print("  GROQ_API_KEY is not set in .env")
        return
    print(f"  models: HTTP {response.status_code}")
    if response.is_success:
        ids = sorted(m["id"] for m in response.json().get("data", []))
        print("  speech models:", [i for i in ids if "whisper" in i] or "none listed")
    _limit_headers(response)


if __name__ == "__main__":
    cfg = config.load()
    check_gemini(cfg)
    check_groq(cfg)
