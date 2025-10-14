#!/usr/bin/env python3
"""
Примеры использования метаданных LLM провайдеров.
Демонстрирует работу с предзагруженными моделями.
"""

from app.types import LLMProviderType
from app.types.llm_models import (
    LLMProviderMetadata,
    get_multimodal_models,
    get_cheapest_text_model,
    get_model_display_name
)


def example_1_basic_metadata():
    """Пример 1: Базовая информация о провайдере"""
    print("="*80)
    print("ПРИМЕР 1: Базовая информация о провайдерах")
    print("="*80)
    
    for provider in LLMProviderType:
        print(f"\n{provider.display_name} ({provider.value})")
        print(f"  API URL: {provider.default_api_url}")
        print(f"  API Key: {provider.default_api_key_env}")
        print(f"  Доступно моделей: {len(provider.available_models)}")


def example_2_list_models():
    """Пример 2: Список всех моделей провайдера"""
    print("\n" + "="*80)
    print("ПРИМЕР 2: Список моделей OpenAI")
    print("="*80)
    
    openai = LLMProviderType.OPENAI
    
    for model_id, info in openai.available_models.items():
        print(f"\n{info.name}")
        print(f"  ID: {model_id}")
        print(f"  Возможности: {', '.join(info.capabilities)}")
        print(f"  Max tokens: {info.max_tokens:,}")
        print(f"  Стоимость: ${info.cost_per_1k}/1k tokens")
        print(f"  Описание: {info.description}")


def example_3_find_by_capability():
    """Пример 3: Поиск моделей по возможностям"""
    print("\n" + "="*80)
    print("ПРИМЕР 3: Модели с поддержкой изображений")
    print("="*80)
    
    for provider in [LLMProviderType.OPENAI, LLMProviderType.GOOGLE]:
        image_models = provider.get_models_by_capability("image")
        if image_models:
            print(f"\n{provider.display_name}:")
            for model_id in image_models:
                info = provider.get_model_info(model_id)
                print(f"  • {info.name} ({model_id})")
                print(f"    Стоимость: ${info.cost_per_1k}/1k tokens")


def example_4_cheapest_models():
    """Пример 4: Самые дешёвые модели для текста"""
    print("\n" + "="*80)
    print("ПРИМЕР 4: Самые дешёвые модели для текстового анализа")
    print("="*80)
    
    providers = [
        LLMProviderType.DEEPSEEK,
        LLMProviderType.OPENAI,
        LLMProviderType.ANTHROPIC,
        LLMProviderType.GOOGLE,
        LLMProviderType.MISTRAL
    ]
    
    for provider in providers:
        cheapest = get_cheapest_text_model(provider.value)
        if cheapest:
            info = provider.get_model_info(cheapest)
            print(f"\n{provider.display_name}: {info.name}")
            print(f"  ID: {cheapest}")
            print(f"  Стоимость: ${info.cost_per_1k}/1k tokens")


def example_5_multimodal():
    """Пример 5: Все мультимодальные модели"""
    print("\n" + "="*80)
    print("ПРИМЕР 5: Мультимодальные модели (текст + фото/видео)")
    print("="*80)
    
    multimodal = get_multimodal_models()
    
    for provider_type, model_ids in multimodal.items():
        provider = LLMProviderType(provider_type)
        print(f"\n{provider.display_name}:")
        for model_id in model_ids:
            info = provider.get_model_info(model_id)
            caps = " + ".join(info.capabilities)
            print(f"  • {info.name} ({caps})")
            print(f"    ${info.cost_per_1k}/1k tokens")


