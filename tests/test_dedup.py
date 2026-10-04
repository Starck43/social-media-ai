"""Content deduplication: hash functions, filter, analyzer integration.

No network: `_analyze_text` is stubbed, real Postgres rows are created and
removed afterward (legacy tests run in the bootstrap workspace, see conftest).
"""

import uuid
from datetime import date

import pytest

from app.models import AIAnalytics, Platform, Source
from app.services.ai import dedup
from app.services.ai.analyzer import AIAnalyzer
from app.types import PeriodType, SourceType


def _item(text: str, external_id: str, platform: str = "telegram") -> dict:
    return {
        "id": external_id.rsplit("_", 1)[-1],
        "external_id": external_id,
        "text": text,
        "platform": platform,
        "date": None,
    }


class TestHashFunctions:
    def test_item_hash_is_stable(self):
        a = _item("hello", "chat_1")
        assert dedup.item_hash(a) == dedup.item_hash(dict(a))

    def test_item_hash_ignores_volatile_fields(self):
        a = _item("hello", "chat_1")
        b = _item("hello", "chat_1")
        b["views"], b["reactions"] = 999, 42  # counters change between fetches
        assert dedup.item_hash(a) == dedup.item_hash(b)

    def test_item_hash_differs_by_text_and_id(self):
        base = _item("hello", "chat_1")
        assert dedup.item_hash(base) != dedup.item_hash(_item("hell0", "chat_1"))
        assert dedup.item_hash(base) != dedup.item_hash(_item("hello", "chat_2"))

    def test_batch_hash_is_order_independent(self):
        one = [_item("a", "c_1"), _item("b", "c_2")]
        two = list(reversed(one))
        assert dedup.batch_hash(one) == dedup.batch_hash(two)
        assert dedup.batch_hash(one) != dedup.batch_hash(one[:1])


@pytest.fixture
async def source():
    """A throwaway Telegram source on the seeded platform row."""
    platform = await Platform.objects.filter(platform_type="telegram").first()
    assert platform is not None, "telegram platform row is missing — run `alembic upgrade head`"
    src = await Source.objects.create(
        platform_id=platform.id,
        name="Dedup Channel",
        source_type=SourceType.CHANNEL.name,
        external_id=f"dedup-{uuid.uuid4().hex[:8]}",
        params={},
        is_active=True,
    )
    yield src
    await AIAnalytics.objects.filter(source_id=src.id).delete()
    await Source.objects.filter(id=src.id).delete()


async def _make_analysis(src, items, summary_extra=None):
    summary = {"analysis_title": "t", "analysis_summary": "s"}
    if summary_extra:
        summary.update(summary_extra)
    return await AIAnalytics.objects.create(
        source_id=src.id,
        summary_data=summary,
        content_hash=dedup.batch_hash(items) if items else None,
        analysis_date=date.today(),
        period_type=PeriodType.DAY,
    )


class TestFilterAnalyzed:
    @pytest.mark.asyncio
    async def test_empty_batch_short_circuits(self, source):
        items, row = await dedup.filter_analyzed([], source.id)
        assert items == [] and row is None

    @pytest.mark.asyncio
    async def test_unknown_batch_returns_everything(self, source):
        items = [_item("new post", "chat_9")]
        got, row = await dedup.filter_analyzed(items, source.id)
        assert got == items and row is None

    @pytest.mark.asyncio
    async def test_identical_batch_is_skipped(self, source):
        items = [_item("post a", "chat_1"), _item("post b", "chat_2")]
        await _make_analysis(source, items)
        got, row = await dedup.filter_analyzed(list(reversed(items)), source.id)
        assert got == [] and row is not None

    @pytest.mark.asyncio
    async def test_overlapping_batch_returns_only_new_items(self, source):
        old = [_item("old post", "chat_1")]
        await _make_analysis(source, old, summary_extra={"content_hashes": [dedup.item_hash(i) for i in old]})
        new = _item("brand new", "chat_2")
        got, row = await dedup.filter_analyzed(old + [new], source.id)
        assert got == [new] and row is None

    @pytest.mark.asyncio
    async def test_fail_open_on_broken_lookup(self, source, monkeypatch):
        """A dedup error must degrade to analyzing, never to skipping."""

        def boom(*args, **kwargs):
            raise RuntimeError("lookup exploded")

        monkeypatch.setattr(dedup, "batch_hash", boom)
        items = [_item("post", "chat_1")]
        got, row = await dedup.filter_analyzed(items, source.id)
        assert got == items and row is None


