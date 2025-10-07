from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def ensure_initialized(self) -> None:
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS notebooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    uuid TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notebook_id INTEGER NOT NULL,
                    uuid TEXT NOT NULL UNIQUE,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(notebook_id) REFERENCES notebooks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS deleted_notes (
                    uuid TEXT PRIMARY KEY,
                    notebook_uuid TEXT,
                    deleted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deleted_notebooks (
                    uuid TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notes_notebook_id ON notes (notebook_id);
                """
            )
        self._migrate_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.db_path), detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def query_single(self, query: str, parameters: Iterable) -> Optional[sqlite3.Row]:
        with self.connection() as conn:
            cursor = conn.execute(query, parameters)
            row = cursor.fetchone()
        return row

    def execute(self, query: str, parameters: Iterable = ()) -> None:
        with self.connection() as conn:
            conn.execute(query, parameters)

    def execute_with_rowid(self, query: str, parameters: Iterable = ()) -> int:
        with self.connection() as conn:
            cursor = conn.execute(query, parameters)
            return cursor.lastrowid

    def query_all(self, query: str, parameters: Iterable = ()) -> Iterable[sqlite3.Row]:
        with self.connection() as conn:
            cursor = conn.execute(query, parameters)
            rows = cursor.fetchall()
        return rows

    def set_metadata(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    def get_metadata(self, key: str) -> Optional[str]:
        row = self.query_single("SELECT value FROM metadata WHERE key = ?", (key,))
        if row:
            return row["value"]
        return None

    # Schema migrations --------------------------------------------------
    def _migrate_schema(self) -> None:
        with self.connection() as conn:
            self._ensure_notebook_uuid(conn)
            self._ensure_note_uuid(conn)
            self._ensure_deleted_tables(conn)

    def _ensure_notebook_uuid(self, conn: sqlite3.Connection) -> None:
        info = conn.execute("PRAGMA table_info(notebooks)").fetchall()
        columns = {row["name"] for row in info}
        if "uuid" not in columns:
            conn.execute("ALTER TABLE notebooks ADD COLUMN uuid TEXT")
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE notebooks ADD COLUMN updated_at TEXT")
        rows = conn.execute("SELECT id, uuid, created_at, updated_at FROM notebooks").fetchall()
        for row in rows:
            notebook_uuid = row["uuid"]
            if not notebook_uuid:
                notebook_uuid = uuid.uuid4().hex
                conn.execute(
                    "UPDATE notebooks SET uuid = ? WHERE id = ?",
                    (notebook_uuid, row["id"]),
                )
            updated_at = row["updated_at"]
            if not updated_at:
                created_at = row["created_at"] or utc_now()
                conn.execute(
                    "UPDATE notebooks SET updated_at = ? WHERE id = ?",
                    (created_at, row["id"]),
                )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notebooks_uuid ON notebooks(uuid)")

    def _ensure_note_uuid(self, conn: sqlite3.Connection) -> None:
        info = conn.execute("PRAGMA table_info(notes)").fetchall()
        columns = {row["name"] for row in info}
        if "uuid" not in columns:
            conn.execute("ALTER TABLE notes ADD COLUMN uuid TEXT")
        rows = conn.execute("SELECT id, uuid FROM notes").fetchall()
        for row in rows:
            note_uuid = row["uuid"]
            if not note_uuid:
                conn.execute(
                    "UPDATE notes SET uuid = ? WHERE id = ?",
                    (uuid.uuid4().hex, row["id"]),
                )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_uuid ON notes(uuid)")

    def _ensure_deleted_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deleted_notes (
                uuid TEXT PRIMARY KEY,
                notebook_uuid TEXT,
                deleted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deleted_notebooks (
                uuid TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL
            )
            """
        )
