import pytest
from datetime import date
import uuid

from app.services.ai.analyzer import AIAnalyzer
from app.models import Platform, Source, AIAnalytics, LLMProvider
from app.types import PlatformType, SourceType, LLMProviderType


@pytest.mark.asyncio
async def test_analyzer_with_llm_provider(monkeypatch):
    """Test AIAnalyzer with new LLMProvider integration"""
    # Arrange: create platform, source, and LLM provider
    platform = await Platform.objects.create(
        name=f"vk_test_{uuid.uuid4().hex[:8]}",
        platform_type=PlatformType.VK.value,
        base_url="https://vk.com",
        params={}
    )
    source = await Source.objects.create(
        platform_id=platform.id,
        name="Test Group",
        source_type=SourceType.GROUP.name,
        external_id="club1",
        params={},
        is_active=True
    )

    # Create LLM provider
    llm_provider = await LLMProvider.objects.create(
        name="Test OpenAI Provider",
        provider_type=LLMProviderType.OPENAI,
        api_url="https://api.openai.com/v1/chat/completions",
        api_key_env="OPENAI_API_KEY",
        model_name="gpt-4",
        capabilities=["text"],
        is_active=True
    )

    analyzer = AIAnalyzer()

    # Mock API call to avoid network
    async def fake_call_api(text, stats, src, platform_name, bot_scenario=None):
        return {
            "request": {"model": "gpt-4", "prompt": "PROMPT"},
            "response": {
                "choices": [{"message": {"content": '{"sentiment_analysis": {"overall_sentiment": "positive"}}'}}]
            }
        }

    monkeypatch.setattr(analyzer, "_call_api", fake_call_api)

    # Act
    content = [{"text": "hello world", "date": str(date.today()), "reactions": 1, "comments": 0}]
    analytics = await analyzer.analyze_content(content, source)

    # Assert
    assert isinstance(analytics, AIAnalytics)
    assert analytics.source_id == source.id
    assert analytics.llm_model == "gpt-4"
    assert analytics.prompt_text == "PROMPT"
    assert analytics.response_payload is not None
    assert analytics.summary_data.get("content_statistics", {}).get("total_posts") == 1


@pytest.mark.asyncio
async def test_llm_provider_capabilities():
    """Test LLMProvider capabilities filtering"""
    # Create providers with different capabilities
    provider1 = await LLMProvider.objects.create(
        name="Text Only Provider",
        provider_type=LLMProviderType.OPENAI,
        api_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        model_name="gpt-3.5-turbo",
        capabilities=["text"],
        is_active=True
    )

    provider2 = await LLMProvider.objects.create(
        name="Multimodal Provider",
        provider_type=LLMProviderType.GOOGLE,
        api_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="GOOGLE_API_KEY",
        model_name="gemini-pro",
        capabilities=["text", "image"],
        is_active=True
    )

    # Test filtering by capability
    text_providers = await LLMProvider.objects.get_by_capability("text")
    image_providers = await LLMProvider.objects.get_by_capability("image")

    assert len(text_providers) == 2  # Both providers support text
    assert len(image_providers) == 1  # Only multimodal supports image

    # Cleanup
    await LLMProvider.objects.delete(provider1.id)
    await LLMProvider.objects.delete(provider2.id)
