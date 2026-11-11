"""PostgreSQL integration test for tenant-scoped web research evidence snapshots (P20-T12).

Requires an isolated PostgreSQL schema (`DB_TEST_SCHEMA`); verifies that live
fetched page snapshots persist into `KnowledgeSnippet` strictly inside the active
tenant, while stub results are refused with zero DB rows written.
"""

from __future__ import annotations

import socket
from contextlib import ExitStack

import pytest
from sqlalchemy import delete, func, select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import KnowledgeSnippet
from app.services.web_research import (
    ChromiumUnderHoodEngine,
    RawHttpResponse,
    WebResearchRefusal,
    WebResearchService,
)
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def _scoped(world, workspace: int = 0) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(world.users[0 if workspace == 0 else 2], member_grants=workspace_grants("owner"))
    )
    return stack


@pytest.fixture
async def research_world(personal_world):  # noqa: F811
    require_isolated_schema()
    tenant_ids = [t.id for t in personal_world.tenants]
    try:
        yield personal_world
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(KnowledgeSnippet).where(KnowledgeSnippet.tenant_id.in_(tenant_ids)))
        async with async_session_maker() as session:
            remaining = await session.scalar(
                select(func.count()).select_from(KnowledgeSnippet).where(KnowledgeSnippet.tenant_id.in_(tenant_ids))
            )
            assert remaining == 0


async def test_live_page_snapshot_persists_to_knowledge_snippet_and_isolates_tenants(research_world):
    def _resolver(host: str, port: int) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    async def _transport(url: str, timeout: float, max_bytes: int) -> RawHttpResponse:
        if url.endswith("/robots.txt"):
            return RawHttpResponse(200, {"content-type": "text/plain"}, b"User-agent: *\nAllow: /\n")
        return RawHttpResponse(
            200,
            {"content-type": "text/html; charset=utf-8"},
            "<html><title>Карточка контрагента</title><body><p>Проверенные публичные данные.</p></body></html>".encode(
                "utf-8"
            ),
        )

    async def _search(query: str, max_results: int) -> list[dict[str, str]]:
        return [{"title": "Карточка контрагента", "url": "https://partner.example.com/profile", "snippet": "Данные"}]

    service = WebResearchService(chromium_engine=ChromiumUnderHoodEngine(search_runner=_search))

    with _scoped(research_world, workspace=0):
        async with async_session_maker() as session, session.begin():
            with pytest.raises(WebResearchRefusal) as stub_exc:
                await service.research(
                    {"query": "stub check", "provider": "stub", "save_evidence": True},
                    resolver=_resolver,
                    session=session,
                )
            assert stub_exc.value.code == "stub_cannot_be_live_evidence"

            live_out = await service.research(
                {"query": "партнёр", "preset": "site_brief", "fetch_top_n": 1, "save_evidence": True},
                resolver=_resolver,
                transport=_transport,
                session=session,
            )
            assert live_out.is_stub is False
            snippet_id = live_out.citations[0].saved_snippet_id
            assert snippet_id is not None

    with _scoped(research_world, workspace=1):
        foreign = await KnowledgeSnippet.objects.visible("cloud", id=snippet_id).first()
        assert foreign is None
