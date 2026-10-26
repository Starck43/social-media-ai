"""Compatibility aliases; canonical declarations live in domain packages."""

from .base import Base, TimestampMixin
from .llm.llm_provider import LLMProvider
from .managers.llm_provider_manager import LLMProviderManager

__all__ = ["Base", "LLMProvider", "LLMProviderManager", "TimestampMixin"]
