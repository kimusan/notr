from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional

import click

try:
    from click.shell_completion import CompletionItem, shell_complete
except ImportError:  # pragma: no cover - older Click versions
    CompletionItem = None  # type: ignore
    shell_complete = None  # type: ignore
import rich_click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .backends import SyncDirection, create_backend
from .config import BackendConfig, ConfigManager, DEFAULT_CONFIG_PATH, NotrConfig, OptionsConfig
from .crypto import CryptoManager
from .db import DatabaseManager
from .errors import AuthenticationError, BackendError, NotrError
from .models import NotePayload
from .progress import SyncProgress
from .session import SessionManager
from .storage import NoteStore
from .sync import SyncService


rich_click.USE_MARKDOWN = True
rich_click.OPTION_GROUPS = {}
rich_click.SHOW_ARGUMENTS = True
rich_click.SHOW_OPTION_TABLE = True


console = Console()


class CLIState:
    def __init__(self, config_path: Optional[str] = None):
        path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
        self.config_manager = ConfigManager(path)
        self.config: Optional[NotrConfig] = None
        self.db: Optional[DatabaseManager] = None
        self.crypto: Optional[CryptoManager] = None
        self.session: Optional[SessionManager] = None
        self.note_store: Optional[NoteStore] = None
        self.sync_service: Optional[SyncService] = None
        self._backend = None
        self._master_key: Optional[bytes] = None
        self._loaded = False

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self.config_manager.exists():
            raise NotrError("Notr is not initialized. Run 'notr init' first.")
        config = self.config_manager.load()
        db_path = Path(config.db_path).expanduser()
        db = DatabaseManager(db_path)
        db.ensure_initialized()
        crypto = CryptoManager(config.encryption)
        session = SessionManager(self.config_manager.path)
        note_store = NoteStore(db, crypto)
        backend = create_backend(config.backend)
        sync_service = SyncService(db, backend, SyncProgress(console))

        # Persist secret identifier if newly generated.
        if "secret_id" not in config.backend.options:
            config.backend.options["secret_id"] = backend.secret_id
            self.config_manager.save(config)

        self.config = config
        self.db = db
        self.crypto = crypto
        self.session = session
        self.note_store = note_store
        self.sync_service = sync_service
        self._backend = backend
        self._loaded = True

    def backend(self):
        self.ensure_loaded()
        return self._backend

    def get_master_key(self, *, prompt: bool = True) -> bytes:
        if self._master_key is not None:
            return self._master_key
        self.ensure_loaded()
        assert self.session is not None
        assert self.crypto is not None
        cached = self.session.load()
        if cached:
            self._master_key = cached
            return cached
        if not prompt:
            raise AuthenticationError("Master key not available in session.")
        password = click.prompt("Master password", hide_input=True)
        try:
            master_key = self.crypto.decrypt_master_key(password)
        except Exception as exc:
            raise AuthenticationError("Invalid master password") from exc
        self._master_key = master_key
        return master_key

    def store_master_key(self, master_key: bytes) -> None:
        if self.session is None:
            return
        self.session.store(master_key)
        self._master_key = master_key

    def clear_master_key(self) -> None:
        if self.session:
            self.session.clear()
        self._master_key = None


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=str),
    help=f"Path to notr config file (default: {DEFAULT_CONFIG_PATH})",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[str]):
    """Encrypted, extensible command line note app."""
    ctx.obj = CLIState(config_path=config_path)


def ensure_state(state: CLIState) -> None:
    try:
        state.ensure_loaded()
    except NotrError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise click.Abort()
    except Exception as exc:  # pragma: no cover - defensive
        console.print(f"[bold red]Failed to load configuration:[/bold red] {exc}")
        raise click.Abort()


def acquire_master_key(state: CLIState) -> bytes:
    try:
        return state.get_master_key()
    except AuthenticationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort()


def maybe_autosync(state: CLIState, reason: str) -> None:
    if not state.config or not state.config.options.autosync:
        return
    if not state.sync_service:
        return
    try:
        context = f"Auto-sync ({reason})"
        state.sync_service.sync(context=context)
    except BackendError as exc:
        console.print(f"[yellow]Auto-sync after {reason} skipped:[/yellow] {exc}")
        return


