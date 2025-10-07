# FZF Integration Guide

This guide shows how to pair `notr export` with fuzzy finders (fzf, skim, etc.) to browse notebooks and notes, preview content, and jump into editing.

The examples assume fzf is installed and located on your path. Substitute `fzf` with your favourite fuzzy finder if needed.

## Export Command Recap

`notr export` provides machine-friendly TSV or JSON output.

- `notebooks` scope: `notr export --scope notebooks`
  - Default TSV columns: `uuid`, `name`, `note_count`, `created_at`, `updated_at`.
- `notes` scope: `notr export --scope notes`
  - Default TSV columns: `note_id`, `title`, `preview`.
- Narrow to a notebook: `notr export --scope notes --notebook "Work"`
- Choose columns: `--fields "note_id,title,preview"`
- JSON output: `--format json`
- Skip header: `--no-header`

All examples below rely on TSV output with headers disabled for clean pipelines.

## Bash

```bash
#!/usr/bin/env bash

# pick a notebook
notebook=$(notr export --scope notebooks --fields name --no-header | fzf)
[[ -z "$notebook" ]] && exit 0

# pick a note within the notebook, previewing its contents
selection=$(notr export --scope notes --notebook "$notebook" \
  --fields "note_id,title" --no-header |
  fzf --delimiter "\t" --with-nth=2 \
      --preview 'notr view '"$notebook"' {1} --plain')

note_id=$(cut -f1 <<<"$selection")
[[ -z "$note_id" ]] && exit 0

# open the note in your configured editor via notr edit
notr edit "$notebook" "$note_id"

# alternatively, apply changes from a scratch buffer or tool
# cat updated.md | notr update "$notebook" "$note_id"
```

- `--delimiter "\t"` tells fzf to split on tabs.
- `--with-nth=2` displays only the title column.
- `{1}` references the first field (here, the note ID) in preview/edit commands.

## Fish

```fish
#!/usr/bin/env fish

notr export --scope notebooks --fields name --no-header \
  | fzf | read --local notebook
test -z "$notebook"; and exit 0

notr export --scope notes --notebook "$notebook" \
  --fields "note_id,title" --no-header \
  | fzf --delimiter "\t" --with-nth=2 \
        --preview "notr view \"$notebook\" {1} --plain" \
  | read --local selection
test -z "$selection"; and exit 0

set note_id (echo $selection | cut -f1)
test -z "$note_id"; and exit 0

notr edit "$notebook" "$note_id"
```

## Zsh

```zsh
#!/usr/bin/env zsh

notebook=$(notr export --scope notebooks --fields name --no-header | fzf)
[[ -z $notebook ]] && return

selection=$(notr export --scope notes --notebook "$notebook" \
  --fields "note_id,title" --no-header |
  fzf --delimiter "\t" --with-nth=2 \
      --preview 'notr view '"$notebook"' {1} --plain')

note_id=$(cut -f1 <<< "$selection")
[[ -z $note_id ]] && return

notr edit "$notebook" "$note_id"
```

## Editing Workflow

The examples above call `notr edit NOTEBOOK NOTE_ID`, which opens the note in your configured editor (as set in `options.editor` or `$EDITOR`).

Alternative approaches:

- Use `notr view` in the preview and open the note in a new terminal pane or TUI viewer.
- Pipe the TSV into another tool (e.g. `awk`, `sk`) if you want different key bindings or preview renderers.

## Advanced Tips

- Export extra fields (e.g. `preview`, `created_at`) and include them in the preview template.
- Switch to JSON when integrating with scripts/tools that prefer structured data:
  ```bash
  notr export --scope notes --format json | jq '.[].title'
  ```
- Combine with `notr sync` before listing to ensure the latest state is available.

Happy searching!