def example_6_create_provider_config():
    """Пример 6: Создание конфигурации провайдера из метаданных"""
    print("\n" + "="*80)
    print("ПРИМЕР 6: Автоматическая конфигурация провайдера")
    print("="*80)
    
    # Выбираем провайдер и модель
    provider = LLMProviderType.OPENAI
    model_id = "gpt-4-vision-preview"
    model_info = provider.get_model_info(model_id)
    
    # Создаём конфигурацию (так можно создавать в админке автоматически)
    config = {
        "name": f"{provider.display_name} {model_info.name}",
        "provider_type": provider.value,
        "api_url": provider.default_api_url,
        "api_key_env": provider.default_api_key_env,
        "model_name": model_id,
        "capabilities": model_info.capabilities,
        "config": {
            "max_tokens": model_info.max_tokens,
            "temperature": 0.2,
        },
        "description": model_info.description,
    }
    
    print("\nСгенерированная конфигурация LLMProvider:")
    print(f"  Name: {config['name']}")
    print(f"  Provider: {config['provider_type']}")
    print(f"  Model: {config['model_name']}")
    print(f"  API URL: {config['api_url']}")
    print(f"  Capabilities: {config['capabilities']}")
    print(f"  Max tokens: {config['config']['max_tokens']:,}")


def example_7_cost_comparison():
    """Пример 7: Сравнение стоимости моделей"""
    print("\n" + "="*80)
    print("ПРИМЕР 7: Сравнение стоимости анализа 100k токенов")
    print("="*80)
    
    # Предположим анализируем 100,000 токенов
    tokens = 100_000
    
    models_to_compare = [
        ("deepseek", "deepseek-chat"),
        ("openai", "gpt-3.5-turbo"),
        ("openai", "gpt-4-turbo-preview"),
        ("openai", "gpt-4-vision-preview"),
        ("anthropic", "claude-3-haiku-20240307"),
        ("anthropic", "claude-3-opus-20240229"),
        ("google", "gemini-pro"),
        ("mistral", "mistral-tiny"),
    ]
    
    print(f"\nСтоимость анализа {tokens:,} токенов:")
    print("-" * 60)
    
    for provider_type, model_id in models_to_compare:
        provider = LLMProviderType(provider_type)
        info = provider.get_model_info(model_id)
        if info:
            cost = (tokens / 1000) * info.cost_per_1k
            print(f"{info.name:30} ${cost:8.2f}")


def example_8_smart_model_selection():
    """Пример 8: Умный выбор модели по задаче"""
    print("\n" + "="*80)
    print("ПРИМЕР 8: Автоматический выбор модели по задаче")
    print("="*80)
    
    # Функция для выбора оптимальной модели
    def select_optimal_model(task_type: str, budget: str = "low"):
        """
        Выбирает оптимальную модель для задачи.
        
        task_type: "text", "image", "video"
        budget: "low", "medium", "high"
        """
        if task_type == "text":
            if budget == "low":
                # Самая дешёвая
                return ("deepseek", "deepseek-chat")
            elif budget == "medium":
                return ("openai", "gpt-3.5-turbo")
            else:  # high
                return ("openai", "gpt-4-turbo-preview")
        
        elif task_type in ["image", "video"]:
            if budget == "low":
                return ("google", "gemini-pro-vision")
            else:
                return ("openai", "gpt-4-vision-preview")
        
        return None
    
    scenarios = [
        ("Анализ настроений (бюджетный)", "text", "low"),
        ("Комплексный анализ текста", "text", "high"),
        ("Анализ фото в соцсетях", "image", "low"),
        ("Детальный анализ видео", "video", "high"),
    ]
    
    for scenario_name, task, budget in scenarios:
        result = select_optimal_model(task, budget)
        if result:
            provider_type, model_id = result
            provider = LLMProviderType(provider_type)
            info = provider.get_model_info(model_id)
            
            print(f"\n{scenario_name}:")
            print(f"  → {provider.display_name} {info.name}")
            print(f"     {model_id}")
            print(f"     ${info.cost_per_1k}/1k tokens")


if __name__ == "__main__":
    example_1_basic_metadata()
    example_2_list_models()
    example_3_find_by_capability()
    example_4_cheapest_models()
    example_5_multimodal()
    example_6_create_provider_config()
    example_7_cost_comparison()
    example_8_smart_model_selection()
    
    print("\n" + "="*80)
    print("✅ Все примеры выполнены!")
    print("="*80)
