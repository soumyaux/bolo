"""Whisper large-v3 on Groq's servers: more accurate than the local model and about as fast.

Docs: https://console.groq.com/docs/speech-to-text
A background check keeps track of whether Groq can be reached, so when the network is down or
a network filter blocks it, dictation goes straight to the local model without waiting
for a timeout. The same connection is reused for the check and the requests, so each request
skips the TLS handshake.
"""

import json
import logging
import socket
import threading
import time
from collections import deque
from pathlib import Path

import httpx

from .base import BadResult, Unavailable, blocked_by_network, broken, flac_seconds, raise_for_status

log = logging.getLogger(__name__)

_BASE = "https://api.groq.com/openai/v1"
# Whisper's prompt is limited to 224 tokens; a glossary of short terms is about 3 characters a token.
_PROMPT_CHARS = 600
# How often Groq is checked, and how soon again after it failed or the network changed.
_CHECK_SECONDS, _RECHECK_SECONDS, _NETWORK_SECONDS = 30, 5, 2
# How long an idle connection is kept for the next request.
_KEEP_OPEN_SECONDS = 60
# A slow upload must not count as Groq being down: this much more time per byte of audio.
_SECONDS_PER_BYTE = 1 / 100_000
# Free tier (console.groq.com, 2026-10-18): 20 requests a minute, 2000 a day, 7200 audio seconds an
# hour, 28800 a day, each request billed as at least 10 s. Groq is left alone at 90% of each, so a
# 429 is rare. A 429 still rests Groq if the count is off (the same key used elsewhere).
_LIMITS = (
    (60, 20, "requests", "minute"),
    (86_400, 2000, "requests", "day"),
    (3600, 7200, "audio seconds", "hour"),
    (86_400, 28_800, "audio seconds", "day"),
)
_SHARE = 0.9
_BILLED_AT_LEAST = 10
# Groq's reply headers on what is left of the daily request limit.
_LIMIT_HEADER, _LEFT_HEADER = "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests"


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


class Budget:
    """Counts what Groq has billed over the last day, so dictation moves to the local model before a
    limit is hit. Kept in path, so a restart does not forget it."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._used: deque[tuple[float, float]] = deque()  # (when, billed seconds)
        # Requests used today as Groq last reported it, and when; counts uses of the key elsewhere too.
        self._reported: tuple[float, int] | None = None
        self._lock = threading.Lock()
        try:
            saved = json.loads(path.read_text()) if path and path.exists() else {}
            self._used.extend((when, billed) for when, billed in saved.get("used", []))
            self._reported = tuple(saved["reported"]) if saved.get("reported") else None
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("groq usage not read, starting from zero: %s", e)

    def spend(self, seconds: float, headers=None) -> None:
        with self._lock:
            now = time.time()
            self._used.append((now, max(seconds, _BILLED_AT_LEAST)))
            limit, left = (headers or {}).get(_LIMIT_HEADER), (headers or {}).get(_LEFT_HEADER)
            if limit and left and limit.isdigit() and left.isdigit():
                self._reported = (now, int(limit) - int(left))
            self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.write_text(json.dumps({"used": list(self._used), "reported": self._reported}))
        except OSError as e:
            log.warning("groq usage not saved: %s", e)

    def _recent(self, now: float, window: float) -> list[float]:
        while self._used and now - self._used[0][0] > 86_400:
            self._used.popleft()
        return [billed for when, billed in self._used if now - when <= window]

    def _counted(self, now: float, window: float, kind: str) -> float:
        recent = self._recent(now, window)
        if kind != "requests":
            return sum(recent)
        used = len(recent)
        if window == 86_400 and self._reported and now - self._reported[0] <= 86_400:
            # Groq's own count also holds requests made with this key from anywhere else.
            used = max(used, self._reported[1] + sum(1 for when, _ in self._used if when > self._reported[0]))
        return used

    def full(self, seconds: float) -> str:
        """Which limit this clip would go past, or "" when it fits."""
        now = time.time()
        with self._lock:
            for window, limit, kind, span in _LIMITS:
                used = self._counted(now, window, kind)
                extra = 1 if kind == "requests" else max(seconds, _BILLED_AT_LEAST)
                if used + extra > limit * _SHARE:
                    return f"{used:.0f} of {limit} {kind} this {span} used"
        return ""

    def day_share(self) -> float:
        """How much of the daily limits is used, 0 to 1: the larger of requests and audio seconds."""
        now = time.time()
        with self._lock:
            return max(
                self._counted(now, window, kind) / limit for window, limit, kind, _ in _LIMITS if window == 86_400
            )


class GroqWhisper:
    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str = "whisper-large-v3",
        language: str = "",
        timeout: float = 4,
        local_under: float = 0,
        saving_from: float = 0.7,
        usage_file: Path | None = None,
    ):
        self.model = model
        # Once this share of the daily limit is used, clips shorter than local_under go to the local
        # model (Groq bills them as 10 s anyway) and a nonsense result is not asked of Groq again.
        self.local_under = local_under
        self.saving_from = saving_from
        self.budget = Budget(usage_file)
        self._saving = False
        self._full = ""
        # "" lets Whisper pick the language per clip, which keeps Hindi as Hindi.
        self.language = language
        self.timeout = timeout
        # httpx closes a connection after 5 s idle by default, which cost a new TLS handshake
        # (about 170 ms) on nearly every dictation. Kept open longer than the 30 s check, the check
        # keeps it warm; Groq was measured keeping an idle connection open for at least 35 s.
        self._client = httpx.Client(
            base_url=_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            limits=httpx.Limits(keepalive_expiry=_KEEP_OPEN_SECONDS),
        )
        # None until the first check has answered; only True lets requests through.
        self._up: bool | None = None
        self._wake = threading.Event()
        threading.Thread(target=self._watch, daemon=True, name="groq-check").start()

    @property
    def available(self) -> bool:
        return bool(self._up)

    @property
    def saving(self) -> bool:
        """True once saving_from of the daily limit is used."""
        saving = self.budget.day_share() >= self.saving_from
        if saving != self._saving:
            log.info("groq saving mode %s", "on: short clips go to the local model" if saving else "off")
            self._saving = saving
        return saving

    def takes(self, seconds: float) -> bool:
        """False when this clip is better sent to the local model: Groq is near a limit, or it is
        short and saving mode is on."""
        if seconds < self.local_under and self.saving:
            return False
        full = self.budget.full(seconds)
        if full != self._full:
            log.info("groq %s", f"is near its free limit ({full}), using the local model" if full else "has room again")
            self._full = full
        return not full

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
            self._set(False, "blocked by a network filter")
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

    def _request(self, flac: bytes, prompt: str, seconds: float) -> str:
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
        if response.status_code != 429:
            # Anything Groq answered may count; a 429 was turned away.
            self.budget.spend(seconds, response.headers)
        raise_for_status(self.name, response)
        return response.json().get("text", "").strip()

    def transcribe(self, flac: bytes, vocabulary: list[str] | None = None) -> str:
        prompt = _prompt(vocabulary)
        seconds = flac_seconds(flac)
        text = self._request(flac, prompt, seconds)
        reason = broken(text, prompt)
        if reason and prompt and not self.saving:
            # Try once more without the prompt, which is often what it got stuck on.
            text = self._request(flac, "", seconds)
            reason = broken(text, "")
        if reason:
            # The local model tries this clip instead.
            raise BadResult(f"{self.name}: transcript {reason}: {text[:60]!r}")
        return text
