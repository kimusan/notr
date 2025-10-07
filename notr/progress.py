from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from rich.console import Console
from rich.status import Status
from rich.table import Table


class SyncProgress:
    """Provides spinner-based progress messaging and summary tables for sync operations."""

    def __init__(self, console: Optional[Console] = None, label: str = "Sync", spinner: bool = True):
        self.console = console or Console()
        self.label = label
        self.spinner_enabled = spinner
        self._status: Optional[Status] = None

    def set_label(self, label: str) -> None:
        self.label = label

    @contextmanager
    def step(self, message: str, *, spinner: Optional[bool] = None) -> Iterator[None]:
        self.start(message, spinner=spinner)
        try:
            yield
        finally:
            self.stop()

    def start(self, message: str, *, spinner: Optional[bool] = None) -> None:
        spinner = self.spinner_enabled if spinner is None else spinner
        text = self._format(message)
        self.stop()
        if spinner:
            self._status = self.console.status(text, spinner="dots", speed=1.0)
            self._status.__enter__()
        else:
            self.console.print(text)

    def update(self, message: str) -> None:
        text = self._format(message)
        if self._status:
            self._status.update(text)
        else:
            self.console.print(text)

    def stop(self) -> None:
        if self._status:
            self._status.__exit__(None, None, None)
            self._status = None

    def summary(
        self,
        *,
        uploaded: bool,
        downloaded: bool,
        local_changes: int,
        remote_changes: int,
        merged_notes: int,
        deleted_notes: int,
    ) -> None:
        self.stop()
        table = Table(
            title=f"{self.label} summary",
            header_style="bold magenta",
            show_header=True,
            show_lines=False,
            box=None,
        )
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold")
        table.add_row("Uploaded", "yes" if uploaded else "no")
        table.add_row("Downloaded", "yes" if downloaded else "no")
        table.add_row("Local changes applied", str(local_changes))
        table.add_row("Remote changes propagated", str(remote_changes))
        table.add_row("Notes merged", str(merged_notes))
        table.add_row("Deletions propagated", str(deleted_notes))
        self.console.print(table)

    def _format(self, message: str) -> str:
        prefix = f"{self.label}: " if self.label else ""
        return f"[cyan]{prefix}{message}[/cyan]"
