# -*- coding: utf-8 -*-
"""PawGit plugin entry point."""

from __future__ import annotations

import logging

from qwenpaw.plugins.api import PluginApi

from .handlers import (
    GcCommandHandler,
    RewindCommandHandler,
    SnapshotCommandHandler,
    TimelineCommandHandler,
)
from .registry import REGISTRY

logger = logging.getLogger(__name__)


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
    """Phase 1 no-op query gate placeholder for Phase 2 memory rewind."""
    del self_agent
    return kwargs


def _install_agent_hooks() -> None:
    """Install AgentScope class hooks once."""
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
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
                        hook_type, hook_name
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
    """Register PawGit Phase 1 capabilities."""

    def register(self, api: PluginApi) -> None:
        for handler in (
            TimelineCommandHandler(),
            SnapshotCommandHandler(),
            RewindCommandHandler(),
            GcCommandHandler(),
        ):
            api.register_control_command(handler, priority_level=10)

        api.register_startup_hook(
            "pawgit_install_hooks",
            _install_agent_hooks,
            priority=60,
        )
        api.register_shutdown_hook("pawgit_teardown", _teardown, priority=60)
        logger.info("PawGit plugin registered")


plugin = PawGitPlugin()
