"""Analysis models. Managers bind in app.models.bootstrap."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_scenario import AgentScenario
    from .analyzed_content_receipt import AnalyzedContentReceipt
    from .ai_analytics import AIAnalytics

__all__ = ["AgentScenario", "AnalyzedContentReceipt", "AIAnalytics"]
_MODULES = {
    "AgentScenario": "agent_scenario",
    "AnalyzedContentReceipt": "analyzed_content_receipt",
    "AIAnalytics": "ai_analytics",
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
