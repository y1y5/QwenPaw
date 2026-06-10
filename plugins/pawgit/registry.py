# -*- coding: utf-8 -*-
"""Per-workspace PawGit engine registry and debounce scheduling."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Coroutine

from .engine import PawGitEngine
from .utils import session_key

logger = logging.getLogger(__name__)


class Debouncer:
    """Asyncio debounce helper keyed by workspace/session."""

    def __init__(self, delay: float = 1.5):
        self.delay = delay
        self._pending: dict[str, asyncio.TimerHandle] = {}

    def schedule(
        self, key: str, coro_factory: Callable[[], Coroutine[Any, Any, None]]
    ) -> None:
        loop = asyncio.get_running_loop()
        handle = self._pending.pop(key, None)
        if handle is not None:
            handle.cancel()

        def _run() -> None:
            self._pending.pop(key, None)
            asyncio.create_task(coro_factory())

        self._pending[key] = loop.call_later(self.delay, _run)

    async def flush(self) -> None:
        handles = list(self._pending.items())
        self._pending.clear()
        tasks = []
        for _, handle in handles:
            if not handle.cancelled():
                handle.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def cancel_all(self) -> None:
        for handle in self._pending.values():
            handle.cancel()
        self._pending.clear()


class PawGitRegistry:
    """Global plugin registry with one engine per workspace."""

    def __init__(self):
        self._engines: dict[str, PawGitEngine] = {}
        self._lock = threading.Lock()
        self.debouncer = Debouncer()

    def get_for_workspace(self, workspace) -> PawGitEngine:
        return self._get(str(workspace.workspace_dir))

    def get_for_agent(self, agent) -> PawGitEngine | None:
        workspace_dir = getattr(agent, "_workspace_dir", None)
        if not workspace_dir:
            return None
        return self._get(str(workspace_dir))

    def get_for_workspace_dir(self, workspace_dir: str | Path) -> PawGitEngine:
        return self._get(str(workspace_dir))

    def _get(self, workspace_dir: str) -> PawGitEngine:
        key = str(Path(workspace_dir).expanduser().resolve())
        with self._lock:
            engine = self._engines.get(key)
            if engine is None:
                engine = PawGitEngine(key)
                self._engines[key] = engine
            return engine

    def schedule_auto_snapshot(
        self,
        agent,
        *,
        session_id: str,
        user_id: str,
        channel: str,
    ) -> None:
        engine = self.get_for_agent(agent)
        if engine is None or not session_id:
            return
        key = session_key(
            channel=channel, user_id=user_id, session_id=session_id
        )
        debounce_key = f"{engine.workspace_dir}:{key}"

        async def _snapshot() -> None:
            try:
                await engine.make_snapshot(
                    kind="auto",
                    session_id=session_id,
                    user_id=user_id,
                    channel=channel,
                    message="Auto checkpoint after reply",
                )
            except Exception:
                logger.exception("PawGit auto snapshot failed")

        self.debouncer.schedule(debounce_key, _snapshot)

    async def flush_and_close_all(self) -> None:
        self.debouncer.cancel_all()
        self._engines.clear()


REGISTRY = PawGitRegistry()
