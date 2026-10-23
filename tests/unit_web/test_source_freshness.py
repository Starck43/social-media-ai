"""Freshness banner arithmetic without a database.

`_stale_sources_summary` is the only place that turns the manager's staleness
predicate into the numbers on the page. Task-owned query lanes are rendered
rows too, but their freshness belongs to the owning task, so they must not
raise a workspace-wide alert or inflate the active total. The manager double
returns the whole active stale set, lanes included, exactly as the real query
would; only the helper decides what counts.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.web import sources as sources_web


def _row(source_id, *, active=True, last_checked=None, owner_task_id=None):
    return SimpleNamespace(
        id=source_id,
        is_active=active,
        last_checked=last_checked,
        owner_task_id=owner_task_id,
    )


def _fake_manager(monkeypatch, rows, calls):
    """Replace the manager predicate and record the window it was asked for."""
    from app.models.managers.source_manager import SourceManager

    async def get_sources_for_monitoring(_self, **kwargs):
        calls.append(kwargs)
        return list(rows)

    monkeypatch.setattr(SourceManager, "get_sources_for_monitoring", get_sources_for_monitoring)


@pytest.mark.asyncio
async def test_the_summary_counts_only_ordinary_rows_the_manager_reports(monkeypatch):
    """Fresh, stale, never, inactive and task-owned rows in one page."""
    monkeypatch.setattr(settings, "SOURCE_STALE_HOURS", 7)
    monkeypatch.setattr(sources_web, "perms_can", lambda *_args: True)
    calls = []
    # The real predicate is active-only and knows nothing about lane ownership,
    # so it reports the stale/never lanes beside the ordinary sources.
    _fake_manager(monkeypatch, [_row(2), _row(3), _row(4), _row(5)], calls)
    sources = [
        _row(1, last_checked="checked-a-minute-ago"),
        _row(2, last_checked="checked-a-week-ago"),
        _row(3),
        _row(4, last_checked="checked-a-week-ago", owner_task_id=17),
        _row(5, owner_task_id=17),
        _row(6, active=False, last_checked="checked-a-week-ago"),
    ]

    summary = await sources_web._stale_sources_summary(SimpleNamespace(), sources)

    assert calls == [{"hours_since_check": 7}], "the configured window is passed through"
    assert summary == {
        "hours": 7,
        "count": 2,
        "active_total": 3,
        "never": 1,
        "ids": frozenset({2, 3}),
        "never_ids": frozenset({3}),
    }
    # Lane ids and the inactive ordinary row never enter either set.
    assert 4 not in summary["ids"] and 5 not in summary["ids"] and 6 not in summary["ids"]


@pytest.mark.asyncio
async def test_task_owned_lanes_stay_on_the_page_but_never_raise_the_alert(monkeypatch):
    """A page of lanes only reports zero stale sources and zero active ones."""
    monkeypatch.setattr(sources_web, "perms_can", lambda *_args: True)
    calls = []
    _fake_manager(monkeypatch, [_row(4), _row(5)], calls)
    sources = [
        _row(4, last_checked="checked-a-week-ago", owner_task_id=17),
        _row(5, owner_task_id=17),
    ]

    summary = await sources_web._stale_sources_summary(SimpleNamespace(), sources)

    assert calls, "the helper still receives and queries for the rendered rows"
    assert summary["count"] == 0
    assert summary["active_total"] == 0
    assert summary["never"] == 0
    assert summary["ids"] == frozenset()
    assert summary["never_ids"] == frozenset()


@pytest.mark.asyncio
async def test_without_source_view_the_manager_is_never_instantiated(monkeypatch):
    """The permission gate answers first; no staleness read happens at all."""
    import app.models.managers.source_manager as source_manager_module

    monkeypatch.setattr(sources_web, "perms_can", lambda *_args: False)

    class _ExplodingManager:
        def __init__(self):
            raise AssertionError("the manager must not be instantiated without source.view")

    monkeypatch.setattr(source_manager_module, "SourceManager", _ExplodingManager)

    summary = await sources_web._stale_sources_summary(SimpleNamespace(), [_row(2)])

    assert summary is None
