"""Scheduling models with lazy exports. Managers bind in app.models.bootstrap."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_task import AgentTask, agent_task_sources
    from .job import Job

__all__ = ["AgentTask", "Job", "agent_task_sources"]
_MODULES = {
    "AgentTask": "agent_task",
    "Job": "job",
    "agent_task_sources": "agent_task",
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
