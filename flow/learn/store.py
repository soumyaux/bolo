"""The learning store: recent dictations, learned words and style examples, in one SQLite file."""

import sqlite3
import threading
import time
from pathlib import Path

PATH = Path.home() / "Library/Application Support/Bolo/learning.db"
# Dictated text is only needed to fix recent ones, so older rows are dropped.
_KEEP_DICTATIONS = 200
# Examples are picked by likeness to each new dictation, so a larger pool finds closer ones.
_KEEP_EXAMPLES = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
    id INTEGER PRIMARY KEY, time REAL, app TEXT, raw TEXT, pasted TEXT, corrected TEXT, mode TEXT);
CREATE TABLE IF NOT EXISTS vocabulary (
    heard TEXT, intended TEXT, count INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
    PRIMARY KEY (heard, intended));
CREATE TABLE IF NOT EXISTS examples (
    id INTEGER PRIMARY KEY, time REAL, raw TEXT, final TEXT, app TEXT);
"""


class Store:
    def __init__(self, path: Path = PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Dictation, the fix window and the edit watcher each use it from their own thread.
        self._db = sqlite3.connect(path, check_same_thread=False)
        # It holds dictated text, so only this user may read it.
        path.chmod(0o600)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            # Files made before prompt mode have no mode column.
            columns = {row["name"] for row in self._db.execute("PRAGMA table_info(dictations)")}
            if "mode" not in columns:
                self._db.execute("ALTER TABLE dictations ADD COLUMN mode TEXT")

    def _run(self, sql: str, *args) -> list[sqlite3.Row]:
        with self._lock, self._db:
            return self._db.execute(sql, args).fetchall()

    def add_dictation(self, app: str, raw: str, pasted: str, mode: str = "") -> int:
        with self._lock, self._db:
            row = self._db.execute(
                "INSERT INTO dictations (time, app, raw, pasted, mode) VALUES (?, ?, ?, ?, ?)",
                (time.time(), app, raw, pasted, mode),
            )
            self._db.execute(
                "DELETE FROM dictations WHERE id <= ?", (row.lastrowid - _KEEP_DICTATIONS,)
            )
            return row.lastrowid

    def dictation(self, dictation_id: int | None = None) -> sqlite3.Row | None:
        """The dictation with this id, or the most recent one."""
        if dictation_id is None:
            rows = self._run("SELECT * FROM dictations ORDER BY id DESC LIMIT 1")
        else:
            rows = self._run("SELECT * FROM dictations WHERE id = ?", dictation_id)
        return rows[0] if rows else None

    def set_corrected(self, dictation_id: int, corrected: str) -> None:
        self._run("UPDATE dictations SET corrected = ? WHERE id = ?", corrected, dictation_id)

    def see_swap(self, heard: str, intended: str, weight: int = 1) -> None:
        self._run(
            "INSERT INTO vocabulary (heard, intended, count) VALUES (?, ?, ?) "
            "ON CONFLICT (heard, intended) DO UPDATE SET count = count + excluded.count",
            heard.lower(), intended, weight,
        )

    def vocabulary(self) -> list[sqlite3.Row]:
        return self._run("SELECT * FROM vocabulary ORDER BY heard, count DESC")

    def set_enabled(self, heard: str, intended: str, enabled: bool) -> None:
        self._run(
            "UPDATE vocabulary SET enabled = ? WHERE heard = ? AND intended = ?",
            int(enabled), heard, intended,
        )

    def delete_swap(self, heard: str, intended: str) -> None:
        self._run("DELETE FROM vocabulary WHERE heard = ? AND intended = ?", heard, intended)

    def add_example(self, raw: str, final: str, app: str) -> None:
        with self._lock, self._db:
            # Fixing the same dictation again replaces its example.
            self._db.execute("DELETE FROM examples WHERE raw = ?", (raw,))
            row = self._db.execute(
                "INSERT INTO examples (time, raw, final, app) VALUES (?, ?, ?, ?)",
                (time.time(), raw, final, app),
            )
            self._db.execute("DELETE FROM examples WHERE id <= ?", (row.lastrowid - _KEEP_EXAMPLES,))

    def examples(self) -> list[sqlite3.Row]:
        return self._run("SELECT raw, final, app FROM examples ORDER BY id DESC LIMIT ?", _KEEP_EXAMPLES)
