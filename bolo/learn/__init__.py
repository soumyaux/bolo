"""Learning from the user's corrections. No model is trained: learned words and a few examples
are kept on this Mac and sent as hints with each request, so every speech provider benefits.
"""

import logging
import re
from pathlib import Path

from . import diff
from .store import PATH, Store

log = logging.getLogger(__name__)

# How many past corrections are shown to the cleanup model as examples.
_EXAMPLES = 5


# A typo made while editing: a stray symbol, or capitals in odd places ("PilL", "IDv", "reName").
_ODD = re.compile(r"[^\w\s.'@&+#/-]|\b[A-Z]+[a-z]\b|\b[a-z]+[A-Z]\w*|\b\w*[a-z][A-Z]\b")


_UNPRINTABLE = re.compile(r"[\ue000-\uf8ff\x00-\x08\x0b-\x1f\x7f]")


# A word split by case into a piece too short to be a real part ("MapedsKu"), unlike "GitHub".
_SHORT_HUMP = re.compile(r"[a-z][A-Z][a-z]?\b")
# Capitals that spell a word but are usually meant as letters.
_ACRONYMS = {"AI", "AM", "PM", "ID", "IT", "OK", "TV", "UK", "US", "USA"}


def _shouting(word: str) -> bool:
    """An ordinary word typed in capitals ("IF", "TEASER") is a slip of Caps Lock, not a name."""
    return len(word) >= 2 and word not in _ACRONYMS and word.isupper() and word.isalpha() and diff.english(word)


def _junk(intended: str) -> bool:
    if _ODD.search(intended) or _SHORT_HUMP.search(intended):
        return True
    return any(_shouting(w) for w in re.findall(r"[\w']+", intended))


def usable(heard: str, intended: str) -> bool:
    """True when a learned swap may be applied to every dictation. An ordinary word ("then",
    "mode", "test") is right in some sentences and wrong in others, so it is never replaced
    everywhere; the cleanup pass learns those from examples instead. Nor are numbers or typos."""
    words = re.findall(r"[\w']+", heard)
    if not words or all(w.isdigit() for w in words) or _junk(intended):
        return False
    if heard.lower() == intended.lower():
        # Only the capitals change ("github" to "GitHub"): safe for a name.
        return any(c.isupper() for c in intended)
    return not all(diff.english(w) for w in words)


class Learning:
    def __init__(self, enabled: bool = True, path: Path = PATH):
        self.store = Store(path) if enabled else None

    def _active(self) -> list:
        """Learned words in use: switched on, seen twice (or trusted), and safe to use everywhere."""
        if self.store is None:
            return []
        rows = [row for row in self.store.vocabulary() if row["enabled"]]
        counts = {(row["heard"].lower(), row["intended"].lower()): row["count"] for row in rows}
        best = {}
        for row in rows:
            # Undone as often as it was made ("mode" to "more", then "more" back to "mode"): it
            # depends on the sentence, so it is not a rule.
            undone = counts.get((row["intended"].lower(), row["heard"].lower()), 0)
            if row["count"] >= 2 and undone * 2 < row["count"] and usable(row["heard"], row["intended"]):
                # Rows come strongest first, so one heard word maps to its most confirmed spelling.
                best.setdefault(row["heard"], row)
        return list(best.values())

    def terms(self, dictionary: list[str]) -> list[str]:
        """The learned spellings, most often corrected first, then the personal dictionary, for the
        speech and cleanup prompts. Groq's prompt is cut at 224 tokens, so the order matters."""
        learned = [row["intended"] for row in sorted(self._active(), key=lambda r: -r["count"])]
        return list(dict.fromkeys([*learned, *dictionary]))

    def apply(self, text: str) -> str:
        """Replaces each mis-heard word with the spelling the user corrected it to."""
        for row in sorted(self._active(), key=lambda r: -len(r["heard"])):
            pattern = rf"(?<![\w]){re.escape(row['heard'])}(?![\w])"
            text = re.sub(pattern, lambda _: row["intended"], text, flags=re.IGNORECASE)
        return text

    def examples(self, text: str, app: str = "") -> list[tuple[str, str]]:
        """The past corrections most like this text, as (raw transcript, what the user wanted).
        Corrections made in the same app come first: a Slack message is written unlike an email."""
        if self.store is None:
            return []
        rows = sorted(
            self.store.examples(), key=lambda r: (bool(app) and r["app"] != app, -diff.similarity(text, r["raw"]))
        )
        return [(row["raw"], row["final"]) for row in rows[:_EXAMPLES]]

    def record(self, app: str, raw: str, pasted: str, mode: str = "") -> int | None:
        return self.store.add_dictation(app, raw, pasted, mode) if self.store else None

    def last(self) -> tuple[int, str] | None:
        """The most recent dictation as (id, the text as it now stands)."""
        row = self.store.dictation() if self.store else None
        return (row["id"], row["corrected"] or row["pasted"]) if row else None

    def learn(self, dictation_id: int, before: str, after: str) -> list[diff.Swap]:
        """Saves a correction and returns the word swaps taken from it."""
        row = self.store.dictation(dictation_id) if self.store else None
        # Editor icons and control characters read from some fields ("\ueb15") are not text.
        after = _UNPRINTABLE.sub("", after)
        if row is None or before == after:
            return []
        self.store.set_corrected(dictation_id, after)
        found = [swap for swap in diff.swaps(before, after) if not _junk(swap.intended)]
        for swap in found:
            # A trusted swap counts as seen twice, so it is used from the next dictation on.
            self.store.see_swap(swap.heard, swap.intended, 2 if swap.trusted else 1)
        # A rewrite changes the meaning, so it is not an example of how to clean up. Nor is a
        # prompt-mode dictation: it would teach the normal cleanup to restructure text.
        if row["mode"] != "prompt" and (found or diff.similarity(before, after) >= 0.7):
            self.store.add_example(row["raw"], after, row["app"])
        log.info("correction saved, %d word swap(s)", len(found))
        return found
