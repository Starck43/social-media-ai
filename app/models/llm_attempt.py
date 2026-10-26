"""Compatibility aliases; canonical declarations live in domain packages."""

from .llm.llm_attempt import LLMAttempt
from .managers.llm_attempt_manager import LLMAttemptManager

__all__ = ["LLMAttempt", "LLMAttemptManager"]
