"""Dictations that could not be finished, kept so nothing the user said is lost.

When every speech provider fails, the audio is saved here and tried again in the background.
When the paste fails, the text is saved. The user pastes a ready one from the menu or with the
paste_saved shortcut. Only these clips are ever written to disk; each is deleted once pasted or
after keep_hours. Files are readable by this user only.
"""

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Waits between tries: 30 s, 1, 2, 4, 8 min, then every 15 min.
_FIRST_WAIT, _LONGEST_WAIT = 30, 900


def _new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + f"{time.time_ns() // 1000 % 1_000_000:06d}"


@dataclass
class Saved:
    id: str
    created: float
    app: str
    tries: int = 0
    last_try: float = 0.0
    text: str = ""
    raw: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.text)

    def due(self, now: float) -> bool:
        wait = min(_FIRST_WAIT * 2 ** max(self.tries - 1, 0), _LONGEST_WAIT)
        return not self.ready and now - max(self.last_try, self.created) >= wait


class Pending:
    def __init__(self, folder: Path, keep_hours: float = 24):
        self.folder = folder
        self._keep_seconds = keep_hours * 3600
        self._lock = threading.Lock()
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)

    def _meta(self, saved_id: str) -> Path:
        return self.folder / f"{saved_id}.json"

    def audio_path(self, saved_id: str) -> Path:
        return self.folder / f"{saved_id}.flac"

    def _write(self, path: Path, data: bytes) -> None:
        # Created private: the audio is the user's own voice.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    def _save_meta(self, saved: Saved) -> None:
        self._write(self._meta(saved.id), json.dumps(asdict(saved)).encode())

    def add_audio(self, flac: bytes, app: str) -> Saved:
        with self._lock:
            saved = Saved(id=_new_id(), created=time.time(), app=app)
            self._write(self.audio_path(saved.id), flac)
            self._save_meta(saved)
            return saved

    def add_text(self, text: str, raw: str, app: str) -> Saved:
        """A dictation that was transcribed but could not be pasted."""
        with self._lock:
            saved = Saved(id=_new_id(), created=time.time(), app=app, text=text, raw=raw)
            self._save_meta(saved)
            return saved

    def all(self) -> list[Saved]:
        """Oldest first. Removes the ones older than keep_hours on the way."""
        items = []
        with self._lock:
            for meta in sorted(self.folder.glob("*.json")):
                try:
                    saved = Saved(**json.loads(meta.read_text()))
                except (OSError, ValueError, TypeError):
                    continue
                if time.time() - saved.created > self._keep_seconds:
                    self._delete(saved.id)
                    continue
                items.append(saved)
        return items

    def tried(self, saved: Saved, text: str = "", raw: str = "") -> None:
        """Records a try. With text the dictation is ready and its audio is deleted."""
        with self._lock:
            saved.tries += 1
            saved.last_try = time.time()
            if text:
                saved.text, saved.raw = text, raw
                self.audio_path(saved.id).unlink(missing_ok=True)
            self._save_meta(saved)

    def remove(self, saved_id: str) -> None:
        with self._lock:
            self._delete(saved_id)

    def _delete(self, saved_id: str) -> None:
        self._meta(saved_id).unlink(missing_ok=True)
        self.audio_path(saved_id).unlink(missing_ok=True)
