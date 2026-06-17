# -*- coding: utf-8 -*-
"""Agent tool entry point for PawGit."""

from __future__ import annotations

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from qwenpaw.app.agent_context import (
    get_current_channel,
    get_current_session_id,
    get_current_user_id,
)
from qwenpaw.config.context import get_current_workspace_dir

from .registry import REGISTRY


def _text_response(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _bool(value: bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _context() -> tuple[object, str, str, str] | str:
    workspace_dir = get_current_workspace_dir()
    session_id = get_current_session_id()
    if workspace_dir is None:
        return "ERROR: PawGit cannot find the current workspace."
    if not session_id:
        return "ERROR: PawGit cannot find the current session."
    return (
        REGISTRY.get_for_workspace_dir(workspace_dir),
        session_id,
        get_current_user_id() or "",
        get_current_channel() or "console",
    )


async def pawgit(
    action: str,
    target: str = "",
    message: str = "",
    limit: int | None = None,
    include_all: bool | str = False,
    include_memory: bool | str = False,
    dry_run: bool | str = True,
    compact: bool | str = False,
    all_sessions: bool | str = False,
    confirm: bool | str = False,
) -> ToolResponse:
    """Run PawGit from an agent tool call.

    Args:
        action: One of "timeline", "snapshot", "rewind", "gc", "reset",
            or "help".
        target: Rewind target: timeline index, snapshot name, or commit SHA.
        message: Snapshot message/name.
        limit: Timeline row limit.
        include_all: Include checkpoints from all sessions in timeline.
        include_memory: Also restore MEMORY.md and memory/ on rewind.
        dry_run: Preview rewind/gc without changing state. Defaults to true.
        compact: Delete all collectible auto/pre-rewind refs during gc.
        all_sessions: Let gc operate on all sessions.
        confirm: Required for mutating gc and reset. Real rewind is blocked
            in the agent tool and must use the /pawgit slash command.

    Returns:
        ToolResponse with the same style of output as /pawgit commands.
    """
    action = (action or "").strip().lower()
    if action in {"", "help"}:
        return _text_response(
            "PawGit tool actions: timeline, snapshot, rewind, gc, reset.\n"
            "Examples:\n"
            '- pawgit(action="snapshot", message="before-refactor")\n'
            '- pawgit(action="timeline", limit=10)\n'
            '- pawgit(action="rewind", target="1", dry_run=True)\n'
            '- pawgit(action="rewind", target="1", dry_run=True)\n'
            '- pawgit(action="gc", dry_run=True)\n',
        )

    ctx = _context()
    if isinstance(ctx, str):
        return _text_response(ctx)
    engine, session_id, user_id, channel = ctx

    if action == "timeline":
        entries = await engine.timeline(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            limit=limit,
            include_all=_bool(include_all),
        )
        return _text_response(engine.render_timeline(entries))

    if action == "snapshot":
        name = await engine.snapshot(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            message=message,
        )
        return _text_response(f"Permanent PawGit snapshot created: `{name}`")

    if action == "rewind":
        if not _bool(dry_run):
            command = f"/pawgit rewind {target}".strip()
            if _bool(include_memory):
                command = f"{command} --include-memory"
            return _text_response(
                "CONTROL_COMMAND_REQUIRED: PawGit real rewind changes the "
                "current conversation context and may require the app to "
                "reload session state. Agent tools can preview rewinds, but "
                "real rewind must run through QwenPaw's slash-command "
                "path.\n\n"
                f"Ask the user to run: `{command}`",
            )
        rewind = (
            engine.rewind_with_memory
            if _bool(include_memory)
            else engine.rewind
        )
        result = await rewind(
            target=target,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            dry_run=_bool(dry_run),
        )
        return _text_response(engine.render_rewind(result))

    if action == "gc":
        if not _bool(dry_run) and not _bool(confirm):
            return _text_response(
                "CONFIRMATION_REQUIRED: rerun with confirm=True to delete "
                "collectible PawGit refs.",
            )
        result = await engine.gc(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            compact=_bool(compact),
            all_sessions=_bool(all_sessions),
            dry_run=_bool(dry_run),
        )
        return _text_response(engine.render_gc(result))

    if action == "reset":
        if not _bool(confirm):
            return _text_response(
                "CONFIRMATION_REQUIRED: reset deletes `.pawgit` checkpoints, "
                "refs, timeline metadata, and PawGit config. Rerun with "
                "confirm=True to continue.",
            )
        await engine.reset()
        return _text_response(
            "**PawGit reset complete**\n\n"
            "Deleted and recreated `.pawgit` for this workspace.",
        )

    return _text_response(f"ERROR: unknown PawGit action `{action}`.")
