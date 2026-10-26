"""Compatibility aliases; canonical declarations live in domain packages."""

from .base import Base, TimestampMixin
from .llm.llm_model import LLMModel, _model_type_to_capabilities
from .managers.llm_model_manager import LLMModelManager

__all__ = ["Base", "LLMModel", "LLMModelManager", "TimestampMixin", "_model_type_to_capabilities"]
