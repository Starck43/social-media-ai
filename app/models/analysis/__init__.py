"""Analysis-domain exports without changing legacy registration/binding order.

Canonical leaves declare models. Legacy modules retain manager bindings;
lazy exports avoid importing this entire domain at the first root-model import.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_scenario import AgentScenario
    from .ai_analytics import AIAnalytics
    from .llm_model import LLMModel
    from .llm_provider import LLMProvider

__all__ = ["AgentScenario", "AIAnalytics", "LLMModel", "LLMProvider"]
_MODULES = {
    "AgentScenario": "agent_scenario",
    "AIAnalytics": "ai_analytics",
    "LLMModel": "llm_model",
    "LLMProvider": "llm_provider",
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
