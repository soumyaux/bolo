"""Keeps dictation audio for a while, so a speech model can be trained on this user's voice.

Each clip is saved next to its dictation in the learning store; the text pasted, or the user's
correction of it, is its label. Clips are readable by this user only, are deleted as soon as a
training run has used them, and never live longer than keep_days.
"""

import logging
import os
import re
import time
import zlib
from pathlib import Path

from .diff import distance
from .store import PATH, Store

log = logging.getLogger(__name__)

FOLDER = PATH.parent / "voice"
MODELS = PATH.parent / "models"
# A training run needs about this much labelled speech to do better than the stock model.
NEEDED_MINUTES = 45
# The model trained on this user's voice, written by scripts/train_voice.py.
TRAINED = MODELS / "whisper-me"
# Every fifth clip, picked by its id, is kept out of training and only used to score models.
_HELD_OUT = 5
_FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "hm", "hmm", "aa"}
_LIST_MARK = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+")


def held_out(row) -> bool:
    return zlib.crc32(str(row["id"]).encode()) % _HELD_OUT == 0


def words(text: str) -> list[str]:
    """The words of a transcript for scoring: no case, punctuation, list marks or fillers."""
    text = _LIST_MARK.sub(" ", text).lower().replace("-", " ")
    return [w for w in re.findall(r"[\w']+", text) if w not in _FILLERS]


def errors(heard: str, label: str) -> tuple[int, int]:
    """(word errors, words in the label): word error rate is their ratio."""
    want = words(label)
    return distance(words(heard), want), len(want)


def training_text(label: str) -> str:
    """The label as one line of speech, as Whisper would write it."""
    return " ".join(_LIST_MARK.sub(" ", label).split())


class Voice:
    def __init__(self, store: Store, folder: Path = FOLDER, keep_days: float = 14):
        self.store = store
        self.folder = folder
        self._keep_seconds = keep_days * 86400
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)

    def save(self, dictation_id: int, flac: bytes, seconds: float) -> None:
        path = self.folder / f"{dictation_id}.flac"
        # Created private: the audio is the user's own voice.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(flac)
        self.store.set_audio(dictation_id, path.name, seconds)

    def path(self, row) -> Path:
        return self.folder / row["audio"]

    def delete(self, rows) -> int:
        """Deletes these clips and unlinks them from their dictations. Returns how many."""
        for row in rows:
            self.path(row).unlink(missing_ok=True)
            self.store.set_audio(row["id"], None)
        return len(rows)

    def prune(self) -> None:
        """Deletes clips older than keep_days, clips whose file is gone, and stray files."""
        now = time.time()
        rows = self.store.with_audio()
        old = [row for row in rows if now - row["time"] > self._keep_seconds or not self.path(row).exists()]
        if old:
            log.info("deleted %d saved voice clip(s) past their time", self.delete(old))
        linked = {row["audio"] for row in rows}
        for path in self.folder.glob("*.flac"):
            if path.name not in linked and now - path.stat().st_mtime > 3600:
                path.unlink(missing_ok=True)

    def minutes(self) -> float:
        return sum(row["audio_seconds"] or 0 for row in self.store.with_audio()) / 60

    def line(self) -> str:
        return f"Voice training: {self.minutes():.0f} of {NEEDED_MINUTES} min collected"
