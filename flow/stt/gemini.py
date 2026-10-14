"""Gemini 3.5 Transcribe through the Interactions API.

Docs: https://ai.google.dev/gemini-api/docs/transcribe
Audio is sent inline (one round trip). The transcribe docs only show the Files API, so if
inline is rejected the provider switches to upload-then-transcribe and stays there.
"""

import base64
import logging
import time
from collections.abc import Callable

import httpx

from .base import Unavailable, raise_for_status

log = logging.getLogger(__name__)

_BASE = "https://generativelanguage.googleapis.com"
_BUSY_RETRIES = 2


def _output_text(body: dict) -> str:
    """Finds the transcript in the response JSON."""
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    parts = []
    for step in body.get("steps") or body.get("outputs") or []:
        for item in step.get("content") or [step]:
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
    return "".join(parts)


class GeminiTranscribe:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-transcribe",
        language_codes: list[str] | None = None,
        smart_mode: bool = True,
        timeout: float = 8,
        on_request: Callable[[], None] | None = None,
    ):
        """on_request runs for every transcription request sent, to count them against the daily limit."""
        self.model = model
        self._on_request = on_request
        self.language_codes = language_codes or []
        self.smart_mode = smart_mode
        self.inline = True
        # How the last clip was sent: "inline" or "upload". Read by the timing script.
        self.last_route = ""
        self._client = httpx.Client(
            base_url=_BASE, headers={"x-goog-api-key": api_key}, timeout=timeout
        )

    def _upload(self, flac: bytes) -> str:
        start = self._client.post(
            "/upload/v1beta/files",
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(len(flac)),
                "X-Goog-Upload-Header-Content-Type": "audio/flac",
            },
            json={"file": {"display_name": "dictation"}},
        )
        raise_for_status(self.name, start)
        done = self._client.post(
            start.headers["x-goog-upload-url"],
            headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
            content=flac,
        )
        raise_for_status(self.name, done)
        return done.json()["file"]["uri"]

    def _request(self, audio: dict, vocabulary: list[str] | None) -> httpx.Response:
        transcription: dict = {}
        if self.language_codes:
            transcription["language_codes"] = self.language_codes
        if vocabulary:
            transcription["custom_vocabulary"] = vocabulary[:1000]
        if self.smart_mode:
            transcription["mode"] = "smart"
        body: dict = {
            "model": self.model,
            "input": [{"type": "audio", "mime_type": "audio/flac", **audio}],
        }
        if transcription:
            body["generation_config"] = {"transcription_config": transcription}
        # The free tier often answers 503 "high demand" and then accepts the same request.
        for attempt in range(_BUSY_RETRIES + 1):
            if self._on_request:
                self._on_request()
            response = self._client.post("/v1beta/interactions", json=body)
            if response.status_code != 503 or attempt == _BUSY_RETRIES:
                break
            log.info("gemini is busy (503), retrying")
            time.sleep(1)
        return response

    def transcribe(self, flac: bytes, vocabulary: list[str] | None = None) -> str:
        try:
            response = None
            if self.inline:
                data = base64.b64encode(flac).decode("ascii")
                response = self._request({"data": data}, vocabulary)
                self.last_route = "inline"
                if response.status_code == 400:
                    log.warning("gemini rejected inline audio, using Files API: %s", response.text[:300])
                    self.inline = False
            if not self.inline:
                response = self._request({"uri": self._upload(flac)}, vocabulary)
                self.last_route = "upload"
        except httpx.HTTPError as e:
            raise Unavailable(f"{self.name}: {type(e).__name__}: {e}") from e
        raise_for_status(self.name, response)
        return _output_text(response.json()).strip()
