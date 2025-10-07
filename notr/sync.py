from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .merge import DatabaseMerger
from .progress import SyncProgress

from .backends.base import Backend, SyncDirection, SyncResult
from .db import DatabaseManager
from .errors import BackendError


def current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


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

        try:
            self.progress.start("Connecting to backend...")
            remote_exists = self.backend.download(remote_temp)
            if direction == SyncDirection.PULL and not remote_exists:
                raise BackendError("Remote database not found for pull sync")

            self.progress.update("Preparing merge...")
            remote_db = DatabaseManager(remote_temp)
            remote_db.ensure_initialized()

            self.progress.update("Merging changes...")

            merger = DatabaseMerger(self.db, remote_db)
            stats = merger.merge()

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
