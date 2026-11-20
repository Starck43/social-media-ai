"""Regression coverage for archive-aware inbox deep-link selection."""

from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.web import communications as routes


class _SessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Request:
    def __init__(self, query_params):
        self.query_params = query_params


@pytest.fixture
def communications_doubles(monkeypatch):
    tenant_id = 41
    threads = {
        101: {"id": 101, "is_archived": False},
        202: {"id": 202, "is_archived": True},
    }
    permission_checks = []
    detail_calls = []
    triage_calls = []
    archive_calls = []

    def guarded_view():
        # The route must continue through the permission/tenant-fenced service
        # calls; it must not fetch an excluded deep-link target directly.
        assert routes.current_tenant_id() == tenant_id
        permission_checks.append(tenant_id)
        return tenant_id

    async def list_inbox_threads(_session, **kwargs):
        guarded_view()
        archive_calls.append(kwargs["is_archived"])
        selected = [thread for thread in threads.values() if thread["is_archived"] == kwargs["is_archived"]]
        return {"items": selected, "next_before_id": None}

    async def list_triaged_threads(_session, *, thread_ids, include_archived, **_kwargs):
        guarded_view()
        assert include_archived is archive_calls[-1]
        return {
            "items": [{"thread_id": thread_id, "category": "fyi"} for thread_id in thread_ids],
            "counts_by_category": {},
        }

    async def get_thread_detail(_session, thread_id):
        guarded_view()
        detail_calls.append(thread_id)
        return {"thread": {"id": thread_id}, "messages": []}

    async def triage_thread(_session, thread_id):
        guarded_view()
        triage_calls.append(thread_id)
        return {"thread_id": thread_id, "category": "fyi"}

    monkeypatch.setattr(routes, "current_tenant_id", lambda: tenant_id)
    monkeypatch.setattr(routes, "snippet_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(routes, "async_session_maker", lambda: _SessionContext())
    monkeypatch.setattr(routes, "list_inbox_threads", list_inbox_threads)
    monkeypatch.setattr(routes, "list_triaged_threads", list_triaged_threads)
    monkeypatch.setattr(routes, "get_thread_detail", get_thread_detail)
    monkeypatch.setattr(routes, "triage_thread", triage_thread)
    monkeypatch.setattr(routes, "render", lambda _request, _template, **context: NS(context=context, status_code=200))

    return NS(
        request=lambda params: _Request(params),
        threads=threads,
        permission_checks=permission_checks,
        detail_calls=detail_calls,
        triage_calls=triage_calls,
        archive_calls=archive_calls,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "archive_value", "thread_id", "expected_detail", "fallback_triage"),
    [
        ({"is_archived": "false"}, False, 101, 101, False),
        ({"is_archived": "true"}, True, 202, 202, False),
        ({}, False, 202, 202, True),
    ],
)
async def test_archive_filter_and_unfiltered_deep_links_select_detail_and_triage(
    communications_doubles,
    params,
    archive_value,
    thread_id,
    expected_detail,
    fallback_triage,
):
    result = await routes.communications_page(
        communications_doubles.request(params),
        thread_id=thread_id,
        is_archived=archive_value if params else None,
    )

    context = result.context
    assert context["inbox_threads"][0]["id"] == (101 if not archive_value else 202)
    assert context["selected_thread_detail"]["thread"]["id"] == expected_detail
    assert context["selected_thread_triage"]["thread_id"] == expected_detail
    assert communications_doubles.archive_calls[-1] is archive_value
    assert (expected_detail in communications_doubles.triage_calls) is fallback_triage


@pytest.mark.asyncio
async def test_explicit_archive_filter_cannot_select_nonarchived_deep_link(
    communications_doubles,
):
    result = await routes.communications_page(
        communications_doubles.request({"is_archived": "true"}),
        thread_id=101,
        is_archived=True,
    )

    context = result.context
    assert [thread["id"] for thread in context["inbox_threads"]] == [202]
    assert context["selected_thread_detail"]["thread"]["id"] == 202
    assert context["selected_thread_triage"]["thread_id"] == 202
    assert 101 not in communications_doubles.detail_calls
    assert 101 not in communications_doubles.triage_calls
    assert communications_doubles.permission_checks
    assert all(tenant_id == 41 for tenant_id in communications_doubles.permission_checks)