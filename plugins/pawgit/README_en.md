# PawGit

[[中文](README.md)]

PawGit is a QwenPaw plugin for versioning conversation context and memory
state. It stores checkpoints in a private shadow Git repository under
`.pawgit`, then lets users inspect timelines, preview rewinds, rewind the
current conversation, optionally rewind memory source files, clean old refs, or
reset PawGit state.

Important scope note: PawGit snapshots capture workspace files into `.pawgit`
so a checkpoint is complete, but rewind is **not** a general project-file
rollback. Rewind restores the current conversation session JSON, and only
restores `MEMORY.md` plus `memory/` when memory rewind is explicitly requested.
It does not restore arbitrary workspace or coding project files.

## Features

| Feature | Description |
| --- | --- |
| Auto checkpoints | After an agent reply, PawGit creates a debounced checkpoint for the current session. |
| Named snapshots | Users can create permanent checkpoints that PawGit GC will not delete. |
| Timeline | Shows checkpoints grouped by type and channel, with an ASCII DAG preview. |
| Conversation rewind | Restores only the current channel/session conversation JSON. |
| Memory rewind | Restores the current conversation plus `MEMORY.md` and `memory/`. |
| Agent tool | Lets the agent create snapshots, inspect timelines, preview rewinds, and run safe maintenance actions. |
| Garbage collection | Cleans collectible `auto` and `pre-rewind` refs according to retention policy. |
| Reset | Deletes and recreates `.pawgit` without touching sessions or memory files. |

## Slash Commands

PawGit exposes one unified slash command:

```text
/pawgit timeline [--limit=N] [--all]
/pawgit snapshot [message]
/pawgit rewind <N|snap_name|sha> [--dry-run]
/pawgit rewind <target> --include-memory [--dry-run|--confirm]
/pawgit gc [--compact] [--all-sessions] [--dry-run]
/pawgit reset --confirm
/pawgit --help
```

Running `/pawgit` with no arguments also shows help.

Common flow:

```text
/pawgit timeline
/pawgit snapshot before-refactor
/pawgit rewind before-refactor --dry-run
/pawgit rewind before-refactor
```

## Agent Tool

The plugin also registers a built-in agent tool named `pawgit`.

The agent tool is useful for:

- creating snapshots before risky work
- rendering the timeline
- previewing rewind targets
- running GC dry-runs or confirmed GC
- resetting PawGit after explicit confirmation

Examples:

```text
pawgit(action="snapshot", message="before-risky-change")
pawgit(action="timeline", limit=10)
pawgit(action="rewind", target="1", dry_run=true)
pawgit(action="rewind", target="1", include_memory=true, dry_run=true)
pawgit(action="gc", dry_run=true)
pawgit(action="gc", dry_run=false, confirm=true)
pawgit(action="reset", confirm=true)
```

Real rewind is intentionally blocked in the agent tool. A real rewind changes
the current conversation context and may require QwenPaw to reload session
state, so it must go through the slash-command path:

```text
/pawgit rewind <target>
/pawgit rewind <target> --include-memory --confirm
```

The agent tool may preview these operations, then ask the user to run the slash
command when an actual rewind is needed.

## Timeline

```text
/pawgit timeline
/pawgit timeline --limit=10
/pawgit timeline --all
```

- By default, only checkpoints for the current session are shown.
- `--limit=N` limits rows per section and is capped by `timeline.max_limit`.
- `--all` shows checkpoints from all sessions in the current workspace.
- Timeline output includes a lightweight ASCII DAG derived from commit
  metadata.
- `*` marks the current session HEAD, `o` marks the active path, and `x` marks
  a branch left behind by rewind.
- Entries are grouped by checkpoint kind (`auto`, `snapshot`, `pre-rewind`) and
  then by channel.
- Rewind commands are shown only for checkpoints in the current session. Other
  sessions are shown for context and should not be rewound with the current
  session's timeline index.

Each timeline row includes:

- checkpoint kind
- snapshot name
- commit SHA prefix
- real-world timestamp
- channel
- latest user query preview
- rewind command when applicable

## Snapshot

```text
/pawgit snapshot
/pawgit snapshot before-refactor
/pawgit snapshot Release candidate 1
```

- Creates a permanent snapshot under `refs/snap/`.
- The message is used as both the snapshot label and commit body.
- If no message is provided, PawGit generates a timestamp-based label.
- Labels are sanitized into safe Git ref components.
- Duplicate labels receive numeric suffixes.
- Named snapshots are never removed by PawGit GC.

Every checkpoint stores commit metadata containing the latest persisted user
query, channel, user ID, session ID, and parent commit. Timeline rendering
shows a safe single-line query preview and preserves Chinese text correctly.

## Rewind

Targets can be:

- timeline index, such as `1`
- snapshot name, such as `before-refactor`
- commit SHA or unique prefix
- full PawGit ref

```text
/pawgit rewind 1
/pawgit rewind before-refactor
/pawgit rewind a1b2c3d4
/pawgit rewind 1 --dry-run
```

Normal rewind restores only the current session JSON. It does not modify
`MEMORY.md`, `memory/`, other sessions, or arbitrary workspace files.

Before a real rewind, PawGit creates a `pre-rewind` checkpoint as a safety
backup. `--dry-run` only resolves the target and shows what would be restored;
it does not write files or create a safety checkpoint.

If a purely numeric string is both a snapshot name and a possible timeline
index, PawGit treats it as a timeline index first. Use a full ref or SHA to
target that numeric snapshot directly.

## Memory Rewind

