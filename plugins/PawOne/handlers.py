# -*- coding: utf-8 -*-
"""PawOne slash command handler."""

from __future__ import annotations

from typing import Any

from qwenpaw.app.runner.control_commands.base import (
    BaseControlCommandHandler,
    ControlContext,
)

from .models import Binding, TargetRef
from .registry import REGISTRY, PawOneRegistry
from .service import PawOneError, PawOneService, format_time

PAWONE_HELP = (
    "# PawOne\n\n"
    "| Command | Description |\n"
    "|---|---|\n"
    "| `/pawone ls --agents` | List configured agents. |\n"
    "| `/pawone ls [agent_id] [--channel=name] [--limit=N]` "
    "| List recent chats. |\n"
    "| `/pawone use <agent_id> <chat_id\\|N> [--channel=name]` "
    "| Bind this conversation to a target chat. |\n"
    "| `/pawone <message>` "
    "| Send to the bound chat and wait for the reply. |\n"
    "| `/pawone run <message>` | Submit a background target task. |\n"
    "| `/pawone status` | Show the last task or bound chat status. |\n"
    "| `/pawone stop` | Request stop for the bound target chat. |\n"
    "| `/pawone close` | Clear this conversation's binding. |\n"
    "| `/pawone --help` | Show this help. |\n\n"
    "Compatibility aliases: `agents`, `list`, `open`, `current`, "
    "`send`, and `submit` still work.\n\n"
)


def _split_subcommand(raw: str) -> tuple[str, str]:
    parts = (raw or "").strip().split(maxsplit=1)
    if not parts:
        return "", ""
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


def _parse_flags(raw: str) -> tuple[list[str], dict[str, str]]:
    args: list[str] = []
    flags: dict[str, str] = {}
    for part in (raw or "").split():
        if part.startswith("--") and "=" in part:
            key, value = part.split("=", 1)
            flags[key] = value
        elif part.startswith("--"):
            flags[part] = "true"
        else:
            args.append(part)
    return args, flags


def _parse_limit(raw: str, *, default: int = 20, maximum: int = 100) -> int:
    _, flags = _parse_flags(raw)
    try:
        return max(1, min(maximum, int(flags.get("--limit", default))))
    except ValueError:
        return default


def _target_line(target: TargetRef) -> str:
    return (
        f"`{target.agent_id}` / `{target.channel}` / "
        f"`{target.name}` (`{target.chat_id[:8]}`)"
    )


def _box(title: str, body: str) -> str:
    return f"```text\n{title}\n{body}\n```"


def _details(target: TargetRef) -> str:
    return "\n".join(
        [
            f"Agent   : {target.agent_id}",
            f"Channel : {target.channel}",
            f"Chat    : {target.name} ({target.chat_id[:8]})",
            f"User    : {target.user_id}",
            f"Session : {target.session_id}",
        ],
    )


