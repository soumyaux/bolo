"""Compares pasted text with the user's corrected version and picks out the mis-heard words."""

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

_EDGE = ".,!?;:\"'()[]{}"
# A mis-hearing is short on both sides; anything longer is the user rewording.
_MAX_WORDS = 3
# Above this share of reworded words the sentence was rewritten, and nothing is learned from it.
_REWRITE_SHARE = 0.3
_NAME = re.compile(r"[A-Z0-9_./-]")
# Ordinary English words, to tell a real word from a typo or a mis-hearing.
_ENGLISH_WORDS = Path("/usr/share/dict/words")
_english: set[str] | None = None


def english(word: str) -> bool:
    """True for an ordinary English word."""
    global _english
    if _english is None:
        try:
            _english = set(_ENGLISH_WORDS.read_text().lower().split())
        except OSError:
            _english = set()
    return word.lower() in _english


class Swap(NamedTuple):
    heard: str
    intended: str
    # A name or code identifier is trusted the first time; so is a word that sounds almost the
    # same ("test" for "text"). Any other ordinary word must be seen twice.
    name: bool

    @property
    def trusted(self) -> bool:
        if self.name:
            return True
        if not sounds_alike(self.heard, self.intended):
            return False
        # A real word "corrected" to a non-word is more likely a typo ("button" to "buttom").
        real = lambda text: all(english(w) for w in re.findall(r"[\w']+", text))
        return real(self.intended) or not real(self.heard)


class _Word(NamedTuple):
    core: str
    starts_sentence: bool
    begin: int
    end: int


def _words(text: str) -> list[_Word]:
    """Each word without its edge punctuation, with its place in the text."""
    words, starts, last = [], True, 0
    for match in re.finditer(r"\S+", text):
        token = match.group()
        if "\n" in text[last : match.start()]:
            starts = True
        core = token.strip(_EDGE)
        if core:
            words.append(_Word(core, starts, match.start(), match.end()))
        starts = token[-1] in ".!?:"
        last = match.end()
    return words


def _matcher(a: list[_Word], b: list[_Word]) -> SequenceMatcher:
    return SequenceMatcher(None, [w.core.lower() for w in a], [w.core.lower() for w in b], autojunk=False)


def _runs(a: list[_Word], b: list[_Word]) -> list[tuple[list[_Word], list[_Word]]]:
    """Stretches where the texts differ, as (words before, words after). A changed capital counts."""
    runs, run = [], None
    for op, i1, i2, j1, j2 in _matcher(a, b).get_opcodes():
        if op != "equal":
            run = run or ([], [])
            run[0].extend(a[i1:i2])
            run[1].extend(b[j1:j2])
            continue
        for old, new in zip(a[i1:i2], b[j1:j2]):
            if old.core != new.core:
                run = run or ([], [])
                run[0].append(old)
                run[1].append(new)
            elif run:
                runs.append(run)
                run = None
    if run:
        runs.append(run)
    return runs


def _skeleton(letters: str) -> str:
    """A rough sound key: the first letter, then consonants with look-alike sounds merged."""
    letters = letters.replace("ph", "f").replace("x", "ks").translate(str.maketrans("ckqzy", "kkksi"))
    key = letters[:1] + re.sub(r"[aeiouh]", "", letters[1:])
    return re.sub(r"(.)\1+", r"\1", key)


def distance(a, b) -> int:
    """Levenshtein distance: the fewest single insertions, deletions or changes from a to b.
    Works on strings (letters) and on lists (words)."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def letters(text: str) -> str:
    return re.sub(r"[\W_]", "", text.lower())


def sound_key(text: str) -> str:
    """The rough sound of a word or phrase, for matching dictionary terms."""
    return _skeleton(letters(text))


def sounds_alike(heard: str, intended: str) -> bool:
    """True when the two are close enough in sound that speech recognition would mix them up:
    "test" and "text", "taste" and "text". Stricter than _alike, so it can be trusted at once."""
    h, i = letters(heard), letters(intended)
    if len(h) < 3 or len(i) < 3 or h == i:
        return False
    return distance(h, i) <= 2 or (distance(_skeleton(h), _skeleton(i)) <= 1 and h[0] == i[0])


def _alike(heard: str, intended: str) -> bool:
    """True when one could be a mis-hearing or mis-spelling of the other."""
    h, i = letters(heard), letters(intended)
    if not h or not i:
        return False
    spelling = SequenceMatcher(None, h, i).ratio()
    sound = SequenceMatcher(None, _skeleton(h), _skeleton(i)).ratio()
    return spelling >= 0.6 or (sound >= 0.8 and spelling >= 0.4)


def _repeat(a: list[_Word], old: list[_Word]) -> bool:
    """True when old, deleted by the user, is a copy of the words just before or after it: speech
    recognition wrote a phrase twice ("convert on, convert on")."""
    at = next((n for n, word in enumerate(a) if word.begin == old[0].begin), None)
    if at is None:
        return False
    said = [w.core.lower() for w in old]
    size = len(old)
    return said in ([w.core.lower() for w in a[at - size : at]], [w.core.lower() for w in a[at + size : at + 2 * size]])


def swaps(before: str, after: str) -> list[Swap]:
    """The mis-hearings the user fixed. Empty when they rewrote the text instead."""
    a, b = _words(before), _words(after)
    found, reworded = [], 0
    for old, new in _runs(a, b):
        if old and not new and _repeat(a, old):
            # Removing a repeated phrase is a fix, not a rewrite; tidy() now removes those itself.
            continue
        # A capital added to the first word of a sentence is not part of the word fixed next to it.
        if old and new and new[0].starts_sentence and old[0].core.capitalize() == new[0].core:
            old, new = old[1:], new[1:]
            if not old and not new:
                continue
        heard =" ".join(w.core for w in old)
        intended = " ".join(w.core for w in new)
        # At the start of a sentence a capital may be only punctuation, so it proves nothing.
        mid_sentence = bool(new) and not new[0].starts_sentence
        name = bool(_NAME.search(intended if mid_sentence else intended[1:]))
        if heard.lower() == intended.lower():
            if name:
                found.append(Swap(heard, intended, True))
        elif 1 <= len(old) <= _MAX_WORDS and 1 <= len(new) <= _MAX_WORDS and _alike(heard, intended):
            found.append(Swap(heard, intended, name))
        else:
            reworded += max(len(old), len(new))
    if reworded > _REWRITE_SHARE * max(len(a), 1):
        return []
    return found


def trim(before: str, after: str) -> str:
    """Drops what the user typed in front of or behind the dictated text; edits inside it stay."""
    a, b = _words(before), _words(after)
    blocks = [m for m in _matcher(a, b).get_matching_blocks() if m.size]
    if not blocks:
        return after
    first, last = blocks[0], blocks[-1]
    begin = b[first.b].begin if first.a == 0 else 0
    end = b[last.b + last.size - 1].end if last.a + last.size == len(a) else len(after)
    return after[begin:end]


def similarity(one: str, other: str) -> float:
    """Share of words two texts have in common, from 0 to 1."""
    x, y = ({w.core.lower() for w in _words(t)} for t in (one, other))
    return len(x & y) / len(x | y) if x and y else 0.0
