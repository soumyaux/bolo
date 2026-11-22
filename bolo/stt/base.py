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
    """True when a network filter answered instead of the provider."""
    return response.headers.get("server", "").lower().startswith("zscaler")


def raise_for_status(name: str, response: httpx.Response) -> None:
    """Turns an error response into RateLimited or Unavailable."""
    if response.is_success:
        return
    if blocked_by_network(response):
        raise Unavailable(f"{name}: blocked by a network filter")
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
# What Whisper writes when it hears near silence: lines from the videos it was trained on.
_STOCK_PHRASE = re.compile(
    r"(?:and )?(?:this is the end of (?:this|the) video|thanks? (?:you )?for watching|please subscribe"
    r"|subtitles by|transcribed by|see you (?:in the )?next video)\W*",
    re.IGNORECASE,
)


def broken(text: str, prompt: str) -> str:
    """Why a transcript is nonsense, or "" when it looks like real speech."""
    words = _WORD.findall(text.lower())
    if not words:
        return ""
    if _STOCK_PHRASE.fullmatch(text.strip()):
        return "is a stock phrase"
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


# A word worth spelling for Whisper: an identifier (camelCase, snake_case, dotted, with digits), an
# acronym, or a capitalised name in mid-sentence.
_TOKEN = re.compile(r"(?<![\w.])[A-Za-z][\w.-]*[\w]")
_CONTEXT_CHARS = 3000
_CONTEXT_TERMS = 15


def context_terms(text: str) -> list[str]:
    """Distinctive words from the text already in the field being typed into, nearest the end first,
    so Whisper spells names and identifiers the way they are written there. Ordinary English words
    are left out: they need no help and would use up Groq's 224-token prompt."""
    from ..learn.diff import english

    found: dict[str, None] = {}
    tail = text[-_CONTEXT_CHARS:]
    for match in reversed(list(_TOKEN.finditer(tail))):
        word = match.group().rstrip(".-")
        before = tail[: match.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?\n"
        identifier = any(c in word for c in "_.") or any(c.isdigit() for c in word) or (
            any(c.isupper() for c in word[1:])) or (
            "-" in word and not all(english(part) for part in word.split("-")))
        name = word[0].isupper() and not sentence_start and not english(word)
        if len(word) >= 2 and (identifier or name) and word.lower() not in {w.lower() for w in found}:
            found[word] = None
            if len(found) == _CONTEXT_TERMS:
                break
    return list(found)
