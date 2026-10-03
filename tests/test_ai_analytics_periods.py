"""Tests for analytics period semantics: period_type rollups + activity metrics.

Covers the pieces that give `ai_analytics.period_type` real meaning and the
per-user activity metrics the reporting reads:
- `AIAnalyticsManager.build_period_rollups` folds DAILY rows into WEEKLY/MONTHLY.
- `AIAnalyzer._calculate_content_stats` writes active_users/messages_count/
  engagement_rate into content_statistics.
- `analyze_content(force_reanalyze=True)` propagates the dedup-bypass to
  `base_analyze_content` (the full-cycle `--force-refresh` path).
"""

import uuid
from datetime import date

import pytest

from app.models import AIAnalytics, Platform, Source
from app.services.ai.analyzer import AIAnalyzer
from app.types import PeriodType, PlatformType, SourceType


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type=PlatformType.VK.db_value,
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def source(platform):
    s = await Source.objects.create(
        platform_id=platform.id,
        name="Period Test Source",
        source_type=SourceType.CHANNEL,
        external_id="period-test",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


async def test_build_period_rollups_folds_daily_rows(source):
    """Two daily rows for a source collapse into one WEEKLY rollup row."""
    from app.models.managers.ai_analytics_manager import AIAnalyticsManager

    await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date(2026, 1, 1),
        period_type=PeriodType.DAILY,
        summary_data={
            "content_statistics": {
                "total_posts": 5,
                "messages_count": 5,
                "active_users": 3,
                "total_reactions": 10,
                "total_comments": 2,
                "total_views": 100,
            }
        },
        request_tokens=100,
        response_tokens=50,
        estimated_cost=10,
    )
    await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date(2026, 1, 2),
        period_type=PeriodType.DAILY,
        summary_data={
            "content_statistics": {
                "total_posts": 3,
                "messages_count": 3,
                "active_users": 2,
                "total_reactions": 6,
                "total_comments": 1,
                "total_views": 60,
            }
        },
        request_tokens=80,
        response_tokens=40,
        estimated_cost=8,
    )

    written = await AIAnalyticsManager().build_period_rollups(PeriodType.WEEKLY, date(2026, 1, 1), date(2026, 1, 7))

    assert written == 1
    rollup = await AIAnalytics.objects.filter(
        source_id=source.id, analysis_date=date(2026, 1, 1), period_type=PeriodType.WEEKLY
    ).first()
    assert rollup is not None
    stats = (rollup.summary_data or {}).get("content_statistics", {})
    assert stats["total_posts"] == 8
    assert stats["messages_count"] == 8
    assert stats["active_users"] == 5
    assert stats["total_reactions"] == 16
    assert rollup.request_tokens == 180
    assert rollup.estimated_cost == 18

    # Idempotent: running again updates, does not duplicate.
    await AIAnalyticsManager().build_period_rollups(PeriodType.WEEKLY, date(2026, 1, 1), date(2026, 1, 7))
    count = await AIAnalytics.objects.filter(
        source_id=source.id, analysis_date=date(2026, 1, 1), period_type=PeriodType.WEEKLY
    )
    assert len(count) == 1

    await AIAnalytics.objects.filter(source_id=source.id).delete()


def test_calculate_content_stats_writes_activity_metrics():
    """content_statistics carries active_users/messages_count/engagement_rate."""
    content = [
        {"text": "a", "reactions": 5, "comments": 2, "views": 100, "from_id": 1},
        {"text": "b", "reactions": 3, "comments": 1, "views": 50, "from_id": 1},
        {"text": "c", "reactions": 0, "comments": 0, "views": 10, "from_id": 2},
    ]
    stats = AIAnalyzer()._calculate_content_stats(content, analysis_date=date(2026, 1, 1))

    assert stats["active_users"] == 2
    assert stats["messages_count"] == 3
    assert stats["total_posts"] == 3
    assert stats["total_reactions"] == 8
    assert stats["total_comments"] == 3
    assert stats["total_views"] == 160
    assert round(stats["engagement_rate"], 2) == round((8 + 3 + 160) / 3, 2)


async def test_analyze_content_forwards_force_reanalyze(monkeypatch, source):
    """force_reanalyze on analyze_content reaches base_analyze_content (dedup bypass)."""
    seen = {}

    async def fake_base(
        self, content, source, topic_chain_id=None, parent_analysis_id=None, analysis_date=None, force_reanalyze=False
    ):
        seen["force_reanalyze"] = force_reanalyze
        seen["len"] = len(content)
        return None

    monkeypatch.setattr(AIAnalyzer, "base_analyze_content", fake_base)

    await AIAnalyzer().analyze_content([{"text": "x"}], source, analyze_by="themes", force_reanalyze=True)
    assert seen.get("force_reanalyze") is True
