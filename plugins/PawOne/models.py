# -*- coding: utf-8 -*-
"""Runtime models for PawOne."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OriginKey:
    """Conversation that issued the PawOne command."""

    agent_id: str
    channel: str
    user_id: str
    session_id: str


@dataclass(frozen=True)
class TargetRef:
    """Target QwenPaw conversation controlled by PawOne."""

    agent_id: str
    chat_id: str
    channel: str
    user_id: str
    session_id: str
    name: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Binding:
    """Per-origin PawOne binding state."""

    target: TargetRef
    last_task_id: str = ""
