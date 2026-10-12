"""LLMAttempt exports and unchanged manager-binding entry point."""

from __future__ import annotations

from .analysis.llm_attempt import LLMAttempt

from .managers.llm_attempt_manager import LLMAttemptManager  # noqa: E402

LLMAttempt.objects = LLMAttemptManager()
