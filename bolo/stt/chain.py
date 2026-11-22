"""The speech providers in the order they are tried."""

import logging
import time

from .. import config
from ..learn.store import PATH as LEARNING_DB
from .base import BadResult, RateLimited, Unavailable, flac_seconds
from .groq import GroqWhisper
from .local_whisper import LocalWhisper

log = logging.getLogger("bolo")


class Chain:
    """Tries providers in order and rests one that failed while another can answer. A provider
    with its own reachability check (Groq) is skipped at once while it is known to be down."""

    def __init__(self, providers: list):
        self.providers = providers
        self._rest_until: dict[str, float] = {}

    def _ready(self) -> list:
        now = time.monotonic()
        return [
            p for p in self.providers if now >= self._rest_until.get(p.name, 0) and getattr(p, "available", True)
        ]

    def first_ready(self):
        """The provider that would be tried first now, or None."""
        return (self._ready() or self.providers or [None])[0]

    def transcribe(self, flac: bytes, vocabulary: list[str]) -> tuple[str, str]:
        errors = []
        ready = self._ready() or self.providers
        # A provider can pass on a clip (Groq: too short, or near its free limit) while another can take it.
        seconds = flac_seconds(flac)
        ready = [p for p in ready if getattr(p, "takes", lambda _: True)(seconds)] or ready
        for provider in ready:
            try:
                return provider.transcribe(flac, vocabulary), provider.name
            except RateLimited as e:
                self._rest_until[provider.name] = time.monotonic() + (e.retry_after or 60)
                errors.append(str(e))
            except BadResult as e:
                errors.append(str(e))
            except Unavailable as e:
                if hasattr(provider, "went_down"):
                    # Its own check brings it back as soon as it answers again.
                    provider.went_down()
                else:
                    self._rest_until[provider.name] = time.monotonic() + 30
                errors.append(str(e))
            log.warning("%s", errors[-1])
        raise Unavailable("; ".join(errors) or "no speech provider is set up")


def build_providers(cfg: dict) -> list:
    providers = []
    for name in cfg["stt"]["order"]:
        if name == "groq" and config.key("GROQ_API_KEY"):
            groq = cfg["stt"].get("groq", {})
            providers.append(
                GroqWhisper(
                    config.key("GROQ_API_KEY"),
                    model=groq.get("model", "whisper-large-v3"),
                    language=groq.get("language", ""),
                    timeout=cfg["stt"]["timeout_seconds"],
                    local_under=groq.get("local_under_seconds", 0),
                    saving_from=groq.get("saving_from", 0.7),
                    usage_file=LEARNING_DB.parent / "groq_usage.json",
                )
            )
        elif name == "local":
            local = cfg["stt"]["local"]
            providers.append(
                LocalWhisper(local["model"], local["languages"], local["unload_after_idle_minutes"])
            )
        else:
            log.info("provider %s is skipped: not set up on this Mac", name)
    return providers
