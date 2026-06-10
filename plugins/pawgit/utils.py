# -*- coding: utf-8 -*-
"""Shared helpers for PawGit."""

from __future__ import annotations

import re
from pathlib import Path

from qwenpaw.app.runner.session import sanitize_filename


_REF_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._/-]+")
_REF_DOTLOCK_RE = re.compile(r"\.lock(?:/|$)")


def session_key(*, channel: str, user_id: str, session_id: str) -> str:
    """Return a filesystem/ref-safe key for a QwenPaw session."""
    raw = f"{channel or 'unknown'}-{user_id or 'anonymous'}-{session_id or 'default'}"
    safe = sanitize_filename(raw).strip("-_.")
    return safe or "default"


def sanitize_ref_component(value: str, *, fallback: str = "snapshot") -> str:
    """Sanitize user text for use as one component of a git ref."""
    value = (value or "").strip()
    if not value:
        return fallback
    value = value.replace("\\", "/")
    value = _REF_UNSAFE_RE.sub("-", value)
    value = re.sub(r"-{2,}", "-", value).strip("/.-")
    value = value.replace("..", ".")
    value = _REF_DOTLOCK_RE.sub("-", value)
    return value[:80] or fallback


def session_file_path(
    workspace_dir: Path,
    *,
    session_id: str,
    user_id: str,
    channel: str,
) -> Path:
    """Return the QwenPaw SafeJSONSession path for a session."""
    safe_sid = sanitize_filename(session_id)
    safe_uid = sanitize_filename(user_id) if user_id else ""
    filename = f"{safe_uid}_{safe_sid}.json" if safe_uid else f"{safe_sid}.json"
    if channel:
        return workspace_dir / "sessions" / sanitize_filename(channel) / filename
    return workspace_dir / "sessions" / filename


def parse_flags(raw: str) -> set[str]:
    """Return command-line flags from a raw slash-command argument string."""
    return {part for part in (raw or "").split() if part.startswith("--")}


def first_positional(raw: str) -> str | None:
    """Return the first non-flag token from raw command args."""
    for part in (raw or "").split():
        if not part.startswith("--"):
            return part
    return None
