from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .db import DatabaseManager
from .storage import parse_timestamp


def _to_datetime(value: str) -> datetime:
    dt = parse_timestamp(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class MergeStats:
    local_changes: int = 0
    remote_changes: int = 0
    notes_merged: int = 0
    notes_deleted: int = 0
    notebooks_created: int = 0
    notebooks_updated: int = 0


class DatabaseMerger:
    """Merge two Notr SQLite databases by synchronising notebooks and notes per UUID."""

    def __init__(self, local_db: DatabaseManager, remote_db: DatabaseManager):
        self.local_db = local_db
        self.remote_db = remote_db

    def merge(self) -> MergeStats:
        stats = MergeStats()

        local_notebooks = self._fetch_notebooks(self.local_db)
        remote_notebooks = self._fetch_notebooks(self.remote_db)

        notebook_stats = self._sync_notebooks(local_notebooks, remote_notebooks)
        stats.notebooks_created += notebook_stats["created"]
        stats.local_changes += notebook_stats["local_changes"]
        stats.remote_changes += notebook_stats["remote_changes"]

        local_notes = self._fetch_notes(self.local_db)
        remote_notes = self._fetch_notes(self.remote_db)
        local_deleted = self._fetch_deleted_notes(self.local_db)
        remote_deleted = self._fetch_deleted_notes(self.remote_db)

        note_stats = self._sync_notes(
            local_notebooks,
            remote_notebooks,
            local_notes,
            remote_notes,
            local_deleted,
            remote_deleted,
        )

        stats.notes_merged = note_stats["merged"]
        stats.notes_deleted = note_stats["deleted"]
        stats.local_changes += note_stats["local_changes"]
        stats.remote_changes += note_stats["remote_changes"]

        return stats

    # Notebook helpers --------------------------------------------------
    def _fetch_notebooks(self, db: DatabaseManager) -> Dict[str, dict]:
        rows = db.query_all(
            "SELECT id, uuid, name, created_at, updated_at FROM notebooks"
        )
        return {
            row["uuid"]: {
                "id": row["id"],
                "uuid": row["uuid"],
                "name": row["name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def _load_notebook_record(self, db: DatabaseManager, nb_uuid: str) -> Optional[dict]:
        row = db.query_single(
            "SELECT id, uuid, name, created_at, updated_at FROM notebooks WHERE uuid = ?",
            (nb_uuid,),
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "uuid": row["uuid"],
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _sync_notebooks(self, local_nb: Dict[str, dict], remote_nb: Dict[str, dict]) -> Dict[str, int]:
        stats = {"created": 0, "local_changes": 0, "remote_changes": 0}
        uuids = set(local_nb) | set(remote_nb)
        for nb_uuid in uuids:
            local = local_nb.get(nb_uuid)
            remote = remote_nb.get(nb_uuid)
            if local and remote:
                local_updated = _to_datetime(local["updated_at"])
                remote_updated = _to_datetime(remote["updated_at"])
                if remote_updated > local_updated:
                    self._update_notebook(
                        self.local_db,
                        nb_uuid,
                        remote["name"],
                        remote["created_at"],
                        remote["updated_at"],
                    )
                    fresh = self._load_notebook_record(self.local_db, nb_uuid)
                    if fresh:
                        local_nb[nb_uuid] = fresh
                    stats["local_changes"] += 1
                elif local_updated > remote_updated:
                    self._update_notebook(
                        self.remote_db,
                        nb_uuid,
                        local["name"],
                        local["created_at"],
                        local["updated_at"],
                    )
                    fresh = self._load_notebook_record(self.remote_db, nb_uuid)
                    if fresh:
                        remote_nb[nb_uuid] = fresh
                    stats["remote_changes"] += 1
            elif remote and not local:
                matching = self._find_notebook_by_name(local_nb, remote["name"])
                if matching:
                    self._reassign_notebook_uuid(self.remote_db, remote["uuid"], matching["uuid"])
                    remote_nb.pop(remote["uuid"], None)
                    refreshed = self._load_notebook_record(self.remote_db, matching["uuid"])
                    if refreshed:
                        remote_nb[matching["uuid"]] = refreshed
                    stats["remote_changes"] += 1
                    continue
                self._insert_notebook(
                    self.local_db,
                    remote["uuid"],
                    remote["name"],
                    remote["created_at"],
                    remote["updated_at"],
                )
                fresh = self._load_notebook_record(self.local_db, nb_uuid)
                if fresh:
                    local_nb[nb_uuid] = fresh
                stats["created"] += 1
                stats["local_changes"] += 1
            elif local and not remote:
                matching = self._find_notebook_by_name(remote_nb, local["name"])
                if matching:
                    self._reassign_notebook_uuid(self.local_db, local["uuid"], matching["uuid"])
                    local_nb.pop(local["uuid"], None)
                    refreshed = self._load_notebook_record(self.local_db, matching["uuid"])
                    if refreshed:
                        local_nb[matching["uuid"]] = refreshed
                    stats["local_changes"] += 1
                    continue
                self._insert_notebook(
                    self.remote_db,
                    local["uuid"],
                    local["name"],
                    local["created_at"],
                    local["updated_at"],
                )
                fresh = self._load_notebook_record(self.remote_db, nb_uuid)
                if fresh:
                    remote_nb[nb_uuid] = fresh
                stats["remote_changes"] += 1
        return stats

    def _insert_notebook(
        self,
        db: DatabaseManager,
        uuid: str,
        name: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        db.execute(
            """
            INSERT INTO notebooks (name, created_at, updated_at, uuid)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                name = excluded.name,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (name, created_at, updated_at, uuid),
        )

    def _update_notebook(
        self,
        db: DatabaseManager,
        uuid: str,
        name: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        db.execute(
            """
            UPDATE notebooks
            SET name = ?, created_at = ?, updated_at = ?
            WHERE uuid = ?
            """,
            (name, created_at, updated_at, uuid),
        )

    def _find_notebook_by_name(self, notebooks: Dict[str, dict], name: str) -> Optional[dict]:
        for data in notebooks.values():
            if data["name"] == name:
                return data
        return None

    def _reassign_notebook_uuid(self, db: DatabaseManager, old_uuid: str, new_uuid: str) -> None:
        if old_uuid == new_uuid:
            return
        db.execute("UPDATE notebooks SET uuid = ? WHERE uuid = ?", (new_uuid, old_uuid))
        db.execute(
            "UPDATE deleted_notes SET notebook_uuid = ? WHERE notebook_uuid = ?",
            (new_uuid, old_uuid),
        )
        db.execute("DELETE FROM deleted_notebooks WHERE uuid = ?", (new_uuid,))
        db.execute(
            "UPDATE deleted_notebooks SET uuid = ? WHERE uuid = ?",
            (new_uuid, old_uuid),
        )

    # Note helpers ------------------------------------------------------
    def _fetch_notes(self, db: DatabaseManager) -> Dict[str, dict]:
        rows = db.query_all(
            """
            SELECT notes.id,
                   notes.uuid,
                   notes.notebook_id,
                   notes.nonce,
                   notes.ciphertext,
                   notes.created_at,
                   notes.updated_at,
                   notebooks.uuid AS notebook_uuid
            FROM notes
            JOIN notebooks ON notebooks.id = notes.notebook_id
            """
        )
        return {
            row["uuid"]: {
                "id": row["id"],
                "uuid": row["uuid"],
                "notebook_id": row["notebook_id"],
                "notebook_uuid": row["notebook_uuid"],
                "nonce": row["nonce"],
                "ciphertext": row["ciphertext"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def _fetch_deleted_notes(self, db: DatabaseManager) -> Dict[str, dict]:
        rows = db.query_all("SELECT uuid, notebook_uuid, deleted_at FROM deleted_notes")
        return {
            row["uuid"]: {
                "uuid": row["uuid"],
                "notebook_uuid": row["notebook_uuid"],
                "deleted_at": row["deleted_at"],
            }
            for row in rows
        }

    def _sync_notes(
        self,
        local_notebooks: Dict[str, dict],
        remote_notebooks: Dict[str, dict],
        local_notes: Dict[str, dict],
        remote_notes: Dict[str, dict],
        local_deleted: Dict[str, dict],
        remote_deleted: Dict[str, dict],
    ) -> Dict[str, int]:
        stats = {"merged": 0, "deleted": 0, "local_changes": 0, "remote_changes": 0}
        uuids = set(local_notes) | set(remote_notes) | set(local_deleted) | set(remote_deleted)

        for note_uuid in uuids:
            local_note = local_notes.get(note_uuid)
            remote_note = remote_notes.get(note_uuid)
            local_del = local_deleted.get(note_uuid)
            remote_del = remote_deleted.get(note_uuid)

            latest_delete = None
            if local_del:
                latest_delete = _to_datetime(local_del["deleted_at"])
            if remote_del:
                deletion = _to_datetime(remote_del["deleted_at"])
                if not latest_delete or deletion > latest_delete:
                    latest_delete = deletion

            if latest_delete:
                # Propagate deletion if the note wasn't updated afterwards.
                if local_note and _to_datetime(local_note["updated_at"]) <= latest_delete:
                    self._delete_note(self.local_db, note_uuid)
                    stats["local_changes"] += 1
                if remote_note and _to_datetime(remote_note["updated_at"]) <= latest_delete:
                    self._delete_note(self.remote_db, note_uuid)
                    stats["remote_changes"] += 1
                if local_del is None:
                    self._record_deletion(self.local_db, note_uuid, remote_del)
                if remote_del is None:
                    self._record_deletion(self.remote_db, note_uuid, local_del)
                stats["deleted"] += 1
                continue

            if local_note and remote_note:
                local_updated = _to_datetime(local_note["updated_at"])
                remote_updated = _to_datetime(remote_note["updated_at"])
                if remote_updated > local_updated:
                    self._upsert_note(self.local_db, remote_note)
                    stats["local_changes"] += 1
                    stats["merged"] += 1
                elif local_updated > remote_updated:
                    self._upsert_note(self.remote_db, local_note)
                    stats["remote_changes"] += 1
                    stats["merged"] += 1
            elif remote_note and not local_note:
                self._upsert_note(self.local_db, remote_note)
                stats["local_changes"] += 1
                stats["merged"] += 1
            elif local_note and not remote_note:
                self._upsert_note(self.remote_db, local_note)
                stats["remote_changes"] += 1
                stats["merged"] += 1

        return stats

    def _upsert_note(self, db: DatabaseManager, note: dict) -> None:
        notebook_row = db.query_single(
            "SELECT id FROM notebooks WHERE uuid = ?",
            (note["notebook_uuid"],),
        )
        if not notebook_row:
            # Notebook should have been created already; skip if missing.
            return
        notebook_id = notebook_row["id"]
        db.execute(
            """
            INSERT INTO notes (notebook_id, uuid, nonce, ciphertext, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                notebook_id = excluded.notebook_id,
                nonce = excluded.nonce,
                ciphertext = excluded.ciphertext,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                notebook_id,
                note["uuid"],
                note["nonce"],
                note["ciphertext"],
                note["created_at"],
                note["updated_at"],
            ),
        )
        # Clear deletion tombstone if present.
        db.execute("DELETE FROM deleted_notes WHERE uuid = ?", (note["uuid"],))

    def _delete_note(self, db: DatabaseManager, note_uuid: str) -> None:
        db.execute("DELETE FROM notes WHERE uuid = ?", (note_uuid,))

    def _record_deletion(self, db: DatabaseManager, note_uuid: str, deletion: Optional[dict]) -> None:
        if not deletion:
            return
        db.execute(
            """
            INSERT INTO deleted_notes (uuid, notebook_uuid, deleted_at)
            VALUES (?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                notebook_uuid = excluded.notebook_uuid,
                deleted_at = excluded.deleted_at
            """,
            (note_uuid, deletion.get("notebook_uuid"), deletion["deleted_at"]),
        )
