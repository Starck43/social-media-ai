from typing import Optional, cast

import pytest

from app.api.v1.endpoints.dashboard import (
	get_sentiment_trends_aggregate,
	get_top_topics_aggregate,
	get_llm_provider_stats_aggregate,
	get_content_mix_aggregate,
	get_engagement_metrics_aggregate,
)
from app.models.user import User


class DummyUser:
	is_superuser = True
	username = "tester"


@pytest.mark.asyncio
async def test_get_sentiment_trends_aggregate(monkeypatch):
	class DummyAgg:
		async def get_sentiment_trends(self, source_id: Optional[int], scenario_id: Optional[int], days: int, group_by: str):
			return [
			]

	# Patch constructor to return our dummy
	monkeypatch.setattr("app.api.v1.endpoints.dashboard.ReportAggregator", lambda: DummyAgg())

	resp = await get_sentiment_trends_aggregate(
		source_id=1,
		scenario_id=None,
		days=7,
		group_by="day",
		current_user=cast(User, DummyUser()),
	)
	assert "trends" in resp
	assert resp["period_days"] == 7


@pytest.mark.asyncio
async def test_get_top_topics_aggregate(monkeypatch):
	class DummyAgg:
		async def get_top_topics(self, source_id: Optional[int], scenario_id: Optional[int], days: int, limit: int):
			return [
				{"topic": "AI", "count": 12, "sentiment": 0.4},
			]

	monkeypatch.setattr("app.api.v1.endpoints.dashboard.ReportAggregator", lambda: DummyAgg())

	resp = await get_top_topics_aggregate(
		source_id=None,
		scenario_id=2,
		days=14,
		limit=5,
		current_user=cast(User, DummyUser()),
	)
	assert "topics" in resp
	assert resp["total_topics"] == 1


@pytest.mark.asyncio
async def test_get_llm_provider_stats_aggregate(monkeypatch):
	class DummyAgg:
		async def get_llm_provider_stats(self, source_id: Optional[int], scenario_id: Optional[int], days: int):
			return {
				"providers": {
					"openai": {"requests": 10, "tokens": 12000, "estimated_cost": 12},
				}
			}

	monkeypatch.setattr("app.api.v1.endpoints.dashboard.ReportAggregator", lambda: DummyAgg())

	resp = await get_llm_provider_stats_aggregate(
		source_id=None,
		scenario_id=None,
		days=30,
		current_user=cast(User, DummyUser()),
	)
	assert "providers" in resp
	assert "openai" in resp["providers"]


@pytest.mark.asyncio
async def test_get_content_mix_aggregate(monkeypatch):
	class DummyAgg:
		async def get_content_mix(self, source_id: Optional[int], scenario_id: Optional[int], days: int):
			return {"text": 80, "image": 15, "video": 5}

	monkeypatch.setattr("app.api.v1.endpoints.dashboard.ReportAggregator", lambda: DummyAgg())

	resp = await get_content_mix_aggregate(
		source_id=1,
		scenario_id=2,
		days=7,
		current_user=cast(User, DummyUser()),
	)
	assert set(resp.keys()) >= {"text", "image", "video"}


@pytest.mark.asyncio
async def test_get_engagement_metrics_aggregate(monkeypatch):
	class DummyAgg:
		async def get_engagement_metrics(self, source_id: Optional[int], scenario_id: Optional[int], days: int):
			return {"avg_reactions": 3.2, "avg_comments": 1.1}

	monkeypatch.setattr("app.api.v1.endpoints.dashboard.ReportAggregator", lambda: DummyAgg())

	resp = await get_engagement_metrics_aggregate(
		source_id=None,
		scenario_id=None,
		days=7,
		current_user=cast(User, DummyUser()),
	)
	assert "avg_reactions" in resp
	assert "avg_comments" in resp
