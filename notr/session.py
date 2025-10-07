from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Optional

try:
    import keyring
except Exception:  # pragma: no cover - keyring optional at runtime
    keyring = None  # type: ignore


class SessionManager:
    """Stores the decrypted master key securely for consecutive CLI invocations."""

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self._identity = hashlib.sha256(str(self.config_path).encode("utf8")).hexdigest()
        cache_dir = Path(os.environ.get("NOTR_CACHE_PATH", "~/.cache/notr")).expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._fallback_file = cache_dir / f"session-{self._identity}.json"

    @property
    def _service_name(self) -> str:
        return "notr"

    @property
    def _username(self) -> str:
        return self._identity

    def store(self, master_key: bytes) -> None:
        encoded = base64.b64encode(master_key).decode("utf8")
        if self._store_keyring(encoded):
            return
        self._store_file(encoded)

    def load(self) -> Optional[bytes]:
        encoded = self._load_keyring()
        if not encoded:
            encoded = self._load_file()
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded.encode("utf8"))
        except (ValueError, binascii.Error):  # pragma: no cover - defensive
            self.clear()
            return None

    def clear(self) -> None:
        self._clear_keyring()
        if self._fallback_file.exists():
            self._fallback_file.unlink()

    # Keyring helpers -----------------------------------------------------
    def _store_keyring(self, encoded: str) -> bool:
        if keyring is None:
            return False
        try:
            keyring.set_password(self._service_name, self._username, encoded)
            return True
        except Exception:
            return False

    def _load_keyring(self) -> Optional[str]:
        if keyring is None:
            return None
        try:
            return keyring.get_password(self._service_name, self._username)
        except Exception:
            return None

    def _clear_keyring(self) -> None:
        if keyring is None:
            return
        try:
            keyring.delete_password(self._service_name, self._username)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception:
            pass

    # Fallback file helpers ----------------------------------------------
    def _store_file(self, encoded: str) -> None:
        data = {"master_key": encoded}
        with self._fallback_file.open("w", encoding="utf8") as handle:
            json.dump(data, handle)
        try:
            self._fallback_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except PermissionError:
            pass

    def _load_file(self) -> Optional[str]:
        if not self._fallback_file.exists():
            return None
        try:
            with self._fallback_file.open("r", encoding="utf8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return None
        return data.get("master_key")
