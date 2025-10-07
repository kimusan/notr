from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Type

from ..config import BackendConfig
from ..errors import BackendError
from ..secrets import SecretStore


class SyncDirection(Enum):
    PUSH = "push"
    PULL = "pull"
    BOTH = "both"


@dataclass
class SyncResult:
    uploaded: bool = False
    downloaded: bool = False
    message: Optional[str] = None
    merged_notes: int = 0
    deleted_notes: int = 0
    local_changes: int = 0
    remote_changes: int = 0


class Backend:
    def __init__(self, options: Dict[str, Any], secret_store: SecretStore, secret_id: str):
        self.options = options
        self.secret_store = secret_store
        self.secret_id = secret_id

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def sync(self, db_path: Path, direction: SyncDirection = SyncDirection.BOTH) -> SyncResult:
        raise NotImplementedError

    def download(self, target: Path) -> bool:
        raise NotImplementedError

    def upload(self, source: Path) -> None:
        raise NotImplementedError

    def login(self, password: Optional[str] = None) -> None:
        raise NotImplementedError

    def logout(self) -> None:
        self.secret_store.delete(self.secret_id)

    def status(self) -> Dict[str, Any]:
        return {}

    def _store_password(self, password: str) -> None:
        self.secret_store.set(self.secret_id, password)

    def _load_password(self) -> Optional[str]:
        return self.secret_store.get(self.secret_id)

    def validate(self, db_path: Path) -> None:
        """Verify backend connectivity by performing a no-op round-trip."""
        temp_dir = Path(tempfile.mkdtemp(prefix="notr-validate-"))
        temp_file = temp_dir / db_path.name
        try:
            if not self.download(temp_file):
                self.upload(db_path)
        finally:
            if temp_file.exists():
                temp_file.unlink()
            if temp_dir.exists():
                temp_dir.rmdir()


def backend_secret_id(config: BackendConfig) -> str:
    options = dict(config.options)
    options.pop("secret_id", None)
    data = json.dumps(
        {"type": config.type, "options": options},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode("utf8")).hexdigest()


def create_backend(config: BackendConfig) -> Backend:
    from .local import LocalBackend
    from .webdav import WebDAVBackend

    secret_store = SecretStore(namespace="backend")
    secret_id = config.options.get("secret_id") or backend_secret_id(config)
    backend_type = config.type.lower()
    backend_map: Dict[str, Type[Backend]] = {
        "local": LocalBackend,
        "webdav": WebDAVBackend,
    }
    backend_cls = backend_map.get(backend_type)
    if not backend_cls:
        raise BackendError(f"Unsupported backend type '{config.type}'")
    instance = backend_cls(config.options, secret_store, secret_id)
    # Persist secret id for future lookups.
    config.options["secret_id"] = secret_id
    return instance
