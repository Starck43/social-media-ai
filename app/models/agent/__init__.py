"""Agent persistence models with lazy exports and stable legacy bindings.

This is app.models.agent, not the separate app.agent runtime package.
No queue, memory-concurrency, uniqueness, cost-unit or role-enum change here.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_feedback import AgentFeedback
    from .agent_memory import AgentMemory
    from .agent_message import AgentMessage
    from .agent_session import AgentSession

__all__ = ["AgentSession", "AgentMessage", "AgentMemory", "AgentFeedback"]
_MODULES = {
    "AgentSession": "agent_session",
    "AgentMessage": "agent_message",
    "AgentMemory": "agent_memory",
    "AgentFeedback": "agent_feedback",
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
