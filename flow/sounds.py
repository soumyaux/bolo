"""Short built-in macOS sounds for when recording starts, when it stops, and when something fails."""

import logging
from pathlib import Path

import numpy as np
import soundfile as sf
from AppKit import NSSound
from PyObjCTools import AppHelper

log = logging.getLogger(__name__)

_FOLDERS = (Path("/System/Library/Sounds"), Path.home() / "Library/Sounds")
_DEFAULTS = {"start": "Tink", "stop": "Pop", "error": "Basso"}


def names() -> list[str]:
    """The sounds that can be chosen, for the settings window."""
    found = {path.stem for folder in _FOLDERS if folder.is_dir() for path in folder.iterdir()}
    return sorted(found)


def _find(name: str) -> Path | None:
    for folder in _FOLDERS:
        for path in folder.glob(f"{name}.*") if folder.is_dir() else ():
            return path
    return None


def audible_seconds(path: Path) -> float:
    """How long the sound can be heard. The files run on for a quiet fade: Tink's is 0.56 s long
    but audible for about 40 ms, so waiting for the whole file would delay the mute for nothing."""
    samples, rate = sf.read(path, always_2d=True)
    level = np.abs(samples).max(axis=1)
    loud = np.flatnonzero(level > level.max() * 0.01)  # -40 dB below its peak
    return (loud[-1] + 1) / rate if len(loud) else 0.0


class Sounds:
    def __init__(self, cfg: dict):
        self.update(cfg)

    def update(self, cfg: dict) -> None:
        settings = cfg.get("sounds", {})
        self.enabled = settings.get("enabled", True)
        self.volume = min(max(float(settings.get("volume", 0.5)), 0.0), 1.0)
        # kind -> (NSSound, seconds audible)
        self._sounds = {}
        for kind, default in _DEFAULTS.items():
            name = settings.get(kind, default)
            path = _find(name) if name else None
            if name and path is None:
                log.warning("%s sound %r not found in %s", kind, name, _FOLDERS[0])
            if path is None:
                continue
            sound = NSSound.alloc().initWithContentsOfFile_byReference_(str(path), True)
            if sound is not None:
                self._sounds[kind] = (sound, audible_seconds(path))

    def play(self, kind: str) -> float:
        """Plays the sound for kind ("start", "stop", "error") from any thread.
        Returns how long it can be heard, or 0 when it is off."""
        if not self.enabled or self.volume == 0 or kind not in self._sounds:
            return 0.0
        sound, audible = self._sounds[kind]

        def play():
            # A double tap plays the start sound twice; NSSound refuses to play one already playing.
            sound.stop()
            sound.setVolume_(self.volume)
            sound.play()

        AppHelper.callAfter(play)
        return audible