```text
/pawgit rewind before-refactor --include-memory --dry-run
/pawgit rewind before-refactor --include-memory --confirm
```

Memory rewind restores:

1. the current session JSON
2. `MEMORY.md` at the workspace root
3. files under `memory/` that existed in the target checkpoint

This is broader than normal conversation rewind. `MEMORY.md` and `memory/` are
shared across channels and sessions, so memory changes made by other sessions
after the target checkpoint can be discarded.

Safety behavior:

- non-dry-run memory rewind requires `--confirm`
- PawGit creates a `pre-rewind` safety checkpoint first
- if restoration fails, PawGit attempts to restore from the safety checkpoint
- ongoing queries wait behind the memory rewind maintenance gate

## Garbage Collection

```text
/pawgit gc
/pawgit gc --dry-run
/pawgit gc --all-sessions
/pawgit gc --compact
```

- By default, GC only handles `auto` and `pre-rewind` refs for the current
  session.
- `--all-sessions` expands the cleanup scope to all sessions in the workspace.
- `--dry-run` reports refs that would be deleted or kept.
- `--compact` deletes all collectible `auto` and `pre-rewind` refs in scope.
- `snapshot` refs are never deleted by PawGit GC.
- Non-dry-run GC runs `git gc --prune=now` after deleting refs.

Auto checkpoints also trigger GC with the default retention policy.

Retention uses both count and time windows:

- keep the newest `gc.gc_keep_count` auto checkpoints per session
- keep auto checkpoints newer than `gc.gc_keep_days`
- keep `pre-rewind` refs newer than `gc.pre_rewind_retention_days`

## Reset

```text
/pawgit reset
/pawgit reset --confirm
```

`reset` deletes and recreates the current workspace's `.pawgit` directory,
including the shadow Git repository, refs, DAG HEAD metadata, and PawGit config.

It does not modify:

- user project files
- session JSON files
- `MEMORY.md`
- `memory/`

Running `/pawgit reset` without `--confirm` only shows the risk message.

## Snapshot Contents

Each checkpoint is a root commit in PawGit's shadow Git repository. PawGit
rebuilds the Git index from scratch for each snapshot and uses `git add -f` so
workspace `.gitignore` files cannot change the snapshot boundary.

Default excludes include:

```text
.git/
.pawgit/
coding_projects/
file_store/
.reme_store_*/
backup/
browser/
__pycache__/
*.pyc
*.log
```

The actual list is defined by `support.EXCLUDE_PATTERNS`. Excluding
`coding_projects/` avoids failures from nested repositories or projects without
a checked-out commit.

## Storage Layout

```text
<workspace>/.pawgit/
|-- shadow.git/    # private bare Git repository
|-- index          # private Git index
|-- config.toml    # workspace-level PawGit config
|-- heads.json     # current DAG HEAD per session
```

Ref categories:

| Ref prefix | Meaning | GC behavior |
| --- | --- | --- |
| `refs/auto/` | automatic checkpoint after an agent reply | count/time/compact cleanup |
| `refs/snap/` | user-created permanent snapshot | always kept |
| `refs/pre-rewind/` | safety checkpoint before real rewind | retention-days/compact cleanup |

Git objects may remain after refs are deleted until non-dry-run GC prunes them.

## Configuration

Config file:

```text
<workspace>/.pawgit/config.toml
```

PawGit reloads config when the file changes, so users can edit it without
reinstalling the plugin.

Default config:

```toml
[gc]
gc_keep_count = 20
gc_keep_days = 7
pre_rewind_retention_days = 7

[auto]
debounce_seconds = 1.5

[timeline]
default_limit = 20
max_limit = 200

[display]
query_preview_chars = 120

[safety]
include_memory_quiesce_timeout = 30.0
```

| Config key | Description |
| --- | --- |
| `gc.gc_keep_count` | Minimum newest auto checkpoints kept per session. |
| `gc.gc_keep_days` | Time window for keeping auto checkpoints. |
| `gc.pre_rewind_retention_days` | Retention window for pre-rewind safety refs. |
| `auto.debounce_seconds` | Debounce delay before creating auto checkpoints. |
| `timeline.default_limit` | Default timeline row limit. |
| `timeline.max_limit` | Maximum accepted timeline row limit. |
| `display.query_preview_chars` | Maximum query preview length in timeline. |
| `safety.include_memory_quiesce_timeout` | Max seconds to wait for memory rewind quiescence. |

Command-line flags affect only the current invocation. For example,
`/pawgit timeline --limit=5` does not modify `config.toml`.

## Module Map

| File | Responsibility |
| --- | --- |
| `backend.py` | Plugin entry point; registers slash command, agent tool, skill provider, and hooks. |
| `handlers.py` | `/pawgit` help text and subcommand dispatch. |
| `tools.py` | Agent-callable PawGit tool for snapshots, timeline, rewind preview, GC, and reset. |
| `registry.py` | Per-workspace engine registry and auto-snapshot debounce. |
| `engine.py` | Snapshot, timeline, conversation rewind, GC, and reset orchestration. |
| `memory_rewind.py` | Memory rewind maintenance gate and file restoration. |
| `repository.py` | Shadow Git setup, config loading, Git availability checks, and atomic writes. |
| `support.py` | Data models, excludes, metadata helpers, target parsing, and renderers. |
| `utils.py` | Session paths, ref sanitization, session keys, and command parsing helpers. |
| `skills/pawgit/SKILL.md` | Skill instructions that teach the agent when and how to use the PawGit tool. |
