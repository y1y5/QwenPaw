# PawGit

PawGit adds shadow-git checkpoints and conv-only rewind for QwenPaw
workspaces.

Phase 1 includes:

- Debounced automatic checkpoints after agent replies.
- `/timeline [--limit=N] [--all]`
- `/snapshot [message]`
- `/rewind <N | snap_name | sha> [--dry-run]`
- `/gc [--compact] [--all-sessions] [--dry-run]`

The shadow repository lives at:

```text
<workspace_dir>/.pawgit/shadow.git/
```

Runtime settings live at `<workspace_dir>/.pawgit/config.toml`. PawGit reloads
the file automatically after it changes:

```toml
[gc]
gc_keep_count = 30
gc_keep_days = 14
pre_rewind_retention_days = 7

[auto]
debounce_seconds = 1.5

[timeline]
default_limit = 20
max_limit = 200

[display]
query_preview_chars = 120
```

Explicit slash-command options, such as `/timeline --limit=5`, override the
configured default for that invocation.

Snapshots are parentless git commits. Permanent `/snapshot` refs are stored
under `refs/snap/*` and are never deleted by `/gc`.

Every auto, permanent, and pre-rewind snapshot stores the latest persisted
user query in its commit message as `PawGit-Metadata` JSON. `/timeline` shows
a shortened single-line preview; the commit metadata retains the full text.

Implementation responsibilities are split by module:

- `engine.py`: snapshot, timeline, rewind, and GC orchestration.
- `repository.py`: shadow Git process and atomic filesystem operations.
- `support.py`: models, policies, metadata, ref parsing, and rendering.

`/rewind --include-memory` is intentionally left for Phase 2.
