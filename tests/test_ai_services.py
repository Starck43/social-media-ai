import pytest
from datetime import date
import uuid

from app.services.ai.analyzer import AIAnalyzer
from app.models import Platform, Source, AIAnalytics
from app.types.models import PlatformType, SourceType


@pytest.mark.asyncio
async def test_analyzer_creates_ai_analytics(monkeypatch):
    # Arrange: create platform and source
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

    analyzer = AIAnalyzer()

    # Mock API call to avoid network
    async def fake_call_api(text, stats, src, platform_name, bot_scenario=None):
        return {
            "request": {"model": "deepseek-chat", "prompt": "PROMPT"},
            "response": {
                "choices": [{"message": {"content": '{"sentiment_analysis": {"overall_sentiment": "neutral"}}'}}]
            }
        }

    monkeypatch.setattr(analyzer, "_call_api", fake_call_api)

    # Act
    content = [{"text": "hello world", "date": str(date.today()), "reactions": 1, "comments": 0}]
    analytics = await analyzer.analyze_content(content, source)

    # Assert
    assert isinstance(analytics, AIAnalytics)
    assert analytics.source_id == source.id
    assert analytics.llm_model == "deepseek-chat"
    assert analytics.prompt_text == "PROMPT"
    assert analytics.response_payload is not None
    assert analytics.summary_data.get("content_statistics", {}).get("total_posts") == 1
