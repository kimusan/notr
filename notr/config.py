from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ValidationError, validator


DEFAULT_CONFIG_PATH = Path(
    os.environ.get("NOTR_CONFIG_PATH", "~/.config/notr/config.json")
).expanduser()
DEFAULT_DB_PATH = Path(
    os.environ.get("NOTR_DB_PATH", "~/.local/share/notr/notr.db")
).expanduser()


class BackendConfig(BaseModel):
    type: str
    options: Dict[str, Any] = Field(default_factory=dict)


class EncryptionConfig(BaseModel):
    kdf_salt: str
    kdf_iterations: int = Field(default=400_000, ge=100_000)
    enc_master_key: str
    master_key_nonce: str


class OptionsConfig(BaseModel):
    editor: Optional[str] = None
    viewer: Optional[str] = None
    show_timestamps: bool = True
    autosync: bool = False
    conflict_strategy: str = Field(default="ask")

    @validator("conflict_strategy")
    def _validate_strategy(cls, value: str) -> str:
        allowed = {"ask", "local", "remote"}
        if value not in allowed:
            raise ValueError(f"conflict_strategy must be one of {', '.join(sorted(allowed))}")
        return value


class NotrConfig(BaseModel):
    version: int = 1
    db_path: str = Field(default=str(DEFAULT_DB_PATH))
    backend: BackendConfig
    encryption: EncryptionConfig
    options: OptionsConfig = Field(default_factory=OptionsConfig)


class ConfigManager:
    """Handles reading and writing the JSON configuration."""

    def __init__(self, path: Path = DEFAULT_CONFIG_PATH):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> NotrConfig:
        try:
            with self.path.open("r", encoding="utf8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Config file not found at {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Config file at {self.path} is not valid JSON") from exc
        try:
            config = NotrConfig.parse_obj(data)
        except ValidationError as exc:
            raise RuntimeError(f"Config file at {self.path} is invalid: {exc}") from exc
        return config

    def save(self, config: NotrConfig) -> None:
        data = json.dumps(config.dict(), indent=2, sort_keys=True)
        self._ensure_directory()
        temp_path = self.path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf8") as handle:
            handle.write(data)
        temp_path.replace(self.path)
        self._set_permissions()

    def upsert(
        self,
        *,
        backend: BackendConfig,
        encryption: EncryptionConfig,
        db_path: Optional[Path] = None,
        options: Optional[OptionsConfig] = None,
    ) -> NotrConfig:
        merged_options = options or OptionsConfig()
        target_db = db_path or DEFAULT_DB_PATH
        config = NotrConfig(
            backend=backend,
            encryption=encryption,
            options=merged_options,
            db_path=str(target_db),
        )
        self.save(config)
        return config

    def _ensure_directory(self) -> None:
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _set_permissions(self) -> None:
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except PermissionError:
            # Non-posix filesystems might not support chmod; fail silently.
            pass
