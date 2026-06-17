# -*- coding: utf-8 -*-
"""In-memory PawOne binding registry."""

from __future__ import annotations

import asyncio

from qwenpaw.app.runner.control_commands.base import ControlContext

from .models import Binding, OriginKey, TargetRef


class PawOneRegistry:
    """Stores command-session bindings for the current app process."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._bindings: dict[OriginKey, Binding] = {}

    @staticmethod
    def origin_from_context(context: ControlContext) -> OriginKey:
        channel = getattr(context.channel, "channel", "") or ""
        return OriginKey(
            agent_id=context.agent_id,
            channel=channel,
            user_id=context.user_id,
            session_id=context.session_id,
        )

    async def set_binding(
        self,
        origin: OriginKey,
        target: TargetRef,
    ) -> Binding:
        binding = Binding(target=target)
        async with self._lock:
            self._bindings[origin] = binding
        return binding

    async def get_binding(self, origin: OriginKey) -> Binding | None:
        async with self._lock:
            return self._bindings.get(origin)

    async def clear_binding(self, origin: OriginKey) -> bool:
        async with self._lock:
            return self._bindings.pop(origin, None) is not None

    async def set_last_task(
        self,
        origin: OriginKey,
        task_id: str,
    ) -> None:
        async with self._lock:
            binding = self._bindings.get(origin)
            if binding is not None:
                binding.last_task_id = task_id


REGISTRY = PawOneRegistry()

