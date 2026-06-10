# -*- coding: utf-8 -*-
"""PawGit models, policies, metadata helpers, ref parsing, and rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .utils import session_file_path

EXCLUDE_PATTERNS = (
    ".git/",
    ".pawgit/",
    "/coding_projects/",
    "file_store/",
    ".reme_store_*/",
    "backup/",
    "browser/",
    "__pycache__/",
    "*.pyc",
    "*.log",
)
METADATA_PREFIX = "PawGit-Metadata: "


def exclude_pattern_to_pathspec(pattern: str) -> str:
    """Convert a gitignore-style exclude entry to a git add pathspec."""
    anchored = pattern.startswith("/")
    body = pattern.lstrip("/")
    is_dir = body.endswith("/")
    body = body.rstrip("/")
    scope = "top,glob" if anchored else "glob"
    if is_dir:
        prefix = "" if anchored else "**/"
        return f":(exclude,{scope}){prefix}{body}/**"
    if anchored:
        return f":(exclude,{scope}){body}"
    return f":(exclude,{scope})**/{body}"


SNAPSHOT_EXCLUDE_PATHSPECS = tuple(
    exclude_pattern_to_pathspec(pattern) for pattern in EXCLUDE_PATTERNS
)


@dataclass(frozen=True)
class TimelineEntry:
    """One PawGit checkpoint."""

    ref: str
    kind: str
    session_key: str
    name: str
    commit: str
    timestamp_ms: int
    subject: str
    query: str | None


@dataclass(frozen=True)
class RewindResult:
    """Result of a conv-only rewind."""

    target: str
    commit: str
    restored_paths: tuple[str, ...]
    pre_rewind_ref: str | None
    dry_run: bool


@dataclass(frozen=True)
class GcResult:
    """Result of session-level GC."""

    deleted_refs: tuple[str, ...]
    kept_refs: tuple[str, ...]
    dry_run: bool


class PawGitError(RuntimeError):
    """Raised when PawGit cannot complete an operation."""


def ref_kind(ref: str) -> str:
    """Return auto, snap, pre-rewind, or unknown."""
    parts = ref.split("/")
    return parts[1] if len(parts) > 1 else "unknown"


def ref_session_key(ref: str) -> str:
    """Return the session key encoded in a PawGit ref."""
    if ref.startswith(("refs/auto/", "refs/snap/")):
        parts = ref.split("/")
        return parts[2] if len(parts) > 2 else ""
    if ref.startswith("refs/pre-rewind/"):
        tail = ref.removeprefix("refs/pre-rewind/")
        return tail.split("-", 1)[1] if "-" in tail else ""
    return ""


def ref_display_name(ref: str) -> str:
    """Return the user-facing name or timestamp encoded in a ref."""
    if ref_kind(ref) == "pre-rewind":
        return ref.removeprefix("refs/pre-rewind/").split("-", 1)[0]
    parts = ref.split("/")
    return "/".join(parts[3:]) if len(parts) > 3 else ""


def latest_user_query(
    workspace_dir: Path,
    *,
    session_id: str,
    user_id: str,
    channel: str,
) -> str | None:
    """Return the latest persisted user text for one QwenPaw session."""
    path = session_file_path(
        workspace_dir,
        session_id=session_id,
        user_id=user_id,
        channel=channel,
    )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    content = state.get("agent", {}).get("memory", {}).get("content", [])
    if not isinstance(content, list):
        return None
    for item in reversed(content):
        message = item[0] if isinstance(item, list) and item else item
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = message_text(message.get("content"))
        if text:
            return text
    return None


def message_text(content: object) -> str | None:
    """Extract text blocks from one serialized AgentScope message."""
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    text = "\n".join(part for part in parts if part).strip()
    return text or None


def encode_metadata(query: str | None) -> str:
    """Encode metadata as a single UTF-8-safe commit-message line."""
    payload = json.dumps(
        {"query": query},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{METADATA_PREFIX}{payload}"


def query_from_commit_message(message: str) -> str | None:
    """Read the latest-query field from a PawGit commit message."""
    for line in reversed(message.splitlines()):
        if not line.startswith(METADATA_PREFIX):
            continue
        try:
            metadata = json.loads(line[len(METADATA_PREFIX) :])
        except json.JSONDecodeError:
            return None
        query = metadata.get("query")
        return query if isinstance(query, str) and query else None
    return None


def render_timeline(entries: list[TimelineEntry]) -> str:
    if not entries:
        return "No PawGit checkpoints found for this session."
    lines = ["# PawGit Timeline"]
    group_titles = {
        "auto": "AUTO Checkpoints",
        "snap": "SNAPSHOT Checkpoints",
        "pre-rewind": "PRE-REWIND Checkpoints",
    }
    current_kind: str | None = None
    for idx, entry in enumerate(entries, 1):
        if entry.kind != current_kind:
            current_kind = entry.kind
            lines.extend(
                [
                    "",
                    f"## {group_titles.get(entry.kind, entry.kind.upper())}",
                    "",
                    "| # | Snapshot | SHA | Date | Query | Rewind |",
                    "|---:|---|---|---|---|---|",
                ],
            )
        snapshot_name = entry.name if entry.kind == "snap" else None
        timestamp = datetime.fromtimestamp(
            entry.timestamp_ms / 1000
        ).astimezone()
        date_text = timestamp.strftime("%Y-%m-%d %H:%M:%S %z")
        query = " ".join(entry.query.split()) if entry.query else "N/A"
        if len(query) > 120:
            query = query[:117] + "..."
        query = query.replace("\\", "\\\\").replace("|", "\\|")
        commands = [f"`/rewind {idx}`"]
        if snapshot_name:
            commands.append(f"`/rewind {snapshot_name}`")
        commands.append(f"`/rewind {entry.commit[:12]}`")
        snapshot = f"`{snapshot_name}`" if snapshot_name else "N/A"
        lines.append(
            f"| {idx} | {snapshot} | `{entry.commit[:12]}` | "
            f"{date_text} | {query} | {'<br>'.join(commands)} |",
        )
    return "\n".join(lines)


def render_rewind(result: RewindResult) -> str:
    mode = "Dry run" if result.dry_run else "Rewind complete"
    lines = [f"**PawGit {mode}**"]
    lines.append(f"- Target: `{result.target}`")
    lines.append(f"- Commit: `{result.commit[:12]}`")
    lines.append(
        f"- Restored: {', '.join(f'`{p}`' for p in result.restored_paths)}"
    )
    if result.pre_rewind_ref:
        lines.append(f"- Safety ref: `{result.pre_rewind_ref}`")
    return "\n".join(lines)


def render_gc(result: GcResult) -> str:
    removed_label = "Would remove" if result.dry_run else "Removed"
    status = "Preview (no changes made)" if result.dry_run else "Completed"
    lines = [
        "# PawGit GC",
        "",
        f"**Status:** {status}",
        "",
        "| Result | Count |",
        "|---|---:|",
        f"| {removed_label} refs | {len(result.deleted_refs)} |",
        f"| Kept refs | {len(result.kept_refs)} |",
    ]
    _render_gc_ref_table(
        lines,
        title=f"{removed_label} Refs",
        refs=result.deleted_refs,
        empty_text="No refs matched the cleanup policy.",
    )
    _render_gc_ref_table(
        lines,
        title="Kept Refs",
        refs=result.kept_refs,
        empty_text="No collectible refs were retained.",
    )
    return "\n".join(lines)


def _render_gc_ref_table(
    lines: list[str],
    *,
    title: str,
    refs: tuple[str, ...],
    empty_text: str,
) -> None:
    lines.extend(["", f"## {title}", ""])
    if not refs:
        lines.append(empty_text)
        return
    lines.extend(["| Type | Session | Name |", "|---|---|---|"])
    for ref in refs:
        kind = ref_kind(ref)
        session = ref_session_key(ref) or "N/A"
        name = ref_display_name(ref) or "N/A"
        lines.append(f"| {kind} | `{session}` | `{name}` |")
