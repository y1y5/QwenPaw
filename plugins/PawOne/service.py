# -*- coding: utf-8 -*-
"""QwenPaw runtime adapter used by PawOne commands."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from qwenpaw.agents.tools.agent_management import (
    collect_final_agent_chat_response,
    extract_agent_text_content,
    get_agent_chat_task_status,
    submit_agent_chat_task,
)
from qwenpaw.app.runner.control_commands.base import ControlContext
from qwenpaw.app.runner.models import ChatSpec
from qwenpaw.config.utils import load_config

from .models import OriginKey, TargetRef

logger = logging.getLogger(__name__)


class PawOneError(RuntimeError):
    """Raised for user-facing PawOne failures."""


class PawOneService:
    """Small facade over QwenPaw's existing multi-agent runtime."""

    def list_agents(self) -> list[dict[str, Any]]:
        config = load_config()
        result = []
        for agent_id, profile in config.agents.profiles.items():
            result.append(
                {
                    "id": agent_id,
                    "enabled": getattr(profile, "enabled", True),
                    "workspace_dir": getattr(profile, "workspace_dir", ""),
                },
            )
        return result

    async def list_chats(
        self,
        context: ControlContext,
        agent_id: str,
        *,
        channel: str | None = None,
        limit: int = 20,
    ) -> list[ChatSpec]:
        workspace = await self._get_workspace(context, agent_id)
        chats = await workspace.chat_manager.list_chats(channel=channel)
        chats.sort(key=lambda chat: chat.updated_at, reverse=True)
        return chats[:limit]

    async def resolve_target(
        self,
        context: ControlContext,
        agent_id: str,
        selector: str,
        *,
        channel: str | None = None,
    ) -> TargetRef:
        chats = await self.list_chats(
            context,
            agent_id,
            channel=channel,
            limit=100,
        )
        chat = self._select_chat(chats, selector)
        if chat is None:
            raise PawOneError(
                f"Target chat `{selector}` was not found for `{agent_id}`.",
            )
        return self._target_from_chat(agent_id, chat)

    async def send(
        self,
        context: ControlContext,
        origin: OriginKey,
        target: TargetRef,
        text: str,
        *,
        timeout: int = 300,
    ) -> str:
        payload = self._build_request_payload(origin, target, text)
        response_data = await asyncio.to_thread(
            collect_final_agent_chat_response,
            None,
            payload,
            target.agent_id,
            timeout,
        )
        if not response_data:
            return ""
        reply = extract_agent_text_content(response_data)
        if reply:
            await self.echo_to_target(context, origin, target, reply)
        return reply

    async def echo_to_target(
        self,
        context: ControlContext,
        origin: OriginKey,
        target: TargetRef,
        text: str,
    ) -> bool:
        if self._is_same_conversation(origin, target):
            return False
        try:
            workspace = await self._get_workspace(context, target.agent_id)
            manager = getattr(workspace, "channel_manager", None)
            if manager is None:
                return False
            channel = await manager.get_channel(target.channel)
            if channel is None:
                return False
            to_handle = channel.to_handle_from_target(
                user_id=target.user_id,
                session_id=target.session_id,
            )
            meta = dict(target.meta or {})
            meta.setdefault("session_id", target.session_id)
            meta.setdefault("user_id", target.user_id)
            meta.setdefault("channel", target.channel)
            await channel.send(to_handle, text, meta)
            return True
        except Exception:
            logger.exception(
                "PawOne target echo failed: agent=%s channel=%s session=%s",
                target.agent_id,
                target.channel,
                target.session_id,
            )
            return False

    async def submit(
        self,
        origin: OriginKey,
        target: TargetRef,
        text: str,
        *,
        timeout: int = 30,
        task_timeout: float | None = None,
    ) -> dict[str, Any]:
        payload = self._build_request_payload(origin, target, text)
        return await asyncio.to_thread(
            submit_agent_chat_task,
            None,
            payload,
            target.agent_id,
            timeout,
            task_timeout,
        )

    async def task_status(
        self,
        target: TargetRef,
        task_id: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            get_agent_chat_task_status,
            None,
            task_id,
            target.agent_id,
            10,
        )

    async def chat_status(
        self,
        context: ControlContext,
        target: TargetRef,
    ) -> str:
        workspace = await self._get_workspace(context, target.agent_id)
        return await workspace.task_tracker.get_status(target.chat_id)

    async def stop_chat(
        self,
        context: ControlContext,
        target: TargetRef,
    ) -> bool:
        workspace = await self._get_workspace(context, target.agent_id)
        return await workspace.task_tracker.request_stop(target.chat_id)

    async def _get_workspace(
        self,
        context: ControlContext,
        agent_id: str,
    ) -> Any:
        if agent_id == context.agent_id:
            return context.workspace
        manager = getattr(context.workspace, "_manager", None)
        if manager is None:
            raise PawOneError(
                "Multi-agent manager is unavailable in this runtime.",
            )
        return await manager.get_agent(agent_id)

    @staticmethod
    def _select_chat(
        chats: list[ChatSpec],
        selector: str,
    ) -> ChatSpec | None:
        value = selector.strip()
        if value.isdigit():
            index = int(value)
            if 1 <= index <= len(chats):
                return chats[index - 1]
            return None
        lowered = value.lower()
        matches = [
            chat
            for chat in chats
            if chat.id.lower().startswith(lowered)
            or chat.name.lower() == lowered
            or chat.name.lower().startswith(lowered)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _target_from_chat(agent_id: str, chat: ChatSpec) -> TargetRef:
        return TargetRef(
            agent_id=agent_id,
            chat_id=chat.id,
            channel=chat.channel,
            user_id=chat.user_id,
            session_id=chat.session_id,
            name=chat.name,
            meta=dict(chat.meta or {}),
        )

    @staticmethod
    def _build_request_payload(
        origin: OriginKey,
        target: TargetRef,
        text: str,
    ) -> dict[str, Any]:
        return {
            "session_id": target.session_id,
            "user_id": target.user_id,
            "channel": target.channel,
            "root_session_id": origin.session_id,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            ],
            "request_context": {
                "root_agent_id": origin.agent_id,
                "root_session_id": origin.session_id,
                "pawone_origin_agent_id": origin.agent_id,
                "pawone_origin_channel": origin.channel,
                "pawone_origin_user_id": origin.user_id,
                "pawone_origin_session_id": origin.session_id,
            },
        }

    @staticmethod
    def _is_same_conversation(
        origin: OriginKey,
        target: TargetRef,
    ) -> bool:
        return (
            origin.agent_id == target.agent_id
            and origin.channel == target.channel
            and origin.user_id == target.user_id
            and origin.session_id == target.session_id
        )


def format_time(value: Any) -> str:
    """Format datetimes for compact command output."""

    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")
