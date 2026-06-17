# -*- coding: utf-8 -*-
"""PawOne command tests."""

# pylint: disable=protected-access,redefined-outer-name
# pylint: disable=wrong-import-order,wrong-import-position

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "plugins"
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from PawOne.handlers import PAWONE_HELP, PawOneCommandHandler  # noqa: E402
from PawOne.models import OriginKey, TargetRef  # noqa: E402
from PawOne.registry import PawOneRegistry  # noqa: E402
from PawOne.service import PawOneService  # noqa: E402


class FakeChat:
    """Minimal chat object for rendering tests."""

    def __init__(
        self,
        *,
        chat_id: str,
        name: str,
        channel: str,
        user_id: str,
        session_id: str,
        status: str = "idle",
    ) -> None:
        self.id = chat_id
        self.name = name
        self.channel = channel
        self.user_id = user_id
        self.session_id = session_id
        self.updated_at = datetime(2026, 6, 16, tzinfo=timezone.utc)
        self.status = status


class FakeService:
    """Deterministic service used by PawOne command tests."""

    def __init__(self) -> None:
        self.chats = [
            FakeChat(
                chat_id="aaaaaaaa-1111",
                name="Console Work",
                channel="console",
                user_id="default",
                session_id="console:default",
            ),
            FakeChat(
                chat_id="bbbbbbbb-2222",
                name="Ding Talk",
                channel="dingtalk",
                user_id="ding-user",
                session_id="dingtalk:ding-user",
            ),
        ]
        self.sent: list[tuple[OriginKey, TargetRef, str]] = []
        self.stopped = False

    def list_agents(self):
        return [
            {
                "id": "default",
                "enabled": True,
                "workspace_dir": "workspace/default",
            },
            {
                "id": "coding-agent",
                "enabled": True,
                "workspace_dir": "workspace/coding-agent",
            },
        ]

    async def list_chats(
        self,
        _context,
        _agent_id,
        *,
        channel=None,
        limit=20,
    ):
        chats = self.chats
        if channel:
            chats = [chat for chat in chats if chat.channel == channel]
        return chats[:limit]

    async def resolve_target(
        self,
        context,
        agent_id,
        selector,
        *,
        channel=None,
    ):
        chats = await self.list_chats(
            context,
            agent_id,
            channel=channel,
            limit=100,
        )
        if selector.isdigit():
            chat = chats[int(selector) - 1]
        else:
            chat = next(chat for chat in chats if chat.id.startswith(selector))
        return TargetRef(
            agent_id=agent_id,
            chat_id=chat.id,
            channel=chat.channel,
            user_id=chat.user_id,
            session_id=chat.session_id,
            name=chat.name,
        )

    async def send(self, _context, origin, target, text):
        self.sent.append((origin, target, text))
        return f"reply to {text}"

    async def submit(self, _origin, _target, _text):
        return {"task_id": "task-1"}

    async def task_status(self, _target, task_id):
        return {"status": "running", "updated_at": "now", "task": task_id}

    async def chat_status(self, _context, _target):
        return "idle"

    async def stop_chat(self, _context, _target):
        self.stopped = True
        return True


class FakeChannel:
    """Captures proactive target sends."""

    channel = "dingtalk"

    def __init__(self) -> None:
        self.sent = []

    def to_handle_from_target(self, *, user_id, session_id):
        return f"handle:{user_id}:{session_id}"

    async def send(self, to_handle, text, meta=None):
        self.sent.append((to_handle, text, meta or {}))


class FakeChannelManager:
    """Single-channel manager facade."""

    def __init__(self, channel) -> None:
        self.channel = channel

    async def get_channel(self, _name):
        return self.channel


def _context(raw_args: str):
    return SimpleNamespace(
        agent_id="default",
        channel=SimpleNamespace(channel="dingtalk"),
        user_id="ding-user",
        session_id="dingtalk:ding-user",
        workspace=SimpleNamespace(),
        args={"_raw_args": raw_args},
    )


@pytest.fixture
def handler():
    return PawOneCommandHandler(
        service=FakeService(),
        registry=PawOneRegistry(),
    )


@pytest.mark.asyncio
async def test_help_and_agents_render(handler):
    assert await handler.handle(_context("--help")) == PAWONE_HELP

    output = await handler.handle(_context("ls --agents"))

    assert "| Agent | Enabled | Workspace |" in output
    assert "`coding-agent`" in output


@pytest.mark.asyncio
async def test_list_renders_chat_table(handler):
    output = await handler.handle(
        _context("ls coding-agent --channel=dingtalk --limit=1"),
    )

    assert "# PawOne Chats: `coding-agent`" in output
    assert "| N | Name | Chat | Channel | User | Updated | Status |" in output
    assert "Ding Talk" in output
    assert "`dingtalk`" in output
    assert "`bbbbbbbb`" in output


@pytest.mark.asyncio
async def test_open_current_send_and_close(handler):
    opened = await handler.handle(_context("use coding-agent 1"))
    assert "PawOne binding opened" in opened
    assert "Agent   : coding-agent" in opened

    current = await handler.handle(_context("current"))
    assert "Current PawOne target" in current
    assert "Console Work (aaaaaaaa)" in current

    reply = await handler.handle(_context("hello target"))
    assert "PawOne reply target" in reply
    assert "reply to hello target" in reply
    assert handler.service.sent[0][0].channel == "dingtalk"

    closed = await handler.handle(_context("close"))
    assert "Binding closed" in closed
    assert "No active binding" in await handler.handle(_context("current"))


@pytest.mark.asyncio
async def test_submit_status_and_stop(handler):
    await handler.handle(_context("use coding-agent bbbbbbbb"))

    submitted = await handler.handle(_context("run run tests"))
    assert "Task: task-1" in submitted
    assert "PawOne task submitted" in submitted

    status = await handler.handle(_context("status"))
    assert "PawOne task status" in status
    assert "running" in status
    assert "task-1" in status

    stopped = await handler.handle(_context("stop"))
    assert "PawOne stop requested" in stopped
    assert handler.service.stopped is True


@pytest.mark.asyncio
async def test_service_echoes_reply_to_target_channel():
    service = PawOneService()
    channel = FakeChannel()
    context = SimpleNamespace(
        agent_id="default",
        workspace=SimpleNamespace(
            channel_manager=FakeChannelManager(channel),
        ),
    )
    origin = OriginKey(
        agent_id="console-agent",
        channel="console",
        user_id="console-user",
        session_id="console:console-user",
    )
    target = TargetRef(
        agent_id="default",
        chat_id="chat-1",
        channel="dingtalk",
        user_id="ding-user",
        session_id="ding-session",
        name="Ding",
    )

    echoed = await service.echo_to_target(
        context,
        origin,
        target,
        "remote reply",
    )

    assert echoed is True
    assert channel.sent == [
        (
            "handle:ding-user:ding-session",
            "remote reply",
            {
                "session_id": "ding-session",
                "user_id": "ding-user",
                "channel": "dingtalk",
            },
        ),
    ]
