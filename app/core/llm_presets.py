"""
LLM Provider metadata and model definitions.
Pre-configured models for popular providers.
"""
from typing import Dict, List, NamedTuple


class ModelInfo(NamedTuple):
    """Information about a specific LLM model."""
    name: str                # Display name
    model_id: str            # API model identifier
    capabilities: List[str]  # ["text", "image", "video"]
    max_tokens: int          # Maximum context length
    cost_per_1k: float       # Approximate cost per 1k tokens (USD)
    description: str = ""    # Optional description


class LLMProviderMetadata:
    """Metadata and available models for LLM providers."""
    
    # DeepSeek models
    DEEPSEEK_MODELS: Dict[str, ModelInfo] = {
        "deepseek-chat": ModelInfo(
            name="DeepSeek Chat",
            model_id="deepseek-chat",
            capabilities=["text"],
            max_tokens=4096,
            cost_per_1k=0.0001,
            description="Fast and affordable model for text analysis"
        ),
        "deepseek-coder": ModelInfo(
            name="DeepSeek Coder",
            model_id="deepseek-coder",
            capabilities=["text"],
            max_tokens=4096,
            cost_per_1k=0.0001,
            description="Specialized for code analysis and generation"
        ),
    }
    
    # OpenAI models
    OPENAI_MODELS: Dict[str, ModelInfo] = {
        "gpt-3.5-turbo": ModelInfo(
            name="GPT-3.5 Turbo",
            model_id="gpt-3.5-turbo",
            capabilities=["text"],
            max_tokens=4096,
            cost_per_1k=0.0015,
            description="Fast and cost-effective for most tasks"
        ),
        "gpt-4": ModelInfo(
            name="GPT-4",
            model_id="gpt-4",
            capabilities=["text"],
            max_tokens=8192,
            cost_per_1k=0.03,
            description="Most capable model for complex reasoning"
        ),
        "gpt-4-turbo-preview": ModelInfo(
            name="GPT-4 Turbo",
            model_id="gpt-4-turbo-preview",
            capabilities=["text"],
            max_tokens=128000,
            cost_per_1k=0.01,
            description="Extended context window, lower cost than GPT-4"
        ),
        "gpt-4-vision-preview": ModelInfo(
            name="GPT-4 Vision",
            model_id="gpt-4-vision-preview",
            capabilities=["text", "image", "video"],
            max_tokens=4096,
            cost_per_1k=0.01,
            description="Multimodal: analyze text, images, and video"
        ),
    }
    
    # Anthropic models
    ANTHROPIC_MODELS: Dict[str, ModelInfo] = {
        "claude-3-opus-20240229": ModelInfo(
            name="Claude 3 Opus",
            model_id="claude-3-opus-20240229",
            capabilities=["text"],
            max_tokens=4096,
            cost_per_1k=0.015,
            description="Most capable Claude model"
        ),
        "claude-3-sonnet-20240229": ModelInfo(
            name="Claude 3 Sonnet",
            model_id="claude-3-sonnet-20240229",
            capabilities=["text"],
            max_tokens=4096,
            cost_per_1k=0.003,
            description="Balanced performance and speed"
        ),
        "claude-3-haiku-20240307": ModelInfo(
            name="Claude 3 Haiku",
            model_id="claude-3-haiku-20240307",
            capabilities=["text"],
            max_tokens=4096,
            cost_per_1k=0.00025,
            description="Fastest and most compact Claude model"
        ),
    }
    
    # Google models
    GOOGLE_MODELS: Dict[str, ModelInfo] = {
        "gemini-pro": ModelInfo(
            name="Gemini Pro",
            model_id="gemini-pro",
            capabilities=["text"],
            max_tokens=32760,
            cost_per_1k=0.00025,
            description="High-quality text generation"
        ),
        "gemini-pro-vision": ModelInfo(
            name="Gemini Pro Vision",
            model_id="gemini-pro-vision",
            capabilities=["text", "image"],
            max_tokens=16384,
            cost_per_1k=0.00025,
            description="Multimodal: text and image understanding"
        ),
    }
    
    # Mistral models
    MISTRAL_MODELS: Dict[str, ModelInfo] = {
        "mistral-tiny": ModelInfo(
            name="Mistral Tiny",
            model_id="mistral-tiny",
            capabilities=["text"],
            max_tokens=32000,
            cost_per_1k=0.00014,
            description="Fast and efficient for simple tasks"
        ),
        "mistral-small": ModelInfo(
            name="Mistral Small",
            model_id="mistral-small",
            capabilities=["text"],
            max_tokens=32000,
            cost_per_1k=0.0006,
            description="Good balance of performance and cost"
        ),
        "mistral-medium": ModelInfo(
            name="Mistral Medium",
            model_id="mistral-medium",
            capabilities=["text"],
            max_tokens=32000,
            cost_per_1k=0.0027,
            description="Most capable Mistral model"
        ),
    }
    
    # Provider configurations
    PROVIDER_CONFIGS = {
        "deepseek": {
            "display_name": "DeepSeek",
            "api_url": "https://api.deepseek.com/v1/chat/completions",
            "api_key_env": "DEEPSEEK_API_KEY",
            "models": DEEPSEEK_MODELS,
        },
        "openai": {
            "display_name": "OpenAI",
            "api_url": "https://api.openai.com/v1/chat/completions",
            "api_key_env": "OPENAI_API_KEY",
            "models": OPENAI_MODELS,
        },
        "anthropic": {
            "display_name": "Anthropic",
            "api_url": "https://api.anthropic.com/v1/messages",
            "api_key_env": "ANTHROPIC_API_KEY",
            "models": ANTHROPIC_MODELS,
        },
        "google": {
            "display_name": "Google",
            "api_url": "https://generativelanguage.googleapis.com/v1beta",
            "api_key_env": "GOOGLE_API_KEY",
            "models": GOOGLE_MODELS,
        },
        "mistral": {
            "display_name": "Mistral AI",
            "api_url": "https://api.mistral.ai/v1/chat/completions",
            "api_key_env": "MISTRAL_API_KEY",
            "models": MISTRAL_MODELS,
        },
        "custom": {
            "display_name": "Custom Provider",
            "api_url": "",
            "api_key_env": "CUSTOM_API_KEY",
            "models": {},
        },
    }
    
    @classmethod
    def get_provider_config(cls, provider_type: str) -> dict:
        """Get configuration for a provider type."""
        return cls.PROVIDER_CONFIGS.get(provider_type, cls.PROVIDER_CONFIGS["custom"])
    
    @classmethod
    def get_available_models(cls, provider_type: str) -> Dict[str, ModelInfo]:
        """Get available models for a provider type."""
        return cls.get_provider_config(provider_type).get("models", {})
    
    @classmethod
    def get_model_info(cls, provider_type: str, model_id: str) -> ModelInfo | None:
        """Get information about a specific model."""
        models = cls.get_available_models(provider_type)
        return models.get(model_id)
    
    @classmethod
    def get_models_by_capability(cls, provider_type: str, capability: str) -> List[str]:
        """Get model IDs that support a specific capability."""
        models = cls.get_available_models(provider_type)
        return [
            model_id
            for model_id, info in models.items()
            if capability in info.capabilities
        ]


# Helper functions for quick access
def get_multimodal_models() -> Dict[str, List[str]]:
    """Get models that support image/video analysis by provider."""
    result = {}
    for provider_type, config in LLMProviderMetadata.PROVIDER_CONFIGS.items():
        multimodal = []
        for model_id, info in config["models"].items():
            if "image" in info.capabilities or "video" in info.capabilities:
                multimodal.append(model_id)
        if multimodal:
            result[provider_type] = multimodal
    return result


def get_cheapest_text_model(provider_type: str) -> str | None:
    """Get the cheapest text-only model for a provider."""
    models = LLMProviderMetadata.get_available_models(provider_type)
    text_models = {
        model_id: info
        for model_id, info in models.items()
        if "text" in info.capabilities
    }
    if not text_models:
        return None
    return min(text_models.items(), key=lambda x: x[1].cost_per_1k)[0]


def get_model_display_name(provider_type: str, model_id: str) -> str:
    """Get display name for a model."""
    info = LLMProviderMetadata.get_model_info(provider_type, model_id)
    return info.name if info else model_id
