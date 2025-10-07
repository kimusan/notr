from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Dict, Optional

try:
    import keyring
except Exception:  # pragma: no cover - optional dependency runtime failures
    keyring = None  # type: ignore


class SecretStore:
    """Generic secret store that prefers the system keyring and falls back to a local vault file."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        cache_dir = Path(os.environ.get("NOTR_CACHE_PATH", "~/.cache/notr")).expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._fallback_file = cache_dir / f"secrets-{self.namespace}.json"

    @property
    def _service_name(self) -> str:
        return f"notr-{self.namespace}"

    def set(self, key: str, value: str) -> None:
        if self._set_keyring(key, value):
            return
        self._set_file(key, value)

    def get(self, key: str) -> Optional[str]:
        value = self._get_keyring(key)
        if value is not None:
            return value
        return self._get_file(key)

    def delete(self, key: str) -> None:
        self._delete_keyring(key)
        data = self._load_file()
        if key in data:
            data.pop(key, None)
            self._save_file(data)

    # Keyring -------------------------------------------------------------
    def _set_keyring(self, key: str, value: str) -> bool:
        if keyring is None:
            return False
        try:
            keyring.set_password(self._service_name, key, value)
            return True
        except Exception:
            return False

    def _get_keyring(self, key: str) -> Optional[str]:
        if keyring is None:
            return None
        try:
            return keyring.get_password(self._service_name, key)
        except Exception:
            return None

    def _delete_keyring(self, key: str) -> None:
        if keyring is None:
            return
        try:
            keyring.delete_password(self._service_name, key)
        except Exception:
            pass

    # File fallback -------------------------------------------------------
    def _load_file(self) -> Dict[str, str]:
        if not self._fallback_file.exists():
            return {}
        try:
            with self._fallback_file.open("r", encoding="utf8") as handle:
                data = json.load(handle)
            return {k: v for k, v in data.items() if isinstance(v, str)}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_file(self, data: Dict[str, str]) -> None:
        with self._fallback_file.open("w", encoding="utf8") as handle:
            json.dump(data, handle)
        try:
            self._fallback_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except PermissionError:
            pass

    def _set_file(self, key: str, value: str) -> None:
        data = self._load_file()
        data[key] = value
        self._save_file(data)

    def _get_file(self, key: str) -> Optional[str]:
        data = self._load_file()
        return data.get(key)
