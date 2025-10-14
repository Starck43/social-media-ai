"""
DEPRECATED: This module has been moved to app.core.llm_presets

Import from app.core.llm_presets instead for LLM provider metadata.
"""

# Re-export for backward compatibility
from app.core.llm_presets import (
    ModelInfo,
    LLMProviderMetadata,
    get_multimodal_models,
    get_cheapest_text_model,
    get_model_display_name,
)

__all__ = [
    "ModelInfo",
    "LLMProviderMetadata",
    "get_multimodal_models",
    "get_cheapest_text_model",
    "get_model_display_name",
]
