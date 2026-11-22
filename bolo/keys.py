"""Reads the keyboard shortcuts written in config.toml."""

import Quartz

# Key codes of the letters and the modifier names a shortcut in config.toml may use.
_LETTERS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38,
    "k": 40, "n": 45, "m": 46,
}  # fmt: skip
_MODIFIERS = {
    "<ctrl>": Quartz.kCGEventFlagMaskControl,
    "<alt>": Quartz.kCGEventFlagMaskAlternate,
    "<cmd>": Quartz.kCGEventFlagMaskCommand,
    "<shift>": Quartz.kCGEventFlagMaskShift,
}


def parse_shortcut(text: str) -> tuple[int, int] | None:
    """Turns "<ctrl>+<alt>+f" into (key code, modifier flags). None when it cannot be read."""
    code, flags = None, 0
    for part in text.lower().replace(" ", "").split("+"):
        if part in _MODIFIERS:
            flags |= _MODIFIERS[part]
        elif part in _LETTERS and code is None:
            code = _LETTERS[part]
        else:
            return None
    return (code, flags) if code is not None and flags else None
