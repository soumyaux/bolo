"""Shared interface for speech providers."""

from typing import Protocol

import httpx


class SttError(Exception):
    pass


class RateLimited(SttError):
    """The provider's cap is reached. retry_after is in seconds when the provider says so."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(SttError):
    """The provider cannot be used right now: no network, timeout, bad key, blocked, server error."""


class Provider(Protocol):
    name: str

    def transcribe(self, flac: bytes, vocabulary: list[str] | None = None) -> str: ...


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
