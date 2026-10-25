"""Analysis exports, including legacy LLM aliases. Managers bind in app.models.bootstrap."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_scenario import AgentScenario
    from .analyzed_content_receipt import AnalyzedContentReceipt
    from .ai_analytics import AIAnalytics
    from .llm_attempt import LLMAttempt
    from .llm_budget import LLMBudgetDay, LLMBudgetReservation
    from .llm_dispatch_receipt import LLMDispatchReceipt
    from .llm_model import LLMModel
    from .llm_outcome_event import LLMOutcomeEvent
    from .llm_provider import LLMProvider

__all__ = [
    "AgentScenario",
    "AnalyzedContentReceipt",
    "AIAnalytics",
    "LLMAttempt",
    "LLMBudgetDay",
    "LLMBudgetReservation",
    "LLMDispatchReceipt",
    "LLMModel",
    "LLMOutcomeEvent",
    "LLMProvider",
]
_MODULES = {
    "AgentScenario": "agent_scenario",
    "AnalyzedContentReceipt": "analyzed_content_receipt",
    "AIAnalytics": "ai_analytics",
    "LLMAttempt": "llm_attempt",
    "LLMBudgetDay": "llm_budget",
    "LLMBudgetReservation": "llm_budget",
    "LLMDispatchReceipt": "llm_dispatch_receipt",
    "LLMModel": "llm_model",
    "LLMOutcomeEvent": "llm_outcome_event",
    "LLMProvider": "llm_provider",
}


def __getattr__(name: str):
    if name not in _MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
    globals()[name] = value
    return value
