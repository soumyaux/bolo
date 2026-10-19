"""Loads the settings and the API keys, and saves the settings window's changes.

The shipped defaults are config.default.toml in the repo. Each user's own settings live in
~/Library/Application Support/Bolo/config.toml and win over the defaults, so a git pull never
touches them. Keys come from .env in that folder, or from the repo's .env while developing."""

import os
import shutil
import tomllib
from pathlib import Path

import tomlkit
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "config.default.toml"
USER_DIR = Path.home() / "Library/Application Support/Bolo"
PATH = USER_DIR / "config.toml"
ENV = USER_DIR / ".env"
# Tables that are lists of the user's own entries: the user's table replaces the default one
# whole, so an entry removed in Settings stays removed.
_WHOLE = {("styles", "apps"), ("styles", "sites"), ("styles", "titles")}


def merge(defaults: dict, user: dict, at: tuple[str, ...] = ()) -> dict:
    """The defaults with the user's values on top, table by table."""
    merged = dict(defaults)
    for name, value in user.items():
        here = at + (name,)
        if isinstance(value, dict) and isinstance(merged.get(name), dict) and here not in _WHOLE:
            merged[name] = merge(merged[name], value, here)
        else:
            merged[name] = value
    return merged


def load(path: Path | None = None, defaults: Path = DEFAULTS) -> dict:
    # The first file loaded wins, so the user's key comes before a developer's repo .env.
    load_dotenv(ENV)
    load_dotenv(ROOT / ".env")
    with open(defaults, "rb") as f:
        cfg = tomllib.load(f)
    path = path or PATH
    if path.exists():
        with open(path, "rb") as f:
            cfg = merge(cfg, tomllib.load(f))
    return cfg


def key(name: str) -> str | None:
    """Returns the key from the environment, or None when it is missing or blank."""
    value = os.environ.get(name, "").strip()
    return value or None


def _plain(item):
    return item.unwrap() if hasattr(item, "unwrap") else item


def _item(value, old=None):
    """A TOML value that keeps the comment at the end of the line it replaces."""
    if isinstance(value, list):
        new = tomlkit.array()
        new.extend(value)
        if value:
            # One per line, as the file is written by hand.
            new.multiline(True)
    else:
        new = tomlkit.item(value)
    if old is not None and hasattr(old, "trivia") and old.trivia.comment:
        new.trivia.comment_ws, new.trivia.comment = old.trivia.comment_ws, old.trivia.comment
    return new


def _set(table, name: str, value) -> None:
    old = table.get(name)
    if isinstance(value, dict):
        # A table such as [styles.apps]: change only the entries that differ, so comments stay.
        if old is None:
            table[name] = old = tomlkit.table()
        wanted = {key: entry[0] if isinstance(entry, tuple) else entry for key, entry in value.items()}
        for key in [key for key in old if key not in wanted]:
            del old[key]
        for key, entry in value.items():
            if key in old:
                if _plain(old[key]) != wanted[key]:
                    old[key] = _item(wanted[key], old[key])
            else:
                # (value, note): the note becomes a comment, such as the app's name.
                item = _item(wanted[key])
                if isinstance(entry, tuple) and entry[1]:
                    item.comment(entry[1])
                old.add(key, item)
        return
    if old is not None and _plain(old) == value:
        return
    table[name] = _item(value, old)


def save(changes: dict[tuple[str, ...], object], path: Path | None = None) -> None:
    """Writes changes, such as {("hotkey", "push_to_talk"): "fn"}, into the user's config.toml. The
    file is read again first, so edits made by hand since Bolo started are kept, and so are all
    comments. The first save starts the file as a copy of the defaults, comments included."""
    path = path or PATH
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULTS, path)
        path.chmod(0o600)
    doc = tomlkit.parse(path.read_text())
    for keys, value in changes.items():
        table = doc
        for name in keys[:-1]:
            if name not in table:
                table[name] = tomlkit.table()
            table = table[name]
        _set(table, keys[-1], value)
    # Written whole to a new file and swapped in, so a crash never leaves half a config.
    part = path.with_name(path.name + ".saving")
    part.write_text(tomlkit.dumps(doc))
    part.chmod(path.stat().st_mode & 0o777)
    os.replace(part, path)
