"""Plays every state of the island over the notch with a fake microphone, for tuning its look.

Run: uv run scripts/island_demo.py           (once through, then quits)
     uv run scripts/island_demo.py --loop    (over and over; Ctrl+C to stop)

Hover on the notch between states to see the peek; hover the island while it listens for ×.
"""

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
from PyObjCTools import AppHelper

from bolo.overlay import Overlay

SPOKEN = (
    "so I think we should ship the notch island tomorrow, first make the meter a little calmer "
    "and then check how it looks on the external display"
)


def fake_level() -> float:
    """Speech-like: syllables of about 5 a second, phrases that come and go, a little noise."""
    now = time.monotonic()
    phrase = max(0.0, math.sin(now * 0.9))
    syllable = 0.55 + 0.45 * abs(math.sin(now * 5 * math.pi / 2))
    return min(1.0, phrase * syllable * 0.95 + random.uniform(0, 0.06))


def main() -> None:
    loop = "--loop" in sys.argv
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    island = Overlay(fake_level, lambda: island.message("Cancelled", kind="cancel"))
    island.ready("Ready")
    steps = [
        (1.0, lambda: island.listening()),
        (4.0, lambda: island.listening("hands-free · tap fn to stop")),
        (2.5, lambda: island.heard("so I think we should ship the notch island tomorrow")),
        (3.0, lambda: island.transcribing()),
        (1.6, lambda: island.polishing(SPOKEN, "qwen2.5:3b")),
        (3.4, lambda: island.done("Pasted · 24 words")),
        (2.4, lambda: island.message("Too short: hold fn while you speak", kind="warn")),
        (2.4, lambda: island.message("Couldn't transcribe. Don't worry, your audio is saved and will be tried again.", 3, "error")),
        (3.6, lambda: island.listening()),
        (2.0, lambda: island.transcribing()),
        (1.2, lambda: island.done("Pasted · 6 words")),
        (2.5, lambda: island.polishing("as a prompt build me a settings page with a dark mode toggle", "qwen2.5:7b", True)),
        (3.0, lambda: island.message("Cancelled", kind="cancel")),
    ]

    def play(at: int = 0) -> None:
        if at == len(steps):
            if loop:
                AppHelper.callLater(1.5, play, 0)
            else:
                AppHelper.callLater(2.0, AppHelper.stopEventLoop)
            return
        delay, step = steps[at]
        AppHelper.callLater(delay, lambda: (step(), play(at + 1)))

    play()
    AppHelper.runEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
