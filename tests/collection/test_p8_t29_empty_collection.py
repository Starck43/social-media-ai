"""An empty search reports its known zero count without spending the next lane's budget."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.collection.context import build_collection_context
from app.services.monitoring import collector
from app.types import SourceType

__source_only__ = True


async def test_empty_budgeted_collection_returns_known_zero_and_preserves_source_params(monkeypatch):
    original = {"mode": "user"}
    platform = SimpleNamespace(id=1, params={})
    source = SimpleNamespace(
        id=10,
        name="Search",
        platform_id=1,
        platform=platform,
        source_type=SourceType.SEARCH,
        params=original,
        last_checked=None,
    )
    client = SimpleNamespace(collect_data=AsyncMock(return_value=[]))
    monkeypatch.setattr(collector, "get_social_client", lambda supplied: client)
    context = build_collection_context({"collection": {"limit": 20}}, platform="vk", source_id=10)
    instance = object.__new__(collector.ContentCollector)
    result = await instance.collect_from_source(source, analyze=False, collection_context=context, report_empty=True)
    assert result["content_count"] == 0
    assert result["parent_count"] == 0
    assert result["limit_reached"] is False
    assert source.params is original
    client.collect_data.assert_awaited_once()


async def test_legacy_empty_collection_retains_its_return_contract(monkeypatch):
    source = SimpleNamespace(
        id=10,
        name="Search",
        platform_id=1,
        platform=SimpleNamespace(params={}),
        source_type=SourceType.SEARCH,
        params={},
        last_checked=None,
    )
    monkeypatch.setattr(
        collector, "get_social_client", lambda supplied: SimpleNamespace(collect_data=AsyncMock(return_value=[]))
    )
    instance = object.__new__(collector.ContentCollector)
    assert await instance.collect_from_source(source, analyze=False) is None
