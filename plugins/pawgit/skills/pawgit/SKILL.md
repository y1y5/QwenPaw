---
name: pawgit
description: Use the PawGit agent tool to checkpoint QwenPaw conversation state, inspect timelines, preview rewinds, clean refs, and reset PawGit safely.
---

# PawGit Skill

Use this skill when the user asks to checkpoint, snapshot, inspect history,
rewind, recover, clean, reset, or protect QwenPaw conversation context or
memory state with PawGit.

PawGit exposes an agent tool named `pawgit`. Use that tool directly when it is
available. PawGit also has equivalent QwenPaw slash commands for users, but
slash commands are entered by the user in the QwenPaw chat input and are not
shell commands.

PawGit snapshots capture the workspace into `.pawgit`, but PawGit rewind is
not a general workspace file rollback. Rewind restores the current conversation
session context, and only restores memory source files when memory rewind is
explicitly requested. It does not restore arbitrary project files.

For faster GC, each checkpoint is stored as a parentless Git commit (no
`-p` parent): every snapshot is an independent full-workspace tree, so deleting
old refs does not require walking a long Git parent chain. The session timeline
DAG is maintained separately via `PawGit-Metadata` parent fields and
`.pawgit/heads.json`, not via Git commit parent links.

Do not edit `.pawgit` files directly. Prefer dry-run and confirmation flows for
destructive operations.

## Agent Tool

Call the `pawgit` tool with these actions:

```text
pawgit(action="timeline", limit=10, include_all=false)
pawgit(action="snapshot", message="before-risky-change")
pawgit(action="rewind", target="1", dry_run=true)
pawgit(action="rewind", target="1", include_memory=true, dry_run=true)
pawgit(action="gc", dry_run=true)
pawgit(action="gc", dry_run=false, confirm=true)
pawgit(action="reset", confirm=true)
pawgit(action="help")
```

Equivalent user slash commands:

```text
/pawgit timeline [--limit=N] [--all]
/pawgit snapshot [message]
/pawgit rewind <N|snap_name|sha> [--dry-run]
/pawgit rewind <target> --include-memory [--dry-run|--confirm]
/pawgit gc [--compact] [--all-sessions] [--dry-run]
/pawgit reset --confirm
/pawgit --help
```

## When To Use

- Before risky edits, dependency upgrades, refactors, migrations, or bulk file
  changes: call `pawgit(action="snapshot", message="<message>")`.
- When the user asks "what checkpoints do I have?", "show history", or "can I
  go back?": call `pawgit(action="timeline")`.
- When the user wants to preview a rollback: call
  `pawgit(action="rewind", target="<target>", dry_run=true)`.
- When the user wants to restore the current conversation: preview first with
  the `pawgit` tool, then ask the user to run `/pawgit rewind <target>` in the
  QwenPaw chat input. Do not perform real rewind from the agent tool.
- When the user explicitly wants memory source files restored too: call
  `pawgit(action="rewind", target="<target>", include_memory=true,
  dry_run=true)` first, then ask the user to run
  `/pawgit rewind <target> --include-memory --confirm` in the QwenPaw chat
  input. Do not perform real memory rewind from the agent tool.
- When PawGit refs become too noisy: call `pawgit(action="gc", dry_run=true)`,
  then use `dry_run=false, confirm=true` only after confirmation.
- When the user wants to discard all PawGit checkpoint state for this
  workspace: explain the impact, then call `pawgit(action="reset",
  confirm=true)` only after confirmation.

## Safety Rules

- Always create or recommend a named snapshot before risky work:
  `pawgit(action="snapshot", message="before-<task>")`.
- Use `pawgit(action="timeline")` to confirm the specific rewind target
  checkpoints.
- Prefer `pawgit(action="rewind", target="<target>", dry_run=true)` before
  telling the user to run a real `/pawgit rewind`.
- Never claim that a real rewind has happened after only calling the agent
  tool. Real rewind must go through the `/pawgit rewind ...` slash command.
- Explain that memory rewind affects shared `MEMORY.md` and `memory/` files
  across sessions.
- Explain that `/pawgit reset --confirm` deletes `.pawgit` checkpoints, refs,
  DAG HEAD metadata, and PawGit config, but does not modify user files,
  sessions, `MEMORY.md`, or `memory/`.
- Explain that PawGit rewind does not restore arbitrary workspace/project
  files; it restores conversation context and, when requested, memory sources.
- If Git is missing, tell the user to install Git from
  `https://git-scm.com/downloads`, ensure `git` is on PATH, and restart
  QwenPaw.

## Timeline Reading

The timeline output contains two parts:

- A lightweight ASCII DAG graph based on PawGit metadata.
- Tables grouped by `auto`, `snapshot`, and `pre-rewind`.

In the graph:

- `*` means the current session HEAD.
- `o` means the active path.
- `x` means a branch left behind by rewind.

For `/pawgit timeline --all`, only checkpoints from the current session should
be used with `/pawgit rewind N`. Other sessions are shown for context and do
not expose rewind commands.

## Recommended Workflows

### Before Risky Work

Call:

```text
pawgit(action="snapshot", message="before-risky-change")
```

Then proceed with the change.

### Preview And Rewind Conversation

Call:

```text
pawgit(action="timeline")
pawgit(action="rewind", target="1", dry_run=true)
```

Then ask the user to run `/pawgit rewind 1` in QwenPaw chat if they want the
actual conversation rewind.

### Preview And Rewind Memory

Call:

```text
pawgit(action="timeline")
pawgit(action="rewind", target="<target>", include_memory=true, dry_run=true)
```

After the user confirms the risk, ask the user to run this slash command:

```text
/pawgit rewind <target> --include-memory --confirm
```

### Clean Old Auto Checkpoints

Call:

```text
pawgit(action="gc", dry_run=true)
pawgit(action="gc", dry_run=false, confirm=true)
```

### Reset PawGit State

Call:

```text
pawgit(action="reset", confirm=false)
pawgit(action="reset", confirm=true)
```
