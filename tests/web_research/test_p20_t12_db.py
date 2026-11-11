"""PostgreSQL integration test for durable web-research quotas and KnowledgeSnippet persistence (P20-T12).

Requires an isolated PostgreSQL schema (`DB_TEST_SCHEMA`); verifies:
1. Durable `WebResearchQuota` (`web_research_quotas`) atomic persistence across fresh sessions,
   daily quota and burst rate-limit enforcement, and cross-tenant quota isolation.
2. Production `KnowledgeSnippet` save (`save_evidence=True`) and list (`list_saved_snapshots_async`)
   with fresh database actor/tenant authorization (`source.view` / `source.create`), refusing viewers
   without `source.create` and isolating saved snippets across tenants.
3. Stub search results are refused with `stub_cannot_be_live_evidence` and write zero `KnowledgeSnippet` rows.
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
from app.models import KnowledgeSnippet, Tenant
from app.services.web_research import (
    ChromiumUnderHoodEngine,
    RawHttpResponse,
    WebResearchBudget,
    WebResearchRefusal,
    WebResearchService,
)
from app.services.web_research.models import WebResearchQuota
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def _scoped(world, workspace: int = 0, *, role: str = "owner") -> ExitStack:
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    user_idx = 0 if (workspace == 0 and role == "owner") else (1 if workspace == 0 else 2)
    stack.enter_context(permission_scope(world.users[user_idx], member_grants=workspace_grants(role)))
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
            await session.execute(delete(WebResearchQuota).where(WebResearchQuota.tenant_id.in_(tenant_ids)))
        async with async_session_maker() as session:
            remaining_snippets = await session.scalar(
                select(func.count()).select_from(KnowledgeSnippet).where(KnowledgeSnippet.tenant_id.in_(tenant_ids))
            )
            remaining_quotas = await session.scalar(
                select(func.count()).select_from(WebResearchQuota).where(WebResearchQuota.tenant_id.in_(tenant_ids))
            )
            assert remaining_snippets == 0
            assert remaining_quotas == 0


async def test_live_page_snapshot_persists_to_knowledge_snippet_and_enforces_durable_quotas(research_world):
    def _resolver(host: str, port: int) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    async def _transport(url: str, timeout: float, max_bytes: int) -> RawHttpResponse:
        # The web request is in flight while fresh sessions can update-lock the
        # workspace and quota rows. Admission must have committed before I/O.
        async with async_session_maker() as probe, probe.begin():
            await probe.scalar(
                select(Tenant.id).where(Tenant.id == research_world.tenants[0].id).with_for_update(nowait=True)
            )
            await probe.scalar(
                select(WebResearchQuota.tenant_id)
                .where(
                    WebResearchQuota.tenant_id == research_world.tenants[0].id,
                    WebResearchQuota.account_key == "default",
                )
                .with_for_update(nowait=True)
            )
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

    budget = WebResearchBudget(
        daily_quota=4,
        burst_limit=4,
        rate_per_minute=60.0,
        max_searches_per_run=2,
        max_fetches_per_run=2,
    )
    service = WebResearchService(chromium_engine=ChromiumUnderHoodEngine(search_runner=_search))

    # 1. Stub results are refused when saving evidence; durable quota row is recorded in PostgreSQL
    with _scoped(research_world, workspace=0, role="owner"):
        with pytest.raises(WebResearchRefusal) as stub_exc:
            await service.research(
                {"query": "stub check", "provider": "stub", "save_evidence": True},
                budget=budget,
                resolver=_resolver,
                require_durable=True,
            )
        assert stub_exc.value.code == "stub_cannot_be_live_evidence"

        live_out = await service.research(
            {"query": "партнёр", "preset": "site_brief", "fetch_top_n": 1, "save_evidence": True},
            budget=budget,
            resolver=_resolver,
            transport=_transport,
            require_durable=True,
        )
        assert live_out.is_stub is False
        snippet_id = live_out.citations[0].saved_snippet_id
        assert snippet_id is not None

        # 2. Fresh PostgreSQL session sees persisted KnowledgeSnippet and durable quota count
        status_out = await service.status_async(budget=budget, require_durable=True)
        assert status_out.budget["daily_used"] == 2
        assert status_out.budget["daily_remaining"] == 2

        saved_rows = await service.list_saved_snapshots_async(require_durable=True)
        assert len(saved_rows) == 1
        assert saved_rows[0]["id"] == snippet_id
        assert saved_rows[0]["source_ref"] == "https://partner.example.com/profile"

        # Exhaust remaining 2 daily quota units for tenant 0 across a new service instance (survives restart!)
        fresh_service = WebResearchService(chromium_engine=ChromiumUnderHoodEngine(search_runner=_search))
        await fresh_service.research(
            {"query": "повторный запрос", "fetch_top_n": 1, "save_evidence": False},
            budget=budget,
            resolver=_resolver,
            transport=_transport,
            require_durable=True,
        )
        with pytest.raises(WebResearchRefusal) as quota_exc:
            await fresh_service.research(
                {"query": "сверх лимита", "fetch_top_n": 0, "save_evidence": False},
                budget=budget,
                resolver=_resolver,
                transport=_transport,
                require_durable=True,
            )
        assert quota_exc.value.code == "budget_exhausted"

    # 3. Viewer without source.create is refused when attempting save_evidence=True
    with _scoped(research_world, workspace=0, role="viewer"):
        with pytest.raises(WebResearchRefusal) as perm_exc:
            await service.research(
                {"query": "viewer save attempt", "fetch_top_n": 1, "save_evidence": True},
                budget=budget,
                resolver=_resolver,
                transport=_transport,
                require_durable=True,
            )
        assert perm_exc.value.code == "permission_denied"

    # 4. Cross-tenant isolation: tenant 1 has full untouched quota and cannot see tenant 0's KnowledgeSnippet
    with _scoped(research_world, workspace=1, role="owner"):
        other_status = await service.status_async(budget=budget, require_durable=True)
        assert other_status.budget["daily_used"] == 0
        other_rows = await service.list_saved_snapshots_async(require_durable=True)
        assert other_rows == []
        foreign = await KnowledgeSnippet.objects.visible("cloud", id=snippet_id).first()
        assert foreign is None
