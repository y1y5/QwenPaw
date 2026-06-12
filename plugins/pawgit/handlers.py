# -*- coding: utf-8 -*-
"""Unified PawGit slash command handler."""

from __future__ import annotations

from qwenpaw.app.runner.control_commands.base import (
    BaseControlCommandHandler,
    ControlContext,
)

from .registry import REGISTRY
from .utils import first_positional, parse_flags

PAWGIT_HELP = (
    "# PawGit\n\n"
    "| Command | Description |\n"
    "|---|---|\n"
    "| `/pawgit timeline [--limit=N] [--all]` "
    "| Show checkpoints for this session. |\n"
    "| `/pawgit snapshot [message]` "
    "| Create a permanent named checkpoint. |\n"
    "| `/pawgit rewind <N\\|snap_name\\|sha> [--dry-run]` "
    "| Restore this conversation. |\n"
    "| `/pawgit rewind <target> --include-memory "
    "[--dry-run\\|--confirm]` "
    "| Restore this conversation and memory source files. |\n"
    "| `/pawgit gc [--compact] [--all-sessions] [--dry-run]` "
    "| Clean collectible checkpoints. |\n"
    "| `/pawgit --help` | Show this help. |\n\n"
    "Memory rewind requires `--confirm` unless `--dry-run` is used.\n"
)


def _parse_limit(raw: str, *, default: int, maximum: int) -> int:
    for part in (raw or "").split():
        if part.startswith("--limit="):
            try:
                return max(1, min(maximum, int(part.split("=", 1)[1])))
            except ValueError:
                return default
    return default


def _split_subcommand(raw: str) -> tuple[str, str]:
    """Return a normalized subcommand and its untouched arguments."""
    parts = (raw or "").strip().split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


class PawGitCommandHandler(BaseControlCommandHandler):
    """Dispatch all PawGit operations through `/pawgit`."""

    command_name = "/pawgit"

    async def handle(self, context: ControlContext) -> str:
        raw = context.args.get("_raw_args", "")
        subcommand, subargs = _split_subcommand(raw)
        if subcommand in {"", "help", "--help", "-h"}:
            return PAWGIT_HELP
        if subcommand == "timeline":
            return await self._timeline(context, subargs)
        if subcommand == "snapshot":
            return await self._snapshot(context, subargs)
        if subcommand == "rewind":
            return await self._rewind(context, subargs)
        if subcommand == "gc":
            return await self._gc(context, subargs)
        return (
            f"**Unknown PawGit subcommand:** `{subcommand}`\n\n"
            f"{PAWGIT_HELP}"
        )

    @staticmethod
    async def _timeline(context: ControlContext, raw: str) -> str:
        engine = REGISTRY.get_for_workspace(context.workspace)
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

    @staticmethod
    async def _snapshot(context: ControlContext, raw: str) -> str:
        engine = REGISTRY.get_for_workspace(context.workspace)
        name = await engine.snapshot(
            session_id=context.session_id,
            user_id=context.user_id,
            channel=context.channel.channel,
            message=raw,
        )
        return f"Permanent PawGit snapshot created: `{name}`"

    @staticmethod
    async def _rewind(context: ControlContext, raw: str) -> str:
        flags = parse_flags(raw)
        include_memory = "--include-memory" in flags
        dry_run = "--dry-run" in flags
        if include_memory and not dry_run and "--confirm" not in flags:
            return (
                "**Confirmation Required**\n\n"
                "This operation rewinds `MEMORY.md`, `memory/`, and the "
                "current conversation. Memory changes made after the target "
                "checkpoint, including changes from other sessions, will be "
                "discarded.\n\n"
                "Run the command again with `--confirm` to continue."
            )
        engine = REGISTRY.get_for_workspace(context.workspace)
        rewind = engine.rewind_with_memory if include_memory else engine.rewind
        result = await rewind(
            target=first_positional(raw),
            session_id=context.session_id,
            user_id=context.user_id,
            channel=context.channel.channel,
            dry_run=dry_run,
        )
        return engine.render_rewind(result)

    @staticmethod
    async def _gc(context: ControlContext, raw: str) -> str:
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
