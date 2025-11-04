from __future__ import annotations

import shutil
from pathlib import Path

from ..errors import BackendError
from .base import Backend, SyncDirection, SyncResult


class LocalBackend(Backend):
    """Synchronises the database file with a directory on the local filesystem."""

    def __init__(self, options, secret_store, secret_id, ssl_verify: bool = True):
        super().__init__(options, secret_store, secret_id, ssl_verify=ssl_verify)
        directory = options.get("directory") or "~/.local/share/notr/remote"
        self.remote_dir = Path(directory).expanduser()
        self.remote_filename = options.get("filename", "notr.db")

    def login(self, password=None):
        # No authentication required for local backend.
        return None

    def sync(self, db_path: Path, direction: SyncDirection = SyncDirection.BOTH) -> SyncResult:
        remote_path = self.remote_dir / self.remote_filename
        if direction == SyncDirection.PUSH:
            self.upload(Path(db_path))
            return SyncResult(uploaded=True, message=f"Pushed database to {remote_path}")
        if direction == SyncDirection.PULL:
            if not self.download(Path(db_path)):
                raise BackendError(f"No remote database found at {remote_path}")
            return SyncResult(downloaded=True, message=f"Pulled database from {remote_path}")
        # BOTH
        if not remote_path.exists():
            self.upload(Path(db_path))
            return SyncResult(uploaded=True, message=f"Initialized remote backup at {remote_path}")
        if remote_path.stat().st_mtime > Path(db_path).stat().st_mtime:
            self.download(Path(db_path))
            return SyncResult(downloaded=True, message=f"Restored newer remote copy from {remote_path}")
        elif Path(db_path).stat().st_mtime > remote_path.stat().st_mtime:
            self.upload(Path(db_path))
            return SyncResult(uploaded=True, message=f"Updated remote copy at {remote_path}")
        return SyncResult(message="Local and remote database are already in sync")

    def download(self, target: Path) -> bool:
        remote_path = self.remote_dir / self.remote_filename
        if not remote_path.exists():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote_path, target)
        return True

    def upload(self, source: Path) -> None:
        source = Path(source)
        if not source.exists():
            raise BackendError(f"Local database not found at {source}")
        self.remote_dir.mkdir(parents=True, exist_ok=True)
        remote_path = self.remote_dir / self.remote_filename
        shutil.copy2(source, remote_path)

    def status(self):
        remote_path = self.remote_dir / self.remote_filename
        return {
            "remote_path": str(remote_path),
            "remote_exists": remote_path.exists(),
        }
