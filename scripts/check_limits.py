"""One tiny request per provider. Prints whether the key works and any rate-limit headers.

Run: uv run scripts/check_limits.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from bolo import config
from bolo.stt.base import blocked_by_network


def _limit_headers(response: httpx.Response) -> None:
    found = {k: v for k, v in response.headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
    if not found:
        print("  no rate-limit headers in the response")
    for k, v in sorted(found.items()):
        print(f"  {k}: {v}")


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
    check_groq(cfg)
