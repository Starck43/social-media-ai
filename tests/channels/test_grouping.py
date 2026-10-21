"""Unit tests for ``group_analytics()``."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.models import AIAnalytics, Platform, Source
from app.services.ai.grouping import group_analytics
from app.types import PeriodType, PlatformType, SourceType

# ── fixtures ─────────────────────────────────────────────────────────────────


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
        name="Grouping Source",
        source_type=SourceType.CHANNEL,
        external_id=f"grouping-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


@pytest.fixture(autouse=True)
async def _db_cleanup():
    await AIAnalytics.objects.delete()
    yield
    await AIAnalytics.objects.delete()


# ── helpers ──────────────────────────────────────────────────────────────────


def _summary(text: dict | None = None, *, topics: list[str] | None = None, entities: list[dict] | None = None) -> dict:
    payload = dict(text or {})
    if topics:
        payload["main_topics"] = topics
    if entities:
        payload["entities"] = entities
    return {"multi_llm_analysis": {"text_analysis": payload}}


async def _row(
    source,
    *,
    day_offset: int = 0,
    summary_data: dict | None = None,
    media_types: list[str] | None = None,
    topic_chain_id: str | None = None,
    period_type: PeriodType = PeriodType.DAY,
):
    return await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date.today() - timedelta(days=day_offset),
        period_type=period_type,
        summary_data=summary_data or {},
        media_types=media_types,
        topic_chain_id=topic_chain_id,
    )


# ── empty input ───────────────────────────────────────────────────────────────


async def test_group_analytics_empty_rows_returns_empty_groups():
    result = await group_analytics([], "themes")
    assert result["groups"] == []
    assert result["axis"] == "themes"
    assert result["time_breakdown"] is False


# ── themes ────────────────────────────────────────────────────────────────────


async def test_group_by_themes_counts_topics(source):
    r1 = await _row(source, day_offset=3, summary_data=_summary(topics=["отпуск", "цены"]))
    r2 = await _row(source, day_offset=2, summary_data=_summary(topics=["отпуск"]))
    r3 = await _row(source, day_offset=1, summary_data=_summary(topics=["цены"]))
    rows = [r1, r2, r3]
    result = await group_analytics(rows, "themes")
    assert len(result["groups"]) == 2
    keys = {g["key"] for g in result["groups"]}
    assert "отпуск" in keys
    assert "цены" in keys
    assert result["groups"][0]["key"] == "отпуск"
    assert result["groups"][0]["count"] == 2
    assert result["groups"][1]["key"] == "цены"
    assert result["groups"][1]["count"] == 2


async def test_group_by_themes_falls_back_to_no_topic(source):
    r1 = await _row(source, day_offset=1, summary_data=_summary())
    rows = [r1]
    result = await group_analytics(rows, "themes")
    assert len(result["groups"]) == 1
    assert result["groups"][0]["key"] == "(без темы)"


# ── sources ───────────────────────────────────────────────────────────────────


async def test_group_by_sources_uses_source_id(source):
    r1 = await _row(source, day_offset=3)
    second = await Source.objects.create(
        platform_id=source.platform_id,
        name="Second Source",
        source_type=SourceType.CHANNEL,
        external_id=f"second-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    try:
        r2 = await _row(second, day_offset=2)
        r3 = await _row(source, day_offset=1)
        rows = [r1, r2, r3]
        result = await group_analytics(rows, "sources")
        assert len(result["groups"]) == 2
        assert result["groups"][0]["count"] == 2
        assert result["groups"][1]["count"] == 1
    finally:
        await Source.objects.delete_by_id(second.id)


# ── entities ──────────────────────────────────────────────────────────────────


async def test_group_by_entities_counts_names(source):
    r1 = await _row(
        source,
        day_offset=3,
        summary_data=_summary(
            entities=[
                {"name": "Coca-Cola", "type": "brand"},
                {"name": "Иван", "type": "person"},
            ]
        ),
    )
    r2 = await _row(
        source,
        day_offset=2,
        summary_data=_summary(
            entities=[
                {"name": "Coca-Cola", "type": "brand"},
            ]
        ),
    )
    rows = [r1, r2]
    result = await group_analytics(rows, "entities")
    keys = {g["key"] for g in result["groups"]}
    assert "Coca-Cola" in keys
    assert "Иван" in keys
    assert result["groups"][0]["count"] == 2
    coca = next(g for g in result["groups"] if g["key"] == "Coca-Cola")
    assert coca["count"] == 2
    ivan = next(g for g in result["groups"] if g["key"] == "Иван")
    assert ivan["count"] == 1


async def test_group_by_entities_filters_by_type(source):
    r1 = await _row(
        source,
        day_offset=2,
        summary_data=_summary(
            entities=[
                {"name": "Coca-Cola", "type": "brand"},
                {"name": "Иван", "type": "person"},
            ]
        ),
    )
    rows = [r1]
    result = await group_analytics(rows, "entities", entity_type="person")
    keys = {g["key"] for g in result["groups"]}
    assert "Иван" in keys
    assert "Coca-Cola" not in keys


@pytest.mark.parametrize("axis", ["sentiment", "content_type"])
async def test_removed_axes_rejected_even_without_rows(axis):
    with pytest.raises(ValueError):
        await group_analytics([], axis)


# ── intent ────────────────────────────────────────────────────────────────────


async def test_group_by_intent_counts_intent_types(source):
    r1 = await _row(source, day_offset=3, summary_data=_summary({"intent_type": "question"}))
    r2 = await _row(source, day_offset=2, summary_data=_summary({"intent_type": "complaint"}))
    r3 = await _row(source, day_offset=1, summary_data=_summary({"intent_type": "question"}))
    rows = [r1, r2, r3]
    result = await group_analytics(rows, "intent")
    keys = {g["key"] for g in result["groups"]}
    assert "question" in keys
    assert "complaint" in keys
    assert result["groups"][0]["count"] == 2


# ── days axis ─────────────────────────────────────────────────────────────────


async def test_days_axis_groups_by_analysis_date(source):
    r1 = await _row(source, day_offset=5, summary_data=_summary(topics=["a"]))
    r2 = await _row(source, day_offset=3, summary_data=_summary(topics=["b"]))
    # Same day as r2 — unique (source, date, period) forces another period_type.
    r3 = await _row(source, day_offset=3, summary_data=_summary(topics=["c"]), period_type=PeriodType.WEEK)
    result = await group_analytics([r1, r2, r3], "days")
    keys = {g["key"] for g in result["groups"]}
    assert keys == {
        (date.today() - timedelta(days=5)).isoformat(),
        (date.today() - timedelta(days=3)).isoformat(),
    }
    busiest = next(g for g in result["groups"] if g["key"] == (date.today() - timedelta(days=3)).isoformat())
    assert busiest["count"] == 2


# ── time_breakdown ────────────────────────────────────────────────────────────


async def test_time_breakdown_adds_per_date_entries(source):
    r1 = await _row(source, day_offset=5, summary_data=_summary(topics=["отпуск"]))
    r2 = await _row(source, day_offset=3, summary_data=_summary(topics=["отпуск"]))
    r3 = await _row(source, day_offset=1, summary_data=_summary(topics=["отпуск"]))
    rows = [r1, r2, r3]
    result = await group_analytics(rows, "themes", time_breakdown=True)
    assert result["time_breakdown"] is True
    group = result["groups"][0]
    assert group["key"] == "отпуск"
    assert group["count"] == 3
    assert len(group["entries"]) == 3
    dates = {e["date"] for e in group["entries"]}
    assert (date.today() - timedelta(days=5)).isoformat() in dates
    assert (date.today() - timedelta(days=3)).isoformat() in dates
    assert (date.today() - timedelta(days=1)).isoformat() in dates


# ── avg_sentiment propagation ─────────────────────────────────────────────────


async def test_avg_sentiment_is_none_when_no_sentiment_data(source):
    r1 = await _row(source, day_offset=1, summary_data=_summary())
    rows = [r1]
    result = await group_analytics(rows, "themes")
    assert result["groups"][0]["avg_sentiment"] is None


async def test_avg_sentiment_computed_across_rows_in_group(source):
    r1 = await _row(source, day_offset=2, summary_data=_summary({"sentiment_score": 0.2}, topics=["t"]))
    r2 = await _row(source, day_offset=1, summary_data=_summary({"sentiment_score": 0.8}, topics=["t"]))
    rows = [r1, r2]
    result = await group_analytics(rows, "themes")
    assert result["groups"][0]["avg_sentiment"] == 0.5


# ── topic_chains ────────────────────────────────────────────────────────────────


async def test_topic_chains_uses_topic_chain_id(source):
    r1 = await _row(source, day_offset=3, topic_chain_id="chain-abc", summary_data=_summary(topics=["т"]))
    r2 = await _row(source, day_offset=1, topic_chain_id="chain-abc", summary_data=_summary(topics=["т"]))
    r3 = await _row(source, day_offset=2, topic_chain_id="chain-xyz", summary_data=_summary(topics=["другое"]))
    rows = [r1, r2, r3]
    result = await group_analytics(rows, "topic_chains")
    chain_ids = {g["chain_id"] for g in result["groups"]}
    assert chain_ids == {"chain-abc", "chain-xyz"}
    abc = next(g for g in result["groups"] if g["chain_id"] == "chain-abc")
    assert abc["count"] == 2
    # Display key is the human label (first topic), not the raw chain id.
    assert abc["key"] == "т"
    xyz = next(g for g in result["groups"] if g["chain_id"] == "chain-xyz")
    assert xyz["key"] == "другое"


async def test_topic_chains_falls_back_to_chain_id_without_label(source):
    r1 = await _row(source, day_offset=1, topic_chain_id="chain-nolabel", summary_data={})
    result = await group_analytics([r1], "topic_chains")
    group = result["groups"][0]
    assert group["key"] == "chain-nolabel"
    assert group["chain_id"] == "chain-nolabel"


async def test_topic_chains_prefers_chain_label_over_topics(source):
    r1 = await _row(source, day_offset=1, topic_chain_id="chain-l", summary_data=_summary(topics=["т"]))
    r1.chain_label = "Горячая тема"
    result = await group_analytics([r1], "topic_chains")
    assert result["groups"][0]["key"] == "Горячая тема"


@pytest.mark.parametrize("score,expected", [(0, "negative"), (.399, "negative"), (.4, "neutral"), (.6, "neutral"), (.601, "positive"), (1, "positive"), (None, None), ("bad", None), (float("nan"), None), (True, None)])
def test_sentiment_bucket_boundaries(score, expected):
    from app.services.ai.reporting import sentiment_bucket
    assert sentiment_bucket(score) == expected


async def test_negative_entities_filter_applies_before_counts_and_averages(source):
    negative = await _row(source, summary_data=_summary({"sentiment_score": .2}, entities=[{"name": "Risk brand", "type": "brand"}]), media_types=["text", "image"])
    positive = await _row(source, day_offset=1, summary_data=_summary({"sentiment_score": .8}, entities=[{"name": "Safe brand", "type": "brand"}]), media_types=["text"])
    result = await group_analytics([negative, positive], "entities", sentiment="negative", media="image", time_breakdown=True)
    assert result["groups"] == [{"key": "Risk brand", "count": 1, "avg_sentiment": .2, "entries": [{"date": date.today().isoformat(), "count": 1, "avg_sentiment": .2}]}]
    assert result["sentiment"] == "negative" and result["media"] == "image"


async def test_filter_missing_sentiment_is_not_neutral_and_media_uses_containment(source):
    unknown = await _row(source, summary_data={}, media_types=["text"])
    neutral = await _row(source, day_offset=1, summary_data={"sentiment_score": .4}, media_types=["text", "video"])
    result = await group_analytics([unknown, neutral], "sources", sentiment="neutral", media="video")
    assert len(result["groups"]) == 1
    assert result["groups"][0]["count"] == 1
    assert result["groups"][0]["avg_sentiment"] == .4


@pytest.mark.parametrize("filters", [{"sentiment": "unknown"}, {"media": "audio"}, {"entity_type": "bogus"}])
async def test_invalid_filters_rejected_on_empty_input(filters):
    with pytest.raises(ValueError):
        await group_analytics([], "entities", **filters)


async def test_theme_membership_all_topics_casefold_and_duplicates(source):
    from app.services.ai.grouping import matches_analytics_group

    first = await _row(source, day_offset=1, summary_data={"topics": ["A", "Birthday", "BIRTHDAY"]})
    second = await _row(source, summary_data=_summary(topics=["birthday"]))
    result = await group_analytics([first, second], "themes", time_breakdown=True)
    group = next(group for group in result["groups"] if group["key"] == "Birthday")
    assert group["count"] == 2 and sum(day["count"] for day in group["entries"]) == 2
    assert all(matches_analytics_group(row, "themes", group["key"]) for row in (first, second))


async def test_entity_counts_unique_analysis_not_duplicate_mentions(source):
    from app.services.ai.grouping import matches_analytics_group

    row = await _row(
        source, summary_data={"entities": [{"name": "Ivan", "type": "person"}, {"name": "Ivan", "type": "person"}]}
    )
    result = await group_analytics([row], "entities", entity_type="person")
    assert result["groups"][0]["count"] == 1
    assert matches_analytics_group(row, "entities", "Ivan", entity_type="person")
