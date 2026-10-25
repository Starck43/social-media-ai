"""No-DB contract for a dry-run collection.

Owner command: `.venv/bin/python -m pytest tests/test_collect_dry_run.py -q --noconftest -o addopts=`

A rehearsal must fetch, count and evaluate the task's text scope, but write no
raw rows, call no model and leave the collection cursor alone. These tests pin
all four, because a rehearsal that stages or advances `last_checked` silently
changes what the following real run sees.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.monitoring.collector import ContentCollector
from app.services.monitoring.selection import build_scope
from app.types import SourceType


def _source():
    return SimpleNamespace(
        id=7,
        name="wall",
        tenant_id=1,
        platform_id=1,
        source_type=SourceType.USER,
        external_id="wall",
        params={"mode": "api"},
        last_checked=None,
        platform=SimpleNamespace(params={}),
    )


def _content():
    return [
        {
            "external_id": "1_1",
            "text": "Поездка в Италию с Древиз",
            "date": datetime(2026, 10, 5, tzinfo=timezone.utc),
            "platform": "vkontakte",
            "author": {"platform": "vk", "external_id": "1", "name": "Автор"},
            "permalink": "https://vk.com/wall1_1",
        },
        {
            "external_id": "1_2",
            "text": "Новая коллекция декоров",
            "date": datetime(2026, 10, 4, tzinfo=timezone.utc),
            "platform": "vkontakte",
            "author": {"platform": "vk", "external_id": "1", "name": "Автор"},
            "permalink": "https://vk.com/wall1_2",
        },
    ]


@pytest.mark.asyncio
async def test_dry_run_returns_selection_preview_without_side_effects(monkeypatch):
    collector = ContentCollector()

    async def fake_collect_data(source, content_type="posts"):
        return _content()

    monkeypatch.setattr(
        "app.services.monitoring.collector.get_social_client",
        lambda platform: SimpleNamespace(collect_data=fake_collect_data),
    )
    monkeypatch.setattr(ContentCollector, "_count_new_items", AsyncMock(return_value=2))

    async def forbidden(*args, **kwargs):
        raise AssertionError("a dry run must not stage rows")

    monkeypatch.setattr(ContentCollector, "_stage_items", forbidden)
    monkeypatch.setattr(
        "app.models.Source.objects.update_last_checked",
        AsyncMock(side_effect=AssertionError("a dry run must not move the cursor")),
    )

    result = await collector.collect_from_source(
        _source(),
        analyze=True,
        dry_run=True,
        selection_scope=build_scope({"keywords_list": ["Италия"]}),
    )

    assert result["dry_run"] is True
    assert result["content_count"] == 2
    assert result["new_items"] == 2
    assert result["staged"] == 0
    assert result["analyzed"] is False
    assert result["selection"]["eligible"] == 1
    assert result["selection"]["rejected"] == 1
    assert result["selection"]["sample"][0]["text"].startswith("Поездка в Италию")
    # The bounded preview is returned separately and never contains more than
    # the fixed cap; the selection sample is capped even tighter.
    assert len(result["preview"]) == 2
    assert len(result["selection"]["sample"]) == 1
