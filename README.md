# Notr

Encrypted, lightning-fast CLI notes with a modern TUI experience and pluggable sync backends.

## Highlights

- **End-to-end encryption** – every note is encrypted with AES-256-GCM before touching disk.
- **Rich CLI UX** – colourful output, fuzzy search, notebook-aware completions, and ergonomic shortcuts powered by Click + Rich.
- **Intelligent sync** – per-note merges with UUID tracking, conflict-safe tombstones, and optional auto-sync.
- **Extensible architecture** – ships with local filesystem and WebDAV backends; new backends slot in via a single interface.

## Installation

```bash
pipx install notr
# or
pip install notr
```

Upgrade with `pipx upgrade notr` (or `pip install --upgrade notr`).

## Quick Start

```bash
# Initialise the secure store and select a backend
notr init

# Add your first note (opens $EDITOR when text omitted)
notr add Work

# Browse notebooks and notes
notr view          # notebooks
notr view Work     # notes in “Work”

# Search across everything (fuzzy search included)
notr find meeting
notr ffind "meetng"

# Merge local/remote state (auto-sync can do this for you)
notr sync
```

Every command provides `--help`, and short aliases exist (for example `notr a` for `notr add`).

### Everyday Commands

| Command | Purpose |
| --- | --- |
| `notr add [NOTEBOOK] [TEXT]` | Create a note (opens `$EDITOR` if `TEXT` omitted; `--file` seeds from a file). |
| `notr view [NOTEBOOK] [ID]` | List notebooks, list notes, or render a note as pretty Markdown. |
| `notr edit NOTEBOOK ID` | Edit an existing note in your editor. |
| `notr move FROM ID TO` | Move a note between notebooks. |
| `notr find [-b NOTEBOOK] QUERY` | Case-insensitive search within a notebook or globally. |
| `notr ffind [-b NOTEBOOK] QUERY` | Fuzzy search powered by RapidFuzz. |
| `notr sync` | Merge local and remote changes with spinner + summary output. |
| `notr backend login/logout/status` | Manage backend credentials and health. |
| `notr login/logout` | Cache or forget the decrypted master password for the current session. |
| `notr changemaster` | Rotate the master password without re-encrypting notes manually. |
| `notr export [options]` | Emit FZF-friendly TSV/JSON listings of notebooks or notes. |
| `notr update NOTEBOOK ID [--file PATH]` | Apply note changes from stdin or a file (no editor launch). |
| `notr notebook create NAME` | Ensure a notebook exists (safe to run repeatedly). |

#### FZF integration

```bash
# pick a notebook, then fuzzy match its notes
notebook=$(notr export --scope notebooks --fields name --no-header | fzf)

notr export --scope notes --notebook "$notebook" \
  --fields "note_id,title" --no-header |
  fzf --delimiter "\t" --with-nth=2 --preview 'notr view '"$notebook"' {1} --plain'
```

```fish
# Fish shell variant
notr export --scope notebooks --fields name --no-header | \
  fzf | read --local notebook

notr export --scope notes --notebook "$notebook" --fields "note_id,title" --no-header \
  | fzf --delimiter "\t" --with-nth=2 --preview "notr view \"$notebook\" {1} --plain"
```

`notr export` supports TSV (default) and JSON; use `--fields` to choose columns (for example `--fields name` or `--fields title,preview`). More recipes live in [docs/FZF-INTEGRATION.md](docs/FZF-INTEGRATION.md).

## Sync & Security

- The master password never leaves your machine. A PBKDF2-derived key wraps a randomly generated master key used for note encryption.
- Backends only see encrypted SQLite pages. Credentials live in the OS keyring when available (with a locked-down file fallback).
- Sync compares notebooks/notes with stable UUIDs, merging only what changed. Tombstones ensure deletions propagate safely. The first sync against an older database migrates it automatically.
- Enable automatic syncing after mutating commands by setting `"autosync": true` in `~/.config/notr/config.json`.

## Configuration

- Config file: `~/.config/notr/config.json` (override via `NOTR_CONFIG_PATH`).
- Encrypted database: `~/.local/share/notr/notr.db` (override via `NOTR_DB_PATH`).
- Tweak defaults within the `options` section (editor, viewer, timestamps, auto-sync, conflict handling).

### Built-in Backends

- **local** – keeps an encrypted copy in a directory you control (perfect for offline backups or sync tools like Syncthing).
- **webdav** – syncs against any WebDAV endpoint using `PUT`/`GET`/`HEAD`, with graceful error messaging and spinner feedback.

To add a backend, subclass `Backend` in `notr/backends/` and register it in `notr/backends/base.py`.

## Shell Completion

Generate completion scripts and source them from your shell startup:

```bash
notr completion bash > ~/.config/notr/notr.bash
notr completion zsh  > ~/.config/notr/notr.zsh
notr completion fish > ~/.config/fish/completions/notr.fish
```

Restart your shell (or `exec $SHELL`) to activate completions.

### Neovim integration

A first-party Neovim plugin lives in [`nvim/notr.nvim`](nvim/notr.nvim). It uses fzf-lua to browse notebooks/notes and edits notes in-buffer via the new `notr update` command. Installation and usage details are in that directory's README.

## Contributing & Support

- Report bugs or request features: <https://github.com/kimusan/notr/issues>
- Ready to contribute? Read [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE). Crafted with care by [Kim Schulz](https://schulz.dk) and the community.
