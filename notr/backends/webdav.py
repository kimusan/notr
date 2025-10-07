from __future__ import annotations

import email.utils
from http import HTTPStatus
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

from ..errors import BackendError
from .base import Backend, SyncDirection, SyncResult


class WebDAVBackend(Backend):
    """Synchronises the database via WebDAV."""

    def __init__(self, options, secret_store, secret_id):
        super().__init__(options, secret_store, secret_id)
        url = options.get("url")
        if not url:
            raise BackendError("WebDAV backend requires a 'url' option")
        self.base_url = url.rstrip("/")
        self.username = options.get("username")
        if not self.username:
            raise BackendError("WebDAV backend requires a 'username'")
        directory = options.get("directory", "/notr")
        self.remote_directory = "/" + directory.strip("/")
        self.remote_filename = options.get("filename", "notr.db")
        self.timeout = options.get("timeout", 30)

    def login(self, password: Optional[str] = None) -> None:
        if not password:
            raise BackendError("Password must be provided to login to WebDAV backend")
        self._store_password(password)

    def sync(self, db_path: Path, direction: SyncDirection = SyncDirection.BOTH) -> SyncResult:
        local_path = Path(db_path)
        remote_path = f"{self.remote_directory}/{self.remote_filename}"

        if direction == SyncDirection.PUSH:
            self.upload(local_path)
            return SyncResult(uploaded=True, message=f"Pushed local database to {remote_path}")
        if direction == SyncDirection.PULL:
            if not self.download(local_path):
                raise BackendError("Remote database not found")
            return SyncResult(downloaded=True, message="Pulled remote database")

        # BOTH
        password = self._load_password()
        if not password:
            raise BackendError("No stored WebDAV credentials. Run 'notr backend login'.")
        try:
            with self._client(password) as client:
                self._ensure_remote_directory(client)
                remote_mtime = self._remote_mtime(client)
                if remote_mtime is None:
                    self._upload(client, local_path)
                    return SyncResult(uploaded=True, message="Uploaded initial database to WebDAV")
                local_mtime = local_path.stat().st_mtime
                if remote_mtime > local_mtime:
                    self._download(client, local_path)
                    return SyncResult(downloaded=True, message="Downloaded newer remote database")
                elif local_mtime > remote_mtime:
                    self._upload(client, local_path)
                    return SyncResult(uploaded=True, message="Uploaded newer local database")
                return SyncResult(message="Remote and local databases are already in sync")
        except httpx.RequestError as exc:
            raise BackendError(
                f"Could not reach WebDAV server at {self.base_url}: {exc}"
            ) from exc

    def download(self, target: Path) -> bool:
        password = self._load_password()
        if not password:
            raise BackendError("No stored WebDAV credentials. Run 'notr backend login'.")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._client(password) as client:
                response = client.get(self._remote_file_url())
        except httpx.RequestError as exc:
            raise BackendError(
                f"Could not reach WebDAV server at {self.base_url}: {exc}"
            ) from exc
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise BackendError(
                f"Failed to download database from WebDAV "
                f"({self._status_message(response.status_code)})."
            )
        with target.open("wb") as handle:
            handle.write(response.content)
        return True

    def upload(self, source: Path) -> None:
        password = self._load_password()
        if not password:
            raise BackendError("No stored WebDAV credentials. Run 'notr backend login'.")
        source = Path(source)
        if not source.exists():
            raise BackendError(f"Local database not found at {source}")
        try:
            with self._client(password) as client:
                self._ensure_remote_directory(client)
                self._upload(client, source)
        except httpx.RequestError as exc:
            raise BackendError(
                f"Could not reach WebDAV server at {self.base_url}: {exc}"
            ) from exc

    def status(self):
        password = self._load_password()
        credentials = "stored" if password else "missing"
        remote_exists = False
        remote_size = None
        remote_mtime = None
        if password:
            try:
                with self._client(password) as client:
                    head = client.head(self._remote_file_url())
                    if head.status_code == 200:
                        remote_exists = True
                        remote_size = head.headers.get("Content-Length")
                        remote_mtime = head.headers.get("Last-Modified")
            except Exception:
                pass
        return {
            "url": self.base_url,
            "remote_path": f"{self.remote_directory}/{self.remote_filename}",
            "credentials": credentials,
            "remote_exists": remote_exists,
            "remote_size": remote_size,
            "remote_last_modified": remote_mtime,
        }

    # Internals -----------------------------------------------------------
    def _client(self, password: str) -> httpx.Client:
        auth = (self.username, password)
        return httpx.Client(auth=auth, timeout=self.timeout)

    def _remote_file_url(self) -> str:
        path = "/".join(
            quote(part, safe="")
            for part in (self.remote_directory.strip("/") + "/" + self.remote_filename).split("/")
            if part
        )
        return f"{self.base_url}/{path}"

    def _remote_directory_url(self) -> str:
        parts = [
            quote(part, safe="")
            for part in self.remote_directory.strip("/").split("/")
            if part
        ]
        if parts:
            return f"{self.base_url}/{'/'.join(parts)}"
        return self.base_url

    def _ensure_remote_directory(self, client: httpx.Client) -> None:
        parts = [p for p in self.remote_directory.strip("/").split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            url = f"{self.base_url}/{'/'.join(quote(seg, safe='') for seg in current.split('/'))}"
            response = client.request("MKCOL", url)
            if response.status_code in (201, 200, 204, 405):
                continue
            if response.status_code == 409:
                continue
            raise BackendError(
                f"Could not create remote directory '{current}' "
                f"({self._status_message(response.status_code)}). "
                "Verify that parent folders exist and your account has permission."
            )

    def _remote_mtime(self, client: httpx.Client) -> Optional[float]:
        response = client.head(self._remote_file_url())
        if response.status_code == 404:
            return None
        if response.status_code not in (200, 204):
            raise BackendError(
                f"Unable to check remote database timestamp "
                f"({self._status_message(response.status_code)})."
            )
        header = response.headers.get("Last-Modified")
        if not header:
            return None
        parsed = email.utils.parsedate_to_datetime(header)
        if parsed is None:
            return None
        return parsed.timestamp()

    def _download(self, client: httpx.Client, db_path: Path) -> None:
        response = client.get(self._remote_file_url())
        if response.status_code == 404:
            raise BackendError("Remote database not found. Run a push sync to create it.")
        if response.status_code != 200:
            raise BackendError(
                f"Failed to download database from WebDAV "
                f"({self._status_message(response.status_code)})."
            )
        temp_path = db_path.with_suffix(".tmp")
        with temp_path.open("wb") as handle:
            handle.write(response.content)
        temp_path.replace(db_path)

    def _upload(self, client: httpx.Client, db_path: Path) -> None:
        with db_path.open("rb") as handle:
            data = handle.read()
        response = client.put(self._remote_file_url(), content=data)
        if response.status_code not in (200, 201, 204):
            raise BackendError(
                f"Failed to upload database to WebDAV "
                f"({self._status_message(response.status_code)})."
            )

    def _status_message(self, status_code: int) -> str:
        try:
            phrase = HTTPStatus(status_code).phrase
        except ValueError:
            phrase = "Unknown Status"
        hints = {
            400: "request was malformed",
            401: "authentication failed (check username/password)",
            403: "access is forbidden (insufficient permissions)",
            404: "resource was not found",
            409: "conflict on the server (path might already exist or parent missing)",
            423: "resource is locked on the server",
            500: "server encountered an internal error",
        }
        hint = hints.get(status_code)
        if hint:
            return f"{status_code} {phrase} — {hint}"
        return f"{status_code} {phrase}"
