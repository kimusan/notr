from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .merge import DatabaseMerger
from .progress import SyncProgress

from .backends.base import Backend, SyncDirection, SyncResult
from .db import DatabaseManager
from .errors import BackendError


def current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()



DEBUG_SYNC = bool(os.environ.get("NOTR_DEBUG_SYNC"))


class SyncService:
    def __init__(self, db: DatabaseManager, backend: Backend, progress: SyncProgress | None = None):
        self.db = db
        self.backend = backend
        self.progress = progress or SyncProgress()

    def sync(self, direction: SyncDirection = SyncDirection.BOTH, context: str = "Sync") -> SyncResult:
        local_path = self.db.db_path
        temp_dir = Path(tempfile.mkdtemp(prefix="notr-sync-"))
        remote_temp = temp_dir / local_path.name
        remote_exists = False
        previous_label = self.progress.label
        self.progress.set_label(context)

        local_digest_before = self._file_digest(local_path)

        try:
            self.progress.start("Connecting to backend...")
            remote_exists = self.backend.download(remote_temp)
            if direction == SyncDirection.PULL and not remote_exists:
                raise BackendError("Remote database not found for pull sync")

            self.progress.update(f"Preparing merge (remote path: {remote_temp})...")
            remote_db = DatabaseManager(remote_temp)
            remote_db.ensure_initialized()
            self._debug_summary("Remote before merge", remote_db)
            self._debug_summary("Local before merge", self.db)

            self.progress.update("Merging changes...")
            self.progress.update(
                f"Local db at {local_path} (size={local_path.stat().st_size} bytes);"
                f" remote db size={remote_temp.stat().st_size} bytes"
            )

            merger = DatabaseMerger(self.db, remote_db)
            stats = merger.merge()
            self._debug_summary("Local after merge", self.db)
            self._debug_summary("Remote after merge", remote_db)

            remote_digest = self._file_digest(remote_temp) if remote_exists else None

            # Ensure both databases have flushed WAL contents before any uploads/readback
            self.db.checkpoint()
            remote_db.checkpoint()

            uploaded = False
            downloaded = stats.local_changes > 0

            if stats.local_changes:
                self.progress.update(
                    f"Applied {stats.local_changes} change{'s' if stats.local_changes != 1 else ''} locally"
                )

            # Upload back if requested.
            should_upload = direction in (SyncDirection.PUSH, SyncDirection.BOTH)
            remote_changed = stats.remote_changes > 0 or not remote_exists
            if should_upload and (remote_changed or direction == SyncDirection.PUSH):
                self.progress.update("Uploading merged database...")
                self.backend.upload(remote_temp)
                uploaded = True

            if (
                not downloaded
                and direction in (SyncDirection.PULL, SyncDirection.BOTH)
                and remote_exists
                and remote_digest is not None
                and remote_digest != local_digest_before
            ):
                self.progress.update("Applying remote snapshot...")
                self.db.replace_with(remote_temp)
                self.db.checkpoint()
                self._debug_summary("Local after applying remote snapshot", self.db)
                downloaded = True

            self.db.set_metadata("last_sync_at", current_timestamp())
            if uploaded:
                self.db.set_metadata("last_sync_direction", "upload")
            elif downloaded:
                self.db.set_metadata("last_sync_direction", "download")
            else:
                self.db.set_metadata("last_sync_direction", "noop")

            message = self._format_message(stats, uploaded, downloaded)
            self.progress.summary(
                uploaded=uploaded,
                downloaded=downloaded,
                local_changes=stats.local_changes,
                remote_changes=stats.remote_changes,
                merged_notes=stats.notes_merged,
                deleted_notes=stats.notes_deleted,
            )
            return SyncResult(
                uploaded=uploaded,
                downloaded=downloaded,
                message=message,
                merged_notes=stats.notes_merged,
                deleted_notes=stats.notes_deleted,
                local_changes=stats.local_changes,
                remote_changes=stats.remote_changes,
            )
        finally:
            self.progress.stop()
            self.progress.set_label(previous_label)
            if remote_temp.exists():
                remote_temp.unlink()
            if temp_dir.exists():
                try:
                    temp_dir.rmdir()
                except OSError:
                    pass

    @staticmethod

    @staticmethod
    def _debug_summary(label: str, db: DatabaseManager) -> None:
        if not DEBUG_SYNC:
            return
        try:
            notebooks = list(db.query_all("SELECT uuid, name, updated_at FROM notebooks"))
            notes = list(
                db.query_all(
                    (
                        "SELECT notes.uuid, notes.updated_at, notebooks.name AS notebook_name "
                        "FROM notes "
                        "JOIN notebooks ON notebooks.id = notes.notebook_id"
                    )
                )
            )
            print(f"[debug] {label}: {len(notebooks)} notebooks, {len(notes)} notes")
            if notebooks:
                latest_nb = max(notebooks, key=lambda row: row["updated_at"])
                print(
                    f"[debug] {label}: latest notebook '{latest_nb['name']}' updated {latest_nb['updated_at']}"
                )
            if notes:
                latest_note = max(notes, key=lambda row: row["updated_at"])
                print(
                    f"[debug] {label}: latest note in '{latest_note['notebook_name']}' updated {latest_note['updated_at']}"
                )
        except Exception as exc:
            print(f"[debug] unable to summarise {label}: {exc}")

    @staticmethod
    def _file_digest(path: Path) -> Optional[str]:
        if not path.exists():
            return None
        sha = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
        return sha.hexdigest()

    @staticmethod
    def _format_message(stats, uploaded: bool, downloaded: bool) -> str:
        parts = []
        if uploaded:
            parts.append("uploaded changes")
        if downloaded:
            parts.append("downloaded changes")
        if stats.notes_merged:
            parts.append(f"merged {stats.notes_merged} notes")
        if stats.notes_deleted:
            parts.append(f"propagated {stats.notes_deleted} deletions")
        if not parts:
            return "No changes detected"
        return ", ".join(parts).capitalize()
