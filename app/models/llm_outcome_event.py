"""Outcome export and existing manager-binding convention."""

from .analysis.llm_outcome_event import LLMOutcomeEvent
from .managers.llm_outcome_event_manager import LLMOutcomeEventManager

LLMOutcomeEvent.objects = LLMOutcomeEventManager()
