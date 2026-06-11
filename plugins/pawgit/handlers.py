# -*- coding: utf-8 -*-
"""PawGit slash command handlers."""

from __future__ import annotations

from qwenpaw.app.runner.control_commands.base import (
    BaseControlCommandHandler,
    ControlContext,
)

from .registry import REGISTRY
from .utils import first_positional, parse_flags


def _parse_limit(raw: str, *, default: int, maximum: int) -> int:
    for part in (raw or "").split():
        if part.startswith("--limit="):
            try:
                return max(1, min(maximum, int(part.split("=", 1)[1])))
            except ValueError:
                return default
    return default


class TimelineCommandHandler(BaseControlCommandHandler):
    """Render checkpoints for the current session."""

    command_name = "/timeline"

    async def handle(self, context: ControlContext) -> str:
        engine = REGISTRY.get_for_workspace(context.workspace)
        raw = context.args.get("_raw_args", "")
        entries = await engine.timeline(
            session_id=context.session_id,
            user_id=context.user_id,
            channel=context.channel.channel,
            limit=_parse_limit(
                raw,
                default=engine.timeline_default_limit,
                maximum=engine.timeline_max_limit,
            ),
            include_all="--all" in parse_flags(raw),
        )
        return engine.render_timeline(entries)


class SnapshotCommandHandler(BaseControlCommandHandler):
    """Create a permanent milestone snapshot."""

    command_name = "/snapshot"

    async def handle(self, context: ControlContext) -> str:
        engine = REGISTRY.get_for_workspace(context.workspace)
        raw = context.args.get("_raw_args", "")
        name = await engine.snapshot(
            session_id=context.session_id,
            user_id=context.user_id,
            channel=context.channel.channel,
            message=raw,
        )
        return f"Permanent PawGit snapshot created: `{name}`"


class RewindCommandHandler(BaseControlCommandHandler):
    """Conv-only rewind for the current session."""

    command_name = "/rewind"

    async def handle(self, context: ControlContext) -> str:
        raw = context.args.get("_raw_args", "")
        if "--include-memory" in parse_flags(raw):
            return (
                "**Unsupported in Phase 1**\n\n"
                "`/rewind --include-memory` is planned for Phase 2. "
                "Phase 1 supports conv-only rewind."
            )
        engine = REGISTRY.get_for_workspace(context.workspace)
        result = await engine.rewind(
            target=first_positional(raw),
            session_id=context.session_id,
            user_id=context.user_id,
            channel=context.channel.channel,
            dry_run="--dry-run" in parse_flags(raw),
        )
        return engine.render_rewind(result)


class GcCommandHandler(BaseControlCommandHandler):
    """Collect non-snapshot checkpoints."""

    command_name = "/gc"

    async def handle(self, context: ControlContext) -> str:
        raw = context.args.get("_raw_args", "")
        flags = parse_flags(raw)
        engine = REGISTRY.get_for_workspace(context.workspace)
        result = await engine.gc(
            session_id=context.session_id,
            user_id=context.user_id,
            channel=context.channel.channel,
            compact="--compact" in flags,
            all_sessions="--all-sessions" in flags,
            dry_run="--dry-run" in flags,
        )
        return engine.render_gc(result)
