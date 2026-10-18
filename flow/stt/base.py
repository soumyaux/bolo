"""Shared interface for speech providers."""

import io
import re
import zlib
from typing import Protocol

import httpx
import soundfile as sf


class SttError(Exception):
    pass


class RateLimited(SttError):
    """The provider's cap is reached. retry_after is in seconds when the provider says so."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(SttError):
    """The provider cannot be used right now: no network, timeout, bad key, blocked, server error."""


class BadResult(Unavailable):
    """This clip came out as nonsense. Another provider should try it; this one is not rested."""


class Provider(Protocol):
    name: str

    def transcribe(self, flac: bytes, vocabulary: list[str] | None = None) -> str: ...


def flac_seconds(flac: bytes) -> float:
    """How long a FLAC clip is, read from its header."""
    return sf.info(io.BytesIO(flac)).duration


def blocked_by_network(response: httpx.Response) -> bool:
    """True when a company web filter answered instead of the provider."""
    return response.headers.get("server", "").lower().startswith("zscaler")


def raise_for_status(name: str, response: httpx.Response) -> None:
    """Turns an error response into RateLimited or Unavailable."""
    if response.is_success:
        return
    if blocked_by_network(response):
        raise Unavailable(f"{name}: blocked by this network's web filter")
    detail = response.text[:300].replace("\n", " ")
    if response.status_code == 429:
        retry = response.headers.get("retry-after")
        raise RateLimited(
            f"{name}: rate limited: {detail}",
            float(retry) if retry and retry.replace(".", "", 1).isdigit() else None,
        )
    if response.status_code in (401, 403):
        raise Unavailable(f"{name}: key rejected (HTTP {response.status_code}): {detail}")
    raise Unavailable(f"{name}: HTTP {response.status_code}: {detail}")


_WORD = re.compile(r"\w+")


def broken(text: str, prompt: str) -> str:
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