class TestAnalyzerIntegration:
    @pytest.mark.asyncio
    async def test_a_real_result_is_persisted(self, source, monkeypatch):
        """A non-stub result must reach `ai_analytics`.

        The dedup tests above all read the saved row, so a save that silently
        never happens does not fail them — they see an empty table and report it
        as "the dedup filter did not work". The guard belongs here instead: the
        "is this a usable result?" loop once `continue`d *before* setting
        `has_results`, which made every result look unusable, so no analysis was
        ever saved and no batch was ever marked as covered.
        """

        async def fake_analyze(
            self, text_items, agent_scenario, content_stats, platform_name, src, trigger_config=None
        ):
            return {
                "request": {"model": "test-model", "prompt": "p"},
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 10}},
                "parsed": {"main_topics": ["persisted"]},
            }

        monkeypatch.setattr(AIAnalyzer, "_analyze_text", fake_analyze)
        monkeypatch.setattr(AIAnalyzer, "_analyze_images", lambda *a, **k: _async_none())
        monkeypatch.setattr(AIAnalyzer, "_analyze_videos", lambda *a, **k: _async_none())
        monkeypatch.setattr(
            AIAnalyzer,
            "_create_unified_summary",
            lambda *a, **k: _async_value({"analysis_title": "t", "analysis_summary": "s"}),
        )

        saved = await AIAnalyzer().base_analyze_content([_item("keep me", "chat_1")], source)
        assert saved is not None, "a real analysis result must be saved, not dropped"

        rows = await AIAnalytics.objects.filter(source_id=source.id)
        assert len(rows) == 1
        assert (rows[0].summary_data or {}).get("content_hashes") == [dedup.item_hash(_item("keep me", "chat_1"))]

    @pytest.mark.asyncio
    async def test_an_error_stub_is_not_saved(self, source, monkeypatch):
        """The counterpart: a Timeout/Error stub must leave the batch unanalysed,
        so the next run retries it instead of treating it as covered."""

        async def fake_analyze(
            self, text_items, agent_scenario, content_stats, platform_name, src, trigger_config=None
        ):
            return {
                "request": {"model": "test-model", "prompt": "p"},
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 10}},
                "parsed": {"analysis": "Timeout after 30s"},
            }

        monkeypatch.setattr(AIAnalyzer, "_analyze_text", fake_analyze)
        monkeypatch.setattr(AIAnalyzer, "_analyze_images", lambda *a, **k: _async_none())
        monkeypatch.setattr(AIAnalyzer, "_analyze_videos", lambda *a, **k: _async_none())
        monkeypatch.setattr(
            AIAnalyzer,
            "_create_unified_summary",
            lambda *a, **k: _async_value({"analysis_title": "t", "analysis_summary": "s"}),
        )

        saved = await AIAnalyzer().base_analyze_content([_item("times out", "chat_1")], source)
        assert saved is None
        assert await AIAnalytics.objects.filter(source_id=source.id).rows() == []

    @pytest.mark.asyncio
    async def test_replayed_batch_is_not_paid_for_twice(self, source, monkeypatch):
        """Second identical call returns the first analysis without an LLM call."""
        calls: list[list[dict]] = []

        async def fake_analyze(
            self, text_items, agent_scenario, content_stats, platform_name, src, trigger_config=None
        ):
            calls.append(text_items)
            return {
                "request": {"model": "test-model", "prompt": "p"},
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 10}},
                "parsed": {"main_topics": ["dedup"]},
            }

        # Skip image/video legs and unified summary round-trip.
        monkeypatch.setattr(AIAnalyzer, "_analyze_text", fake_analyze)
        monkeypatch.setattr(
            AIAnalyzer,
            "_analyze_images",
            lambda *a, **k: _async_none(),
        )
        monkeypatch.setattr(
            AIAnalyzer,
            "_analyze_videos",
            lambda *a, **k: _async_none(),
        )
        monkeypatch.setattr(
            AIAnalyzer,
            "_create_unified_summary",
            lambda *a, **k: _async_value({"analysis_title": "t", "analysis_summary": "s"}),
        )

        items = [_item("replay me", "chat_1")]
        first = await AIAnalyzer().base_analyze_content(list(items), source)
        assert first is not None and first.content_hash == dedup.batch_hash(items)
        assert (first.summary_data or {}).get("content_hashes") == [dedup.item_hash(items[0])]
        assert len(calls) == 1

        second = await AIAnalyzer().base_analyze_content(list(items), source)
        assert second is not None and second.id == first.id
        assert len(calls) == 1  # no second LLM call

    @pytest.mark.asyncio
    async def test_partial_overlap_pays_only_for_new_items(self, source, monkeypatch):
        calls: list[list[dict]] = []

        async def fake_analyze(
            self, text_items, agent_scenario, content_stats, platform_name, src, trigger_config=None
        ):
            calls.append(text_items)
            return {
                "request": {"model": "test-model", "prompt": "p"},
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 10}},
                "parsed": {"main_topics": ["dedup"]},
            }

        monkeypatch.setattr(AIAnalyzer, "_analyze_text", fake_analyze)
        monkeypatch.setattr(AIAnalyzer, "_analyze_images", lambda *a, **k: _async_none())
        monkeypatch.setattr(AIAnalyzer, "_analyze_videos", lambda *a, **k: _async_none())
        monkeypatch.setattr(
            AIAnalyzer,
            "_create_unified_summary",
            lambda *a, **k: _async_value({"analysis_title": "t", "analysis_summary": "s"}),
        )

        await AIAnalyzer().base_analyze_content([_item("first", "chat_1")], source)
        fresh = _item("second", "chat_2")
        await AIAnalyzer().base_analyze_content([_item("first", "chat_1"), fresh], source)

        assert len(calls) == 2
        # Only the genuinely new item reached the LLM.
        assert calls[1][0]["external_id"] == "chat_2"

        # The daily row's dedup set grew to cover both batches.
        rows = await AIAnalytics.objects.filter(source_id=source.id)
        stored = set((rows[0].summary_data or {}).get("content_hashes") or [])
        assert {dedup.item_hash(_item("first", "chat_1")), dedup.item_hash(fresh)} <= stored


def _async_none():
    async def _n():
        return None

    return _n()


def _async_value(value):
    async def _v():
        return value

    return _v()
