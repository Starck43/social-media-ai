"""Compatibility aliases; canonical declarations live in domain packages."""

from .llm.llm_outcome_event import LLMOutcomeEvent
from .managers.llm_outcome_event_manager import LLMOutcomeEventManager

__all__ = ["LLMOutcomeEvent", "LLMOutcomeEventManager"]
