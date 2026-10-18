"""Whisper large-v3 on Groq's servers: more accurate than the local model and about as fast.

Docs: https://console.groq.com/docs/speech-to-text
A background check keeps track of whether Groq can be reached, so when the network is down or
the company web filter blocks it, dictation goes straight to the local model without waiting
for a timeout. The same connection is reused for the check and the requests, so each request
skips the TLS handshake.
"""

import logging
import socket
import threading
import time

import httpx

from .base import BadResult, Unavailable, blocked_by_network, broken, raise_for_status

log = logging.getLogger(__name__)

_BASE = "https://api.groq.com/openai/v1"
# Whisper's prompt is limited to 224 tokens; a glossary of short terms is about 3 characters a token.
_PROMPT_CHARS = 600
# How often Groq is checked, and how soon again after it failed or the network changed.
_CHECK_SECONDS, _RECHECK_SECONDS, _NETWORK_SECONDS = 30, 5, 2
# A slow upload must not count as Groq being down: this much more time per byte of audio.
_SECONDS_PER_BYTE = 1 / 100_000


def _network() -> str:
    """The address this Mac would use to reach the internet, or "" with no network. Sends nothing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except OSError:
        return ""


def _prompt(vocabulary: list[str] | None) -> str:
    """The glossary as Whisper's prompt, the first words first, cut to fit the prompt limit."""
    prompt = ""
    for word in dict.fromkeys(vocabulary or []):
        longer = f"{prompt}, {word}" if prompt else word
        if len(longer) > _PROMPT_CHARS:
            break
        prompt = longer
    return prompt + "." if prompt else ""


class GroqWhisper:
    name = "groq"

    def __init__(self, api_key: str, model: str = "whisper-large-v3", language: str = "", timeout: float = 4):
        self.model = model
        # "" lets Whisper pick the language per clip, which keeps Hindi as Hindi.
        self.language = language
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=_BASE, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
        # None until the first check has answered; only True lets requests through.
        self._up: bool | None = None
        self._wake = threading.Event()
        threading.Thread(target=self._watch, daemon=True, name="groq-check").start()

    @property
    def available(self) -> bool:
        return bool(self._up)

    def went_down(self) -> None:
        """A request failed: skip Groq until the next check finds it working again."""
        self._set(False, "a request failed")

    def _set(self, up: bool, why: str) -> None:
        if up != self._up:
            log.info("groq is %s (%s)", "reachable" if up else "not reachable, using the local model", why)
        self._up = up
        if not up:
            self._wake.set()

    def check(self) -> bool:
        try:
            response = self._client.get("/models", timeout=3)
        except httpx.HTTPError as e:
            self._set(False, type(e).__name__)
            return False
        if blocked_by_network(response):
            self._set(False, "blocked by the web filter")
        elif response.status_code in (401, 403):
            self._set(False, f"key rejected, HTTP {response.status_code}")
        else:
            # A 429 here still means Groq answers; a rate limit is handled per request.
            self._set(response.status_code < 500, f"HTTP {response.status_code}")
        return bool(self._up)

    def _watch(self) -> None:
        network, last = _network(), 0.0
        while True:
            now = time.monotonic()
            changed = _network()
            due = _RECHECK_SECONDS if not self._up else _CHECK_SECONDS
            if changed != network or now - last >= due:
                if changed != network:
                    log.info("network changed, checking groq")
                network, last = changed, now
                if network:
                    self.check()
                else:
                    self._set(False, "no network")
            self._wake.wait(_NETWORK_SECONDS)
            self._wake.clear()

    def _request(self, flac: bytes, prompt: str) -> str:
        data = {"model": self.model, "temperature": "0", "response_format": "json"}
        if prompt:
            data["prompt"] = prompt
        if self.language:
            data["language"] = self.language
        try:
            response = self._client.post(
                "/audio/transcriptions",
                files={"file": ("dictation.flac", flac, "audio/flac")},
                data=data,
                timeout=self.timeout + len(flac) * _SECONDS_PER_BYTE,
            )
        except httpx.HTTPError as e:
            raise Unavailable(f"{self.name}: {type(e).__name__}: {e}") from e
        raise_for_status(self.name, response)
        return response.json().get("text", "").strip()

    def transcribe(self, flac: bytes, vocabulary: list[str] | None = None) -> str:
        prompt = _prompt(vocabulary)
        text = self._request(flac, prompt)
        reason = broken(text, prompt)
        if reason and prompt:
            # Try once more without the prompt, which is often what it got stuck on.
            text = self._request(flac, "")
            reason = broken(text, "")
        if reason:
            raise BadResult(f"{self.name}: transcript {reason}: {text[:60]!r}")
        return text