class PawOneCommandHandler(BaseControlCommandHandler):
    """Dispatch all PawOne operations through `/pawone`."""

    command_name = "/pawone"
    description = "Control another QwenPaw agent session."

    def __init__(
        self,
        *,
        service: PawOneService | None = None,
        registry: PawOneRegistry = REGISTRY,
    ) -> None:
        self.service = service or PawOneService()
        self.registry = registry

    async def handle(self, context: ControlContext) -> str:
        raw = context.args.get("_raw_args", "")
        subcommand, subargs = _split_subcommand(raw)
        try:
            return await self._dispatch(context, subcommand, subargs)
        except PawOneError as exc:
            return f"**PawOne Error**\n\n{exc}"

    async def _dispatch(
        self,
        context: ControlContext,
        subcommand: str,
        subargs: str,
    ) -> str:
        if subcommand in {"", "help", "--help", "-h"}:
            return PAWONE_HELP
        if subcommand == "agents":
            return self._render_agents(self.service.list_agents())
        if subcommand in {"ls", "list"}:
            return await self._list(context, subargs)
        if subcommand in {"use", "open"}:
            return await self._open(context, subargs)
        if subcommand == "current":
            return await self._current(context)
        if subcommand == "send":
            return await self._send(context, subargs)
        if subcommand in {"run", "submit"}:
            return await self._submit(context, subargs)
        if subcommand == "status":
            return await self._status(context)
        if subcommand == "stop":
            return await self._stop(context)
        if subcommand == "close":
            return await self._close(context)
        if await self._binding(context) is not None:
            raw = f"{subcommand} {subargs}".strip()
            return await self._send(context, raw)
        return (
            f"**Unknown PawOne subcommand:** `{subcommand}`\n\n"
            f"{PAWONE_HELP}"
        )

    async def _list(self, context: ControlContext, raw: str) -> str:
        args, flags = _parse_flags(raw)
        if "--agents" in flags:
            return self._render_agents(self.service.list_agents())
        agent_id = args[0] if args else context.agent_id
        chats = await self.service.list_chats(
            context,
            agent_id,
            channel=flags.get("--channel"),
            limit=_parse_limit(raw),
        )
        return self._render_chats(agent_id, chats)

    async def _open(self, context: ControlContext, raw: str) -> str:
        args, flags = _parse_flags(raw)
        if len(args) < 2:
            return "Usage: `/pawone use <agent_id> <chat_id|N>`"
        target = await self.service.resolve_target(
            context,
            args[0],
            args[1],
            channel=flags.get("--channel"),
        )
        origin = self.registry.origin_from_context(context)
        await self.registry.set_binding(origin, target)
        return _box("PawOne binding opened", _details(target))

    async def _current(self, context: ControlContext) -> str:
        binding = await self._binding(context)
        if binding is None:
            return _box("PawOne", "No active binding in this conversation.")
        return _box("Current PawOne target", _details(binding.target))

    async def _send(self, context: ControlContext, text: str) -> str:
        if not text.strip():
            return "Usage: `/pawone send <message>`"
        binding = await self._require_binding(context)
        origin = self.registry.origin_from_context(context)
        reply = await self.service.send(context, origin, binding.target, text)
        reply = reply or "(No text content in response)"
        return (
            f"{_box('PawOne reply target', _details(binding.target))}\n\n"
            f"```text\n{reply}\n```"
        )

    async def _submit(self, context: ControlContext, text: str) -> str:
        if not text.strip():
            return "Usage: `/pawone run <message>`"
        binding = await self._require_binding(context)
        origin = self.registry.origin_from_context(context)
        result = await self.service.submit(origin, binding.target, text)
        task_id = str(result.get("task_id") or "")
        if not task_id:
            return f"Task submitted, but no task_id was returned:\n\n{result}"
        await self.registry.set_last_task(origin, task_id)
        return (
            f"{_box('PawOne task submitted', _details(binding.target))}\n\n"
            f"```text\nTask: {task_id}\n```"
        )

    async def _status(self, context: ControlContext) -> str:
        binding = await self._require_binding(context)
        if binding.last_task_id:
            status = await self.service.task_status(
                binding.target,
                binding.last_task_id,
            )
            return self._render_status(binding, status)
        status = await self.service.chat_status(context, binding.target)
        body = f"{_details(binding.target)}\nStatus  : {status}"
        return _box("PawOne target status", body)

    async def _stop(self, context: ControlContext) -> str:
        binding = await self._require_binding(context)
        stopped = await self.service.stop_chat(context, binding.target)
        if stopped:
            return _box("PawOne stop requested", _details(binding.target))
        return _box("PawOne stop", "Target is not running.")

    async def _close(self, context: ControlContext) -> str:
        origin = self.registry.origin_from_context(context)
        cleared = await self.registry.clear_binding(origin)
        if cleared:
            return _box("PawOne", "Binding closed.")
        return _box("PawOne", "No active binding in this conversation.")

    async def _binding(self, context: ControlContext) -> Binding | None:
        origin = self.registry.origin_from_context(context)
        return await self.registry.get_binding(origin)

    async def _require_binding(self, context: ControlContext) -> Binding:
        binding = await self._binding(context)
        if binding is None:
            raise PawOneError(
                "No active binding. Use "
                "`/pawone use <agent_id> <chat_id|N>` first.",
            )
        return binding

    @staticmethod
    def _render_agents(agents: list[dict[str, Any]]) -> str:
        if not agents:
            return _box("PawOne agents", "No agents are configured.")
        lines = [
            "# PawOne Agents",
            "",
            "```text",
            "Use: /pawone ls <agent_id>",
            "```",
            "",
            "| Agent | Enabled | Workspace |",
            "|---|---:|---|",
        ]
        for agent in agents:
            enabled = "yes" if agent.get("enabled", True) else "no"
            lines.append(
                f"| `{agent.get('id', '')}` | {enabled} | "
                f"`{agent.get('workspace_dir', '')}` |",
            )
        return "\n".join(lines)

    @staticmethod
    def _render_chats(agent_id: str, chats: list[Any]) -> str:
        if not chats:
            return _box("PawOne chats", f"No chats found for {agent_id}.")
        lines = [
            f"# PawOne Chats: `{agent_id}`",
            "",
            "```text",
            "Use: /pawone use <agent_id> <N|chat_id>",
            "```",
            "",
            "| N | Name | Chat | Channel | User | Updated | Status |",
            "|---:|---|---|---|---|---|---|",
        ]
        for index, chat in enumerate(chats, start=1):
            lines.append(
                f"| {index} | {chat.name} | `{chat.id[:8]}` | "
                f"`{chat.channel}` | `{chat.user_id}` | "
                f"{format_time(chat.updated_at)} | `{chat.status}` |",
            )
        return "\n".join(lines)

    @staticmethod
    def _render_status(binding: Binding, status: dict[str, Any]) -> str:
        lines = [
            _details(binding.target),
            f"Task    : {binding.last_task_id}",
        ]
        for key in ("status", "error", "created_at", "updated_at"):
            value = status.get(key)
            if value:
                lines.append(f"{key:<8}: {value}")
        return _box("PawOne task status", "\n".join(lines))
