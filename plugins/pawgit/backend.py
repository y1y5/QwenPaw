# -*- coding: utf-8 -*-
"""PawGit plugin entry point."""

from __future__ import annotations

import logging
from pathlib import Path

from qwenpaw.plugins.api import PluginApi

from .handlers import PawGitCommandHandler
from .registry import REGISTRY
from .core.repository import ensure_git_available
from .tools import pawgit

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).parent

_HOOKS_INSTALLED = False


async def _post_reply_auto_snapshot(self_agent, kwargs, output_msg):
    """Schedule a debounced auto snapshot after each agent reply."""
    del kwargs
    del output_msg
    try:
        from qwenpaw.app.agent_context import (
            get_current_channel,
            get_current_session_id,
            get_current_user_id,
        )

        REGISTRY.schedule_auto_snapshot(
            self_agent,
            session_id=get_current_session_id() or "",
            user_id=get_current_user_id() or "",
            channel=get_current_channel() or "console",
        )
    except Exception:
        logger.exception("PawGit post_reply hook failed")
    return None


async def _pre_reply_query_gate(self_agent, kwargs):
    """Wait while a memory rewind has the workspace in maintenance mode."""
    try:
        engine = REGISTRY.get_for_agent(self_agent)
        if engine is not None:
            await engine.query_gate.wait()
    except Exception:
        logger.exception("PawGit pre_reply query gate failed")
    return kwargs


def _install_agent_hooks() -> None:
    """Install AgentScope class hooks once."""
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    ensure_git_available()
    try:
        from qwenpaw.agents.react_agent import QwenPawAgent

        QwenPawAgent.register_class_hook(
            hook_type="pre_reply",
            hook_name="pawgit_query_gate",
            hook=_pre_reply_query_gate,
        )
        QwenPawAgent.register_class_hook(
            hook_type="post_reply",
            hook_name="pawgit_auto_snapshot",
            hook=_post_reply_auto_snapshot,
        )
        _HOOKS_INSTALLED = True
        logger.info("PawGit AgentScope hooks installed")
    except Exception:
        logger.exception("Failed to install PawGit AgentScope hooks")


async def _teardown() -> None:
    """Remove hooks and close registry state."""
    global _HOOKS_INSTALLED
    try:
        from qwenpaw.agents.react_agent import QwenPawAgent

        for hook_type, hook_name in (
            ("pre_reply", "pawgit_query_gate"),
            ("post_reply", "pawgit_auto_snapshot"),
        ):
            remover = getattr(QwenPawAgent, "remove_class_hook", None)
            if callable(remover):
                try:
                    remover(  # pylint: disable=not-callable
                        hook_type,
                        hook_name,
                    )
                except Exception:
                    logger.debug(
                        "Failed to remove PawGit hook %s/%s",
                        hook_type,
                        hook_name,
                        exc_info=True,
                    )
    finally:
        _HOOKS_INSTALLED = False
        await REGISTRY.flush_and_close_all()


class PawGitPlugin:
    """Register PawGit checkpoint and rewind capabilities."""

    def register(self, api: PluginApi) -> None:
        api.register_control_command(
            PawGitCommandHandler(),
            priority_level=10,
        )
        api.register_tool(
            tool_name="pawgit",
            tool_func=pawgit,
            description=(
                "Create PawGit snapshots, inspect timelines, preview or "
                "prepare rewinds, clean checkpoint refs, and reset PawGit."
            ),
            icon="P",
            enabled=True,
        )
        api.register_skill_provider(
            skills_dir=PLUGIN_DIR / "skills",
            enabled_by_default=True,
            channels=["all"],
        )

        api.register_startup_hook(
            "pawgit_install_hooks",
            _install_agent_hooks,
            priority=60,
        )
        api.register_shutdown_hook("pawgit_teardown", _teardown, priority=60)
        logger.info("PawGit plugin registered")


plugin = PawGitPlugin()
