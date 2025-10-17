"""
Метаданные провайдеров LLM и преднастроенные модели.
"""
from typing import NamedTuple

from app.types.enums.content_types import MediaType


class ModelInfo(NamedTuple):
    """Информация о конкретной LLM-модели."""
    name: str                 # Отображаемое имя
    model_id: str             # Идентификатор модели в API
    capabilities: list[MediaType]  # Поддерживаемые типы медиа
    max_tokens: int           # Максимальная длина контекста
    cost_per_1k: float        # Примерная стоимость за 1k токенов (USD)
    description: str = ""     # Описание модели


class LLMProviderMetadata:
    """Метаданные и список доступных моделей по провайдерам LLM."""
    
    # DeepSeek models
    DEEPSEEK_MODELS: dict[str, ModelInfo] = {
        "deepseek-chat": ModelInfo(
            name="DeepSeek Chat",
            model_id="deepseek-chat",
            capabilities=[MediaType.TEXT],
            max_tokens=4096,
            cost_per_1k=0.0001,
            description="Быстрая и недорогая модель для текстового анализа"
        ),
        "deepseek-coder": ModelInfo(
            name="DeepSeek Coder",
            model_id="deepseek-coder",
            capabilities=[MediaType.TEXT],
            max_tokens=4096,
            cost_per_1k=0.0001,
            description="Специализируется на анализе и генерации кода"
        ),
    }
    
    # OpenAI models
    OPENAI_MODELS: dict[str, ModelInfo] = {
        "gpt-3.5-turbo": ModelInfo(
            name="GPT-3.5 Turbo",
            model_id="gpt-3.5-turbo",
            capabilities=[MediaType.TEXT],
            max_tokens=4096,
            cost_per_1k=0.0015,
            description="Быстрая и экономичная модель для большинства задач"
        ),
        "gpt-4": ModelInfo(
            name="GPT-4",
            model_id="gpt-4",
            capabilities=[MediaType.TEXT],
            max_tokens=8192,
            cost_per_1k=0.03,
            description="Самая мощная модель для сложных рассуждений"
        ),
        "gpt-4-turbo-preview": ModelInfo(
            name="GPT-4 Turbo",
            model_id="gpt-4-turbo-preview",
            capabilities=[MediaType.TEXT],
            max_tokens=128000,
            cost_per_1k=0.01,
            description="Увеличенное окно контекста, ниже стоимость, чем у GPT-4"
        ),
        "gpt-4-vision-preview": ModelInfo(
            name="GPT-4 Vision",
            model_id="gpt-4-vision-preview",
            capabilities=[MediaType.TEXT, MediaType.IMAGE, MediaType.VIDEO],
            max_tokens=4096,
            cost_per_1k=0.01,
            description="Мультимодальная модель: анализ текста, изображений и видео"
        ),
    }
    
    # Anthropic models
    ANTHROPIC_MODELS: dict[str, ModelInfo] = {
        "claude-3-opus-20240229": ModelInfo(
            name="Claude 3 Opus",
            model_id="claude-3-opus-20240229",
            capabilities=[MediaType.TEXT],
            max_tokens=4096,
            cost_per_1k=0.015,
            description="Самая мощная модель из линейки Claude"
        ),
        "claude-3-sonnet-20240229": ModelInfo(
            name="Claude 3 Sonnet",
            model_id="claude-3-sonnet-20240229",
            capabilities=[MediaType.TEXT],
            max_tokens=4096,
            cost_per_1k=0.003,
            description="Баланс производительности и скорости"
        ),
        "claude-3-haiku-20240307": ModelInfo(
            name="Claude 3 Haiku",
            model_id="claude-3-haiku-20240307",
            capabilities=[MediaType.TEXT],
            max_tokens=4096,
            cost_per_1k=0.00025,
            description="Самая быстрая и компактная модель Claude"
        ),
    }
    
    # Google models
    GOOGLE_MODELS: dict[str, ModelInfo] = {
        "gemini-pro": ModelInfo(
            name="Gemini Pro",
            model_id="gemini-pro",
            capabilities=[MediaType.TEXT],
            max_tokens=32760,
            cost_per_1k=0.00025,
            description="Высокое качество генерации текста"
        ),
        "gemini-pro-vision": ModelInfo(
            name="Gemini Pro Vision",
            model_id="gemini-pro-vision",
            capabilities=[MediaType.TEXT, MediaType.IMAGE],
            max_tokens=16384,
            cost_per_1k=0.00025,
            description="Мультимодальная модель: понимание текста и изображений"
        ),
    }
    
    # Mistral models
    MISTRAL_MODELS: dict[str, ModelInfo] = {
        "mistral-tiny": ModelInfo(
            name="Mistral Tiny",
            model_id="mistral-tiny",
            capabilities=[MediaType.TEXT],
            max_tokens=32000,
            cost_per_1k=0.00014,
            description="Быстрая и эффективная для простых задач"
        ),
        "mistral-small": ModelInfo(
            name="Mistral Small",
            model_id="mistral-small",
            capabilities=[MediaType.TEXT],
            max_tokens=32000,
            cost_per_1k=0.0006,
            description="Хороший баланс стоимости и производительности"
        ),
        "mistral-medium": ModelInfo(
            name="Mistral Medium",
            model_id="mistral-medium",
            capabilities=[MediaType.TEXT],
            max_tokens=32000,
            cost_per_1k=0.0027,
            description="Самая мощная модель Mistral"
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
        """Получить конфигурацию провайдера по типу."""
        return cls.PROVIDER_CONFIGS.get(provider_type, cls.PROVIDER_CONFIGS["custom"])
    
    @classmethod
    def get_available_models(cls, provider_type: str) -> dict[str, ModelInfo]:
        """Список доступных моделей для указанного провайдера."""
        return cls.get_provider_config(provider_type).get("models", {})
    
    @classmethod
    def get_model_info(cls, provider_type: str, model_id: str) -> ModelInfo | None:
        """Информация о конкретной модели."""
        models = cls.get_available_models(provider_type)
        return models.get(model_id)
    
    @classmethod
    def get_models_by_capability(cls, provider_type: str, capability: MediaType | str) -> list[str]:
        """Получить список ID моделей, поддерживающих указанный тип медиа.

        Поддерживается передача как `MediaType`, так и строки ("text", "image", ...).
        """
        models = cls.get_available_models(provider_type)
        # Нормализация capability
        if isinstance(capability, str):
            try:
                cap = MediaType(capability)
            except ValueError:
                return []
        else:
            cap = capability
        return [
            model_id
            for model_id, info in models.items()
            if cap in info.capabilities
        ]


# Утилиты
def get_multimodal_models() -> dict[str, list[str]]:
    """Получить по провайдерам модели, поддерживающие изображение/видео."""
    result = {}
    for provider_type, config in LLMProviderMetadata.PROVIDER_CONFIGS.items():
        multimodal = []
        for model_id, info in config["models"].items():
            if MediaType.IMAGE in info.capabilities or MediaType.VIDEO in info.capabilities:
                multimodal.append(model_id)
        if multimodal:
            result[provider_type] = multimodal
    return result


def get_cheapest_text_model(provider_type: str) -> str | None:
    """Найти самую дешёвую текстовую модель для заданного провайдера."""
    models = LLMProviderMetadata.get_available_models(provider_type)
    text_models = {model_id: info for model_id, info in models.items() if MediaType.TEXT in info.capabilities}
    if not text_models:
        return None
    return min(text_models.items(), key=lambda x: x[1].cost_per_1k)[0]


def get_model_display_name(provider_type: str, model_id: str) -> str:
    """Получить отображаемое имя модели."""
    info = LLMProviderMetadata.get_model_info(provider_type, model_id)
    return info.name if info else model_id