def complete_notebooks(ctx: click.Context, param, incomplete: str):
    if CompletionItem is None:
        return []
    try:
        state: CLIState = ctx.ensure_object(CLIState)
    except Exception:
        return []
    try:
        state.ensure_loaded()
    except Exception:
        return []
    if state.note_store is None:
        return []
    try:
        notebooks = state.note_store.list_notebooks()
        counts = state.note_store.notebook_counts()
    except Exception:
        return []
    suggestions = []
    incomplete_lower = incomplete.lower()
    for notebook in notebooks:
        if incomplete and not notebook.name.lower().startswith(incomplete_lower):
            continue
        total = counts.get(notebook.name, 0)
        help_text = f"{total} note{'s' if total != 1 else ''}"
        suggestions.append(CompletionItem(notebook.name, help=help_text))
    return suggestions


def _parse_fields(spec: Optional[str], allowed: Iterable[str], default: Iterable[str]) -> List[str]:
    allowed_set = {field: field for field in allowed}
    if spec:
        fields = []
        for item in spec.split(","):
            key = item.strip()
            if key not in allowed_set:
                raise click.ClickException(
                    f"Unknown field '{key}'. Allowed fields: {', '.join(allowed_set)}"
                )
            fields.append(allowed_set[key])
        if not fields:
            raise click.ClickException("Field list cannot be empty")
        return fields
    return list(default)


