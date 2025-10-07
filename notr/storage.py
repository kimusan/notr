from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import List, Optional
import uuid

from rapidfuzz import fuzz, process

from .crypto import CryptoManager
from .db import DatabaseManager, utc_now
from .models import Note, NotePayload, Notebook


def parse_timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class NoteStore:
    def __init__(self, db: DatabaseManager, crypto: CryptoManager):
        self.db = db
        self.crypto = crypto

    # Notebooks -----------------------------------------------------------
    def list_notebooks(self) -> List[Notebook]:
        rows = self.db.query_all(
            "SELECT id, name, created_at, updated_at, uuid FROM notebooks ORDER BY name ASC"
        )
        return [
            Notebook(
                id=row["id"],
                uuid=row["uuid"],
                name=row["name"],
                created_at=parse_timestamp(row["created_at"]),
                updated_at=parse_timestamp(row["updated_at"]),
            )
            for row in rows
        ]

    def notebook_counts(self) -> dict[str, int]:
        rows = self.db.query_all(
            """
            SELECT notebooks.name AS name, COUNT(notes.id) AS total
            FROM notebooks
            LEFT JOIN notes ON notes.notebook_id = notebooks.id
            GROUP BY notebooks.uuid
            """
        )
        return {row["name"]: row["total"] for row in rows}

    def ensure_notebook(self, name: str) -> Notebook:
        row = self.db.query_single(
            "SELECT id, name, created_at, updated_at, uuid FROM notebooks WHERE name = ?",
            (name,),
        )
        if row:
            return Notebook(
                id=row["id"],
                uuid=row["uuid"],
                name=row["name"],
                created_at=parse_timestamp(row["created_at"]),
                updated_at=parse_timestamp(row["updated_at"]),
            )
        created_at = utc_now()
        notebook_uuid = uuid.uuid4().hex
        notebook_id = self.db.execute_with_rowid(
            "INSERT INTO notebooks (name, created_at, updated_at, uuid) VALUES (?, ?, ?, ?)",
            (name, created_at, created_at, notebook_uuid),
        )
        return Notebook(
            id=notebook_id,
            uuid=notebook_uuid,
            name=name,
            created_at=parse_timestamp(created_at),
            updated_at=parse_timestamp(created_at),
        )

    def rename_notebook(self, old_name: str, new_name: str) -> None:
        if old_name == new_name:
            return
        existing = self.db.query_single(
            "SELECT id FROM notebooks WHERE name = ?", (old_name,)
        )
        if not existing:
            raise ValueError(f"Notebook '{old_name}' does not exist")
        conflict = self.db.query_single("SELECT id FROM notebooks WHERE name = ?", (new_name,))
        if conflict:
            raise ValueError(f"Notebook '{new_name}' already exists")
        self.db.execute(
            "UPDATE notebooks SET name = ?, updated_at = ? WHERE id = ?",
            (new_name, utc_now(), existing["id"]),
        )

    # Notes ---------------------------------------------------------------
    def list_notes(self, master_key: bytes, notebook_name: Optional[str] = None) -> List[Note]:
        if notebook_name:
            rows = self.db.query_all(
                """
                SELECT notes.id,
                       notes.uuid,
                       notes.nonce,
                       notes.ciphertext,
                       notes.created_at,
                       notes.updated_at,
                       notebooks.id as notebook_id,
                       notebooks.uuid as notebook_uuid,
                       notebooks.name as notebook_name
                FROM notes
                JOIN notebooks ON notebooks.id = notes.notebook_id
                WHERE notebooks.name = ?
                ORDER BY notes.updated_at DESC
                """,
                (notebook_name,),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT notes.id,
                       notes.uuid,
                       notes.nonce,
                       notes.ciphertext,
                       notes.created_at,
                       notes.updated_at,
                       notebooks.id as notebook_id,
                       notebooks.uuid as notebook_uuid,
                       notebooks.name as notebook_name
                FROM notes
                JOIN notebooks ON notebooks.id = notes.notebook_id
                ORDER BY notes.updated_at DESC
                """
            )
        return [self._row_to_note(row, master_key) for row in rows]

    def get_note(self, master_key: bytes, notebook_name: str, note_id: int) -> Note:
        row = self.db.query_single(
            """
            SELECT notes.id,
                   notes.uuid,
                   notes.nonce,
                   notes.ciphertext,
                   notes.created_at,
                   notes.updated_at,
                   notebooks.id as notebook_id,
                   notebooks.uuid as notebook_uuid,
                   notebooks.name as notebook_name
            FROM notes
            JOIN notebooks ON notebooks.id = notes.notebook_id
            WHERE notebooks.name = ? AND notes.id = ?
            """,
            (notebook_name, note_id),
        )
        if not row:
            raise ValueError(f"Note {note_id} not found in notebook '{notebook_name}'")
        return self._row_to_note(row, master_key)

    def create_note(
        self,
        master_key: bytes,
        notebook_name: str,
        payload: NotePayload,
    ) -> Note:
        notebook = self.ensure_notebook(notebook_name)
        nonce, ciphertext = self.crypto.encrypt_note(master_key, asdict(payload))
        created_at = utc_now()
        note_uuid = uuid.uuid4().hex
        note_id = self.db.execute_with_rowid(
            """
            INSERT INTO notes (notebook_id, uuid, nonce, ciphertext, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (notebook.id, note_uuid, nonce, ciphertext, created_at, created_at),
        )
        return self.get_note(master_key, notebook.name, note_id)

    def update_note(
        self,
        master_key: bytes,
        notebook_name: str,
        note_id: int,
        payload: NotePayload,
    ) -> Note:
        note = self.get_note(master_key, notebook_name, note_id)
        nonce, ciphertext = self.crypto.encrypt_note(master_key, asdict(payload))
        updated_at = utc_now()
        self.db.execute(
            """
            UPDATE notes
            SET nonce = ?, ciphertext = ?, updated_at = ?
            WHERE id = ?
            """,
            (nonce, ciphertext, updated_at, note.id),
        )
        return self.get_note(master_key, notebook_name, note_id)

    def delete_note(self, notebook_name: str, note_id: int) -> None:
        existing = self.db.query_single(
            """
            SELECT notes.id, notes.uuid, notebooks.uuid AS notebook_uuid
            FROM notes
            JOIN notebooks ON notebooks.id = notes.notebook_id
            WHERE notebooks.name = ? AND notes.id = ?
            """,
            (notebook_name, note_id),
        )
        if not existing:
            raise ValueError(f"Note {note_id} not found in notebook '{notebook_name}'")
        self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.db.execute(
            """
            INSERT INTO deleted_notes (uuid, notebook_uuid, deleted_at)
            VALUES (?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET deleted_at=excluded.deleted_at,
                                          notebook_uuid=excluded.notebook_uuid
            """,
            (existing["uuid"], existing["notebook_uuid"], utc_now()),
        )

    def move_note(self, master_key: bytes, note_id: int, from_notebook: str, to_notebook: str) -> Note:
        if from_notebook == to_notebook:
            raise ValueError("Source and destination notebooks are the same")
        note = self.get_note(master_key, from_notebook, note_id)
        target_notebook = self.ensure_notebook(to_notebook)
        self.db.execute(
            "UPDATE notes SET notebook_id = ?, updated_at = ? WHERE id = ?",
            (target_notebook.id, utc_now(), note.id),
        )
        return self.get_note(master_key, target_notebook.name, note.id)

    def search_notes(
        self,
        master_key: bytes,
        query: str,
        notebook_name: Optional[str] = None,
    ) -> List[Note]:
        notes = self.list_notes(master_key, notebook_name)
        query_lower = query.lower()
        results = []
        for note in notes:
            if query_lower in note.payload.title.lower() or query_lower in note.payload.body.lower():
                results.append(note)
        return results

    def fuzzy_find(
        self,
        master_key: bytes,
        query: str,
        notebook_name: Optional[str] = None,
        limit: int = 10,
    ) -> List[Note]:
        notes = self.list_notes(master_key, notebook_name)
        choices = {
            note.id: f"{note.payload.title}\n{note.payload.body}"
            for note in notes
        }
        scored = process.extract(
            query,
            choices,
            scorer=fuzz.WRatio,
            limit=limit,
        )
        best_ids = [match[2] for match in scored if match[1] > 40]
        id_lookup = {note.id: note for note in notes}
        return [id_lookup[note_id] for note_id in best_ids if note_id in id_lookup]

    # Internal helpers ----------------------------------------------------
    def _row_to_note(self, row, master_key: bytes) -> Note:
        payload_dict = self.crypto.decrypt_note(master_key, row["nonce"], row["ciphertext"])
        payload = NotePayload(
            title=payload_dict.get("title", ""),
            body=payload_dict.get("body", ""),
            metadata=payload_dict.get("metadata"),
        )
        return Note(
            id=row["id"],
            uuid=row["uuid"],
            notebook_id=row["notebook_id"],
            notebook_uuid=row["notebook_uuid"],
            notebook_name=row["notebook_name"],
            payload=payload,
            created_at=parse_timestamp(row["created_at"]),
            updated_at=parse_timestamp(row["updated_at"]),
        )