def display_notebooks(state: CLIState) -> None:
    assert state.note_store is not None
    notebooks = state.note_store.list_notebooks()
    counts = state.note_store.notebook_counts()
    if not notebooks:
        console.print("[yellow]No notebooks found. Use 'notr add <notebook>' to create one.[/yellow]")
        return
    table = Table(title="Notebooks", header_style="bold magenta")
    table.add_column("Name", style="bold")
    table.add_column("Notes", justify="right")
    table.add_column("Created", justify="right")
    for notebook in notebooks:
        count = counts.get(notebook.name, 0)
        table.add_row(
            notebook.name,
            str(count),
            notebook.created_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


def view_notes(state: CLIState, notebook_name: str) -> None:
    master_key = acquire_master_key(state)
    assert state.note_store is not None
    notes = state.note_store.list_notes(master_key, notebook_name)
    if not notes:
        console.print(f"[yellow]No notes in notebook '{notebook_name}'.[/yellow]")
        return
    table = Table(
        title=f"Notes in [bold cyan]{notebook_name}[/bold cyan]",
        header_style="bold blue",
    )
    table.add_column("ID", justify="right", style="bold")
    table.add_column("Title")
    table.add_column("Updated", justify="right")
    for note in notes:
        preview = note.payload.title or "(untitled)"
        table.add_row(
            str(note.id),
            preview,
            note.updated_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


def view_note_detail(state: CLIState, notebook_name: str, note_id: int, plain: bool = False) -> None:
    master_key = acquire_master_key(state)
    assert state.note_store is not None
    note = state.note_store.get_note(master_key, notebook_name, note_id)
    if plain:
        heading = f"# {note.payload.title}".rstrip()
        body = note.payload.body
        if body:
            console.print(f"{heading}\n\n{body}")
        else:
            console.print(heading)
        return
    header = Text(f"{note.payload.title or '(untitled)'}", style="bold underline cyan")
    metadata = Text(
        f"Notebook: {note.notebook_name} • Created: {note.created_at:%Y-%m-%d %H:%M} • Updated: {note.updated_at:%Y-%m-%d %H:%M}",
        style="dim",
    )
    console.print(header)
    console.print(metadata)
    console.print(Markdown(note.payload.body or ""))


def parse_note_input(content: str) -> NotePayload:
    lines = content.splitlines()
    title = "(untitled)"
    body_lines: List[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            title = stripped.lstrip("#").strip() or stripped
            body_lines = lines[index + 1 :]
            break
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    body = "\n".join(body_lines).rstrip()
    return NotePayload(title=title or "(untitled)", body=body)


@cli.command()
@click.option("--db-path", type=click.Path(dir_okay=False, path_type=str), help="Custom database path")
@click.pass_obj
def init(state: CLIState, db_path: Optional[str]):
    """Initialize configuration, master password, and backend."""
    config_manager = state.config_manager
    if config_manager.exists():
        overwrite = click.confirm(
            f"Configuration already exists at {config_manager.path}. Overwrite?", default=False
        )
        if not overwrite:
            console.print("[yellow]Initialization aborted.[/yellow]")
            return

    password = click.prompt("Create master password", hide_input=True, confirmation_prompt=True)
    master_key, bundle = CryptoManager.create_master_key_bundle(password)
    database_path = (
        Path(db_path).expanduser()
        if db_path
        else Path(os.environ.get("NOTR_DB_PATH", "~/.local/share/notr/notr.db")).expanduser()
    )
    init_progress = SyncProgress(console, label="Init")
    with init_progress.step("Preparing local database..."):
        db_manager = DatabaseManager(database_path)
        db_manager.ensure_initialized()

    backend_instance = None
    backend_type = ""
    backend_options: dict = {}
    while True:
        backend_type = prompt_backend_type()
        backend_options = dict(prompt_backend_options(backend_type))
        temp_backend_config = BackendConfig(type=backend_type, options=backend_options)
        backend_instance = create_backend(temp_backend_config)
        backend_options["secret_id"] = backend_instance.secret_id
        backend_instance.options["secret_id"] = backend_instance.secret_id

        if backend_type == "webdav":
            password_prompt = click.prompt(
                "WebDAV password", hide_input=True, confirmation_prompt=True
            )
            try:
                with init_progress.step("Authenticating with WebDAV..."):
                    backend_instance.login(password_prompt)
            except BackendError as exc:
                console.print(f"[red]Backend login failed:[/red] {exc}")
                console.print("[yellow]Please re-enter backend settings.[/yellow]")
                backend_instance.logout()
                continue

        try:
            with init_progress.step("Validating backend connection..."):
                backend_instance.validate(database_path)
        except BackendError as exc:
            console.print(f"[red]Backend validation failed:[/red] {exc}")
            console.print("[yellow]Please re-enter backend settings.[/yellow]")
            backend_instance.logout()
            continue
        except Exception as exc:  # pragma: no cover - defensive
            console.print(f"[red]Backend validation encountered an error:[/red] {exc}")
            console.print("[yellow]Please re-enter backend settings.[/yellow]")
            backend_instance.logout()
            continue
        break

    assert backend_instance is not None
    backend_config = BackendConfig(type=backend_type, options=dict(backend_instance.options))


    options = OptionsConfig()
    new_config = config_manager.upsert(
        backend=backend_config,
        encryption=bundle.to_encryption_config(),
        db_path=database_path,
        options=options,
    )
    state.config = new_config
    state.db = db_manager
    state.crypto = CryptoManager(new_config.encryption)
    state.session = SessionManager(config_manager.path)
    state.note_store = NoteStore(db_manager, state.crypto)
    state.sync_service = SyncService(db_manager, backend_instance, SyncProgress(console))
    state._backend = backend_instance
    state._loaded = True
    state.store_master_key(master_key)
    console.print("[green]Setup complete. Database initialized and backend configured.[/green]")


def prompt_backend_type() -> str:
    options = [
        ("local", "Local filesystem sync (fastest)"),
        ("webdav", "WebDAV server"),
    ]
    table = Table(title="Available Backends", header_style="bold magenta")
    table.add_column("#", justify="right")
    table.add_column("Type", style="bold cyan")
    table.add_column("Description")
    for idx, (value, description) in enumerate(options, start=1):
        table.add_row(str(idx), value, description)
    console.print(table)
    choice = click.prompt(
        "Select backend",
        type=click.IntRange(1, len(options)),
        default=1,
    )
    return options[choice - 1][0]


def prompt_backend_options(backend_type: str) -> dict:
    if backend_type == "local":
        directory = click.prompt(
            "Local sync directory",
            default=os.environ.get("NOTR_LOCAL_BACKUP", "~/.local/share/notr/remote"),
        )
        return {"directory": directory, "filename": "notr.db"}
    if backend_type == "webdav":
        url = click.prompt("WebDAV base URL (e.g. https://example.com/webdav/)")
        username = click.prompt("WebDAV username")
        directory = click.prompt(
            "WebDAV directory",
            default="/notr",
        )
        return {"url": url, "username": username, "directory": directory, "filename": "notr.db"}
    raise BackendError(f"Unsupported backend type '{backend_type}'")


@cli.command()
@click.argument("notebook", shell_complete=complete_notebooks)
@click.argument("content", required=False)
@click.option(
    "--file",
    "-f",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="Seed the note body from a file",
)
@click.option("--metadata", "-m", multiple=True, help="Add metadata entries key=value", metavar="KEY=VALUE")
@click.pass_obj
def add(
    state: CLIState,
    notebook: str,
    content: Optional[str],
    file_path: Optional[Path],
    metadata: tuple[str, ...],
):
    """Add a new note to a notebook."""
    ensure_state(state)
    assert state.config is not None
    master_key = acquire_master_key(state)
    if content and file_path:
        console.print("[red]Please provide either inline content or --file, not both.[/red]")
        raise click.Abort()

    if file_path:
        try:
            text = file_path.read_text(encoding="utf8")
        except UnicodeDecodeError:
            console.print(f"[red]File {file_path} is not valid UTF-8 text.[/red]")
            raise click.Abort()
        except OSError as exc:
            console.print(f"[red]Could not read file {file_path}: {exc}[/red]")
            raise click.Abort()
        payload = parse_note_input(text)
    elif not content:
        initial = "# Title\n\n"
        editor = state.config.options.editor if state.config and state.config.options.editor else None
        edited = click.edit(initial, editor=editor)
        if edited is None:
            console.print("[yellow]Note creation cancelled.[/yellow]")
            return
        payload = parse_note_input(edited.strip())
    else:
        payload = parse_note_input(content)
    if metadata:
        meta_dict = {}
        for item in metadata:
            if "=" not in item:
                console.print(f"[red]Invalid metadata entry '{item}'. Expected KEY=VALUE.[/red]")
                raise click.Abort()
            key, value = item.split("=", 1)
            meta_dict[key.strip()] = value.strip()
        payload.metadata = meta_dict
    assert state.note_store is not None
    note = state.note_store.create_note(master_key, notebook, payload)
    console.print(
        Panel.fit(
            f"[green]Note #{note.id} created in notebook [bold]{note.notebook_name}[/bold][/green]",
            border_style="green",
        )
    )
    maybe_autosync(state, "add")


@cli.command()
@click.argument("notebook", required=False, shell_complete=complete_notebooks)
@click.argument("note_id", required=False, type=int)
@click.option("--plain", is_flag=True, help="Print raw note content without formatting")
@click.pass_obj
def view(state: CLIState, notebook: Optional[str], note_id: Optional[int], plain: bool):
    """View notebooks, notes, or note details."""
    ensure_state(state)
    if notebook and note_id:
        view_note_detail(state, notebook, note_id, plain=plain)
    elif notebook:
        if plain:
            console.print("[yellow]--plain is only valid when viewing a specific note.[/yellow]")
        view_notes(state, notebook)
    else:
        if plain:
            console.print("[yellow]--plain is only valid when viewing a specific note.[/yellow]")
        display_notebooks(state)


@cli.command()
@click.argument("notebook", shell_complete=complete_notebooks)
@click.argument("note_id", type=int)
@click.pass_obj
def edit(state: CLIState, notebook: str, note_id: int):
    """Edit a note using the configured editor."""
    ensure_state(state)
    assert state.config is not None
    master_key = acquire_master_key(state)
    assert state.note_store is not None
    note = state.note_store.get_note(master_key, notebook, note_id)
    heading = f"# {note.payload.title}".rstrip()
    if note.payload.body:
        template = f"{heading}\n\n{note.payload.body}\n"
    else:
        template = f"{heading}\n\n"
    editor = state.config.options.editor if state.config and state.config.options.editor else None
    edited = click.edit(template, editor=editor)
    if edited is None:
        console.print("[yellow]Edit cancelled.[/yellow]")
        return
    payload = parse_note_input(edited.strip())
    payload.metadata = note.payload.metadata
    updated = state.note_store.update_note(master_key, notebook, note_id, payload)
    console.print(
        Panel.fit(
            f"[green]Note #{updated.id} updated ({updated.updated_at:%Y-%m-%d %H:%M}).[/green]",
            border_style="green",
        )
    )
    maybe_autosync(state, "edit")


@cli.command()
@click.argument("from_notebook", shell_complete=complete_notebooks)
@click.argument("note_id", type=int)
@click.argument("to_notebook", shell_complete=complete_notebooks)
@click.pass_obj
def move(state: CLIState, from_notebook: str, note_id: int, to_notebook: str):
    """Move a note between notebooks."""
    ensure_state(state)
    master_key = acquire_master_key(state)
    assert state.note_store is not None
    note = state.note_store.move_note(master_key, note_id, from_notebook, to_notebook)
    console.print(
        Panel.fit(
            f"[green]Moved note #{note.id} to notebook [bold]{note.notebook_name}[/bold].[/green]",
            border_style="green",
        )
    )
    maybe_autosync(state, "move")


@cli.command()
@click.argument("old_name", shell_complete=complete_notebooks)
@click.argument("new_name")
@click.pass_obj
def rename(state: CLIState, old_name: str, new_name: str):
    """Rename a notebook."""
    ensure_state(state)
    acquire_master_key(state)
    assert state.note_store is not None
    try:
        state.note_store.rename_notebook(old_name, new_name)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort()
    console.print(
        Panel.fit(
            f"[green]Notebook '{old_name}' renamed to '{new_name}'.[/green]",
            border_style="green",
        )
    )
    maybe_autosync(state, "rename")


@cli.command()
@click.argument("notebook", shell_complete=complete_notebooks)
@click.argument("note_id", type=int)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.pass_obj
def remove(state: CLIState, notebook: str, note_id: int, force: bool):
    """Remove a note."""
    ensure_state(state)
    acquire_master_key(state)
    if not force:
        confirm = click.confirm(f"Delete note {note_id} from '{notebook}'?", default=False)
        if not confirm:
            console.print("[yellow]Deletion cancelled.[/yellow]")
            return
    assert state.note_store is not None
    try:
        state.note_store.delete_note(notebook, note_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort()
    console.print(Panel.fit(f"[green]Note {note_id} deleted.[/green]", border_style="green"))
    maybe_autosync(state, "remove")


@cli.command()
@click.argument("query")
@click.option(
    "--book",
    "-b",
    "notebook",
    help="Limit search to a specific notebook",
    shell_complete=complete_notebooks,
)
@click.pass_obj
def find(state: CLIState, query: str, notebook: Optional[str]):
    """Search notes using a case-insensitive match."""
    ensure_state(state)
    master_key = acquire_master_key(state)
    assert state.note_store is not None
    results = state.note_store.search_notes(master_key, query, notebook)
    render_search_results(results, query)


@cli.command()
@click.argument("query")
@click.option(
    "--book",
    "-b",
    "notebook",
    help="Limit search to a specific notebook",
    shell_complete=complete_notebooks,
)
@click.option("--limit", default=10, show_default=True, help="Maximum results to display")
@click.pass_obj
def ffind(state: CLIState, query: str, notebook: Optional[str], limit: int):
    """Fuzzy search notes using RapidFuzz scoring."""
    ensure_state(state)
    master_key = acquire_master_key(state)
    assert state.note_store is not None
    results = state.note_store.fuzzy_find(master_key, query, notebook, limit=limit)
    render_search_results(results, query, fuzzy=True)


def render_search_results(results, query: str, fuzzy: bool = False) -> None:
    if not results:
        console.print(f"[yellow]No matches for '{query}'.[/yellow]")
        return
    table = Table(
        title=f"{'Fuzzy ' if fuzzy else ''}Search Results for [bold]{query}[/bold]",
        header_style="bold magenta",
    )
    table.add_column("ID", justify="right")
    table.add_column("Notebook", style="cyan")
    table.add_column("Title")
    table.add_column("Updated", justify="right")
    for note in results:
        table.add_row(
            str(note.id),
            note.notebook_name,
            note.payload.title or "(untitled)",
            note.updated_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@cli.command()
@click.option(
    "--scope",
    type=click.Choice(["notebooks", "notes"]),
    default="notebooks",
    show_default=True,
    help="Export notebooks or notes",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["tsv", "json"]),
    default="tsv",
    show_default=True,
    help="Output format",
)
@click.option(
    "--no-header",
    is_flag=True,
    help="Omit header row in TSV output",
)
@click.option(
    "--fields",
    help="Comma-separated list of columns to include in TSV output",
)
@click.option(
    "--notebook",
    "notebook_filter",
    shell_complete=complete_notebooks,
    help="Limit notes export to a specific notebook",
)
@click.pass_obj
def export(
    state: CLIState,
    scope: str,
    fmt: str,
    no_header: bool,
    fields: Optional[str],
    notebook_filter: Optional[str],
):
    """Emit machine-friendly notebook/note listings for tools like fzf."""
    ensure_state(state)
    assert state.note_store is not None

    if fmt == "json":
        import json

    if scope == "notebooks":
        notebooks = state.note_store.list_notebooks()
        counts = state.note_store.notebook_counts()
        if fmt == "json":
            payload = [
                {
                    "uuid": nb.uuid,
                    "name": nb.name,
                    "note_count": counts.get(nb.name, 0),
                    "created_at": nb.created_at.isoformat(),
                    "updated_at": nb.updated_at.isoformat(),
                }
                for nb in notebooks
            ]
            click.echo(json.dumps(payload))
        else:
            field_map = {
                "uuid": lambda nb: nb.uuid,
                "name": lambda nb: nb.name,
                "note_count": lambda nb: str(counts.get(nb.name, 0)),
                "created_at": lambda nb: nb.created_at.isoformat(),
                "updated_at": lambda nb: nb.updated_at.isoformat(),
            }
            selected_fields = _parse_fields(fields, field_map.keys(), default=[
                "uuid",
                "name",
                "note_count",
                "created_at",
                "updated_at",
            ])
            if not no_header:
                click.echo("\t".join(selected_fields))
            for nb in notebooks:
                row = [field_map[field](nb) for field in selected_fields]
                click.echo("\t".join(row))
        return

    # scope == notes
    master_key = acquire_master_key(state)
    notes = state.note_store.list_notes(master_key, notebook_filter)
    if fmt == "json":
        import json

        payload = [
            {
                "note_id": note.id,
                "uuid": note.uuid,
                "notebook_uuid": note.notebook_uuid,
                "notebook": note.notebook_name,
                "title": note.payload.title,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat(),
                "preview": note.payload.body.splitlines()[0] if note.payload.body else "",
            }
            for note in notes
        ]
        click.echo(json.dumps(payload))
    else:
        field_map = {
            "note_id": lambda n: str(n.id),
            "note_uuid": lambda n: n.uuid,
            "notebook_uuid": lambda n: n.notebook_uuid,
            "notebook": lambda n: n.notebook_name,
            "title": lambda n: n.payload.title,
            "created_at": lambda n: n.created_at.isoformat(),
            "updated_at": lambda n: n.updated_at.isoformat(),
            "preview": lambda n: (n.payload.body.splitlines()[0] if n.payload.body else ""),
        }
        selected_fields = _parse_fields(fields, field_map.keys(), default=[
            "note_id",
            "title",
            "preview",
        ])
        if not no_header:
            click.echo("\t".join(selected_fields))
        for note in notes:
            row = [field_map[field](note) for field in selected_fields]
            click.echo("\t".join(row))


@cli.command()
@click.option(
    "--direction",
    type=click.Choice([d.value for d in SyncDirection]),
    default=SyncDirection.BOTH.value,
    show_default=True,
    help="Sync direction",
)
@click.pass_obj
def sync(state: CLIState, direction: str):
    """Sync the local database with the configured backend."""
    ensure_state(state)
    assert state.config is not None
    assert state.db is not None
    backend = state.backend()
    assert state.sync_service is not None
    direction_enum = SyncDirection(direction)
    try:
        result = state.sync_service.sync(direction_enum, context="Sync")
    except BackendError as exc:
        console.print(f"[red]Sync failed:[/red] {exc}")
        raise click.Abort()
    if result.message:
        console.print(f"[green]{result.message}[/green]")


@cli.group()
def backend():
    """Backend management commands."""


@backend.command("login")
@click.pass_obj
def backend_login(state: CLIState):
    """Log in to the remote backend."""
    ensure_state(state)
    backend = state.backend()
    password = click.prompt("Backend password", hide_input=True)
    try:
        backend.login(password=password)
    except BackendError as exc:
        console.print(f"[red]{exc}[/red]")
        raise click.Abort()
    console.print("[green]Backend credentials stored.[/green]")


@backend.command("logout")
@click.pass_obj
def backend_logout(state: CLIState):
    """Clear stored backend credentials."""
    ensure_state(state)
    backend = state.backend()
    backend.logout()
    console.print("[green]Backend credentials cleared.[/green]")


@backend.command("status")
@click.pass_obj
def backend_status(state: CLIState):
    """Show backend status information."""
    ensure_state(state)
    backend = state.backend()
    info = backend.status()
    table = Table(title="Backend status", header_style="bold magenta")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="bold")
    for key, value in info.items():
        table.add_row(key, str(value))
    console.print(table)


@cli.command()
@click.pass_obj
def login(state: CLIState):
    """Unlock the master password for this session."""
    ensure_state(state)
    assert state.crypto is not None
    password = click.prompt("Master password", hide_input=True)
    try:
        master_key = state.crypto.decrypt_master_key(password)
    except Exception:
        console.print("[red]Invalid master password.[/red]")
        raise click.Abort()
    state.store_master_key(master_key)
    console.print("[green]Master password unlocked for this session.[/green]")


@cli.command()
@click.pass_obj
def logout(state: CLIState):
    """Forget the cached master password."""
    ensure_state(state)
    state.clear_master_key()
    console.print("[green]Master password removed from session.[/green]")


@cli.command()
@click.pass_obj
def changemaster(state: CLIState):
    """Change the master password."""
    ensure_state(state)
    assert state.crypto is not None
    assert state.config is not None
    current_password = click.prompt("Current master password", hide_input=True)
    try:
        master_key = state.crypto.decrypt_master_key(current_password)
    except Exception:
        console.print("[red]Current master password is incorrect.[/red]")
        raise click.Abort()
    new_password = click.prompt("New master password", hide_input=True, confirmation_prompt=True)
    iterations = state.config.encryption.kdf_iterations
    bundle = CryptoManager.rewrap_master_key(master_key, new_password, iterations=iterations)
    state.config.encryption = bundle.to_encryption_config()
    state.config_manager.save(state.config)
    state.store_master_key(master_key)
    console.print("[green]Master password updated successfully.[/green]")


@cli.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]), required=True)
def completion(shell: str):
    """Generate shell completion script."""
    if shell_complete is None:
        raise click.ClickException(
            "Shell completion scripts require Click >= 8.0. Please upgrade Click."
        )
    prog_name = "notr"
    complete_var = f"_{prog_name.replace('-', '_').upper()}_COMPLETE"
    status = shell_complete(cli, {}, prog_name, complete_var, f"{shell}_source")
    if status != 0:
        raise click.ClickException(f"Shell '{shell}' is not supported for completion.")


def main() -> None:
    try:
        cli.main(standalone_mode=False)
    except click.exceptions.Abort:
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - top-level safety net
        console.print(f"[bold red]Fatal error:[/bold red] {exc}")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
