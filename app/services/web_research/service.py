"""Web research orchestration service: search -> fetch -> evidence-linked summary (P20-T12)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from app.core.outbound import Resolver, StaticPolicy, default_dns_resolver
from app.core.tenant_context import current_tenant_id
from app.services.web_research.contracts import (
    RESEARCH_PRESETS,
    STUB_SEARCH_MARKER,
    CitationItemOut,
    PageSnapshotOut,
    ProviderCapabilityConfig,
    SearchHitOut,
    SearchProviderKind,
    TenantBudgetTracker,
    WebFetchRequest,
    WebResearchBudget,
    WebResearchRefusal,
    WebResearchRunOut,
    WebResearchStatusOut,
    WebSearchRequest,
    resolve_search_capability,
)
from app.services.web_research.fetcher import (
    ChromiumUnderHoodEngine,
    HttpTransport,
    build_stub_search_hits,
    fetch_public_page,
    validate_public_url,
)

YandexSearchRunner = Callable[[str, int], Awaitable[list[dict[str, str]]]]


def _resolve_tenant_id(explicit_tenant_id: int | None = None) -> int:
    tid = explicit_tenant_id if explicit_tenant_id is not None else current_tenant_id()
    if type(tid) is not int or tid <= 0:
        raise WebResearchRefusal("permission_denied")
    return tid


async def _authorize_actor_in_db(
    session: Any,
    tenant_id: int,
    *,
    require_create: bool = False,
) -> tuple[int, int, str, tuple[str, ...]]:
    """Re-verify active user, active workspace, active membership, and source grants in PostgreSQL."""
    if session is None:
        raise WebResearchRefusal("permission_denied")
    try:
        from sqlalchemy import select

        from app.core.access_catalog import RESOURCES, workspace_grants
        from app.core.permissions import get_current_user, has_permission
        from app.core.tenant_context import is_bypass
        from app.models import Tenant, TenantUser, User, WorkspaceRole
        from app.types import ActionType
    except Exception:
        raise WebResearchRefusal("permission_denied") from None

    ctx_tenant = current_tenant_id()
    actor = get_current_user()
    if (
        is_bypass()
        or type(tenant_id) is not int
        or tenant_id <= 0
        or (ctx_tenant is not None and ctx_tenant != tenant_id)
        or actor is None
        or not getattr(actor, "is_active", False)
    ):
        raise WebResearchRefusal("permission_denied")

    required_action = ActionType.CREATE if require_create else ActionType.VIEW
    try:
        user_id = await session.scalar(
            select(User.id).where(User.id == actor.id, User.is_active.is_(True)).with_for_update(read=True)
        )
        workspace_id = await session.scalar(
            select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update(read=True)
        )
        member = await session.scalar(
            select(TenantUser)
            .where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == actor.id,
                TenantUser.is_active.is_(True),
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        role = (
            None
            if member is None
            else await session.scalar(
                select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
            )
        )
    except Exception:
        raise WebResearchRefusal("permission_denied") from None

    grants = workspace_grants(role)
    if (
        user_id is None
        or workspace_id is None
        or ("source", ActionType.VIEW) not in grants
        or ("source", required_action) not in grants
        or not has_permission(actor, "source", ActionType.VIEW)
        or not has_permission(actor, "source", required_action)
    ):
        raise WebResearchRefusal("permission_denied")

    scopes = tuple(
        f"{r.key}.{a.db_value}"
        for r in RESOURCES
        if r.workspace
        for a in r.actions
        if (r.model_name, a) in grants and has_permission(actor, r.model_name, a)
    )
    return tenant_id, int(user_id), str(role), (*scopes, "public")


def _build_recommended_actions(
    preset: str,
    query: str,
    citations: list[CitationItemOut],
    *,
    is_stub: bool,
) -> list[dict[str, str]]:
    """Build convenient workflow handoff links across the web app (/app/chat, /app/documents, /app/knowledge, /app/tasks)."""
    actions: list[dict[str, str]] = []
    first_snippet_id = next((c.saved_snippet_id for c in citations if c.saved_snippet_id is not None), None)
    if first_snippet_id is not None and not is_stub:
        chat_href = "/app/chat?" + urlencode(
            {"context_type": "knowledge", "context_id": str(first_snippet_id), "boundary": "cloud"}
        )
    else:
        chat_href = "/app/chat"

    actions.append(
        {
            "key": "open_chat",
            "label": "Обсудить в чате с агентом",
            "href": chat_href,
            "surface": "chat",
        }
    )
    actions.append(
        {
            "key": "draft_document",
            "label": "Подготовить черновик документа (P19-T02)",
            "href": "/app/documents",
            "surface": "documents",
        }
    )
    actions.append(
        {
            "key": "open_knowledge",
            "label": "Открыть сохранённые фрагменты в Базе знаний",
            "href": "/app/knowledge?" + urlencode({"q": query[:80], "boundary": "cloud"}),
            "surface": "knowledge",
        }
    )
    actions.append(
        {
            "key": "delegate_task",
            "label": "Поручения агенту и задачи",
            "href": "/app/tasks",
            "surface": "tasks",
        }
    )
    if preset in RESEARCH_PRESETS:
        target_surface = RESEARCH_PRESETS[preset]["target_surface"]
        actions.sort(key=lambda item: 0 if item["href"].startswith(target_surface) else 1)
    return actions


class WebResearchService:
    """Permission- and tenant-scoped service for web search, public page fetching, and evidence snapshots."""

    def __init__(
        self,
        *,
        capability_config: ProviderCapabilityConfig | None = None,
        budget_tracker: TenantBudgetTracker | None = None,
        chromium_engine: ChromiumUnderHoodEngine | None = None,
        yandex_runner: YandexSearchRunner | None = None,
    ) -> None:
        self.capability_config = capability_config or ProviderCapabilityConfig()
        self.budget_tracker = budget_tracker or TenantBudgetTracker()
        self.chromium_engine = chromium_engine or ChromiumUnderHoodEngine()
        self.yandex_runner = yandex_runner
        self._tenant_snapshots: dict[int, list[dict[str, Any]]] = {}
        self._next_local_id: int = 1

    def _resolved_capability(
        self,
        *,
        chromium_engine: ChromiumUnderHoodEngine | None = None,
        yandex_runner: YandexSearchRunner | None = None,
    ):
        engine = chromium_engine or self.chromium_engine
        active_yandex = yandex_runner if yandex_runner is not None else self.yandex_runner
        # Injected runners are test seams, not activation approval. O-5 keeps
        # production on the stub until a separately reviewed provider/pilot.
        has_chromium_runner = False
        return resolve_search_capability(
            self.capability_config,
            has_yandex_runner=active_yandex is not None,
            has_chromium_runner=has_chromium_runner,
        )

    async def _authorize_and_consume_durable(
        self,
        tenant_id: int,
        account_key: str,
        *,
        cost: int,
        budget: WebResearchBudget | None,
        require_create: bool,
    ) -> dict[str, int | float]:
        """Commit one authorization and quota admission before any external I/O."""
        try:
            from app.core.database import async_session_maker

            async with async_session_maker() as session:
                async with session.begin():
                    await _authorize_actor_in_db(session, tenant_id, require_create=require_create)
                    return await self.budget_tracker.consume_durable(
                        session, tenant_id, account_key, cost=cost, budget=budget
                    )
        except WebResearchRefusal:
            raise
        except Exception:
            raise WebResearchRefusal("quota_state_unavailable") from None

    def status(
        self,
        *,
        tenant_id: int | None = None,
        account_key: str = "default",
        budget: WebResearchBudget | None = None,
    ) -> WebResearchStatusOut:
        tid = _resolve_tenant_id(tenant_id)
        cap = self._resolved_capability()
        budget_snap = self.budget_tracker.snapshot(tid, account_key, budget=budget)
        return WebResearchStatusOut(
            preferred_provider=cap.preferred_provider,
            default_under_hood=cap.default_under_hood,
            yandex_configured=cap.yandex_configured,
            yandex_live_enabled=cap.yandex_live_enabled,
            chromium_default_active=cap.chromium_default_active,
            is_live_capable=cap.is_live_capable,
            label=cap.label,
            note=cap.note,
            budget=budget_snap,
            presets=list(RESEARCH_PRESETS.values()),
        )

    async def status_async(
        self,
        *,
        tenant_id: int | None = None,
        account_key: str = "default",
        budget: WebResearchBudget | None = None,
        session: Any | None = None,
        require_durable: bool = False,
    ) -> WebResearchStatusOut:
        tid = _resolve_tenant_id(tenant_id)
        cap = self._resolved_capability()
        if session is None and require_durable:
            try:
                from app.core.database import async_session_maker

                async with async_session_maker() as managed_session, managed_session.begin():
                    return await self.status_async(
                        tenant_id=tid,
                        account_key=account_key,
                        budget=budget,
                        session=managed_session,
                        require_durable=True,
                    )
            except WebResearchRefusal:
                raise
            except Exception:
                raise WebResearchRefusal("quota_state_unavailable") from None

        if session is not None:
            await _authorize_actor_in_db(session, tid, require_create=False)
            budget_snap = await self.budget_tracker.snapshot_durable(session, tid, account_key, budget=budget)
        else:
            budget_snap = self.budget_tracker.snapshot(tid, account_key, budget=budget)

        return WebResearchStatusOut(
            preferred_provider=cap.preferred_provider,
            default_under_hood=cap.default_under_hood,
            yandex_configured=cap.yandex_configured,
            yandex_live_enabled=cap.yandex_live_enabled,
            chromium_default_active=cap.chromium_default_active,
            is_live_capable=cap.is_live_capable,
            label=cap.label,
            note=cap.note,
            budget=budget_snap,
            presets=list(RESEARCH_PRESETS.values()),
        )

    def list_saved_snapshots(self, *, tenant_id: int | None = None) -> list[dict[str, Any]]:
        """Return in-memory evidence snapshots for DB-exempt unit tests."""
        tid = _resolve_tenant_id(tenant_id)
        rows = self._tenant_snapshots.get(tid, [])
        return [dict(item) for item in rows]

    async def list_saved_snapshots_async(
        self,
        *,
        tenant_id: int | None = None,
        boundary: str = "cloud",
        limit: int = 20,
        session: Any | None = None,
        require_durable: bool = False,
    ) -> list[dict[str, Any]]:
        """Return durable `KnowledgeSnippet` rows from PostgreSQL with fresh tenant/actor authorization."""
        tid = _resolve_tenant_id(tenant_id)
        if boundary not in {"local_only", "company", "cloud"}:
            raise ValueError("invalid_boundary")

        if session is None and require_durable:
            try:
                from app.core.database import async_session_maker

                async with async_session_maker() as managed_session, managed_session.begin():
                    return await self.list_saved_snapshots_async(
                        tenant_id=tid,
                        boundary=boundary,
                        limit=limit,
                        session=managed_session,
                        require_durable=True,
                    )
            except WebResearchRefusal:
                raise
            except Exception:
                raise WebResearchRefusal("provider_unavailable") from None

        if session is None:
            return self.list_saved_snapshots(tenant_id=tid)

        _, _, _, allowed_scopes = await _authorize_actor_in_db(session, tid, require_create=False)
        from sqlalchemy import select

        from app.models import KnowledgeSnippet
        from app.models.managers.knowledge_manager import BOUNDARIES

        rows = (
            await session.scalars(
                select(KnowledgeSnippet)
                .where(
                    KnowledgeSnippet.tenant_id == tid,
                    KnowledgeSnippet.context_type == "knowledge",
                    KnowledgeSnippet.acl_scope.in_(allowed_scopes),
                    KnowledgeSnippet.data_boundary.in_(BOUNDARIES[boundary]),
                )
                .order_by(KnowledgeSnippet.id.desc())
                .limit(max(1, min(limit, 100)))
            )
        ).all()
        return [
            {
                "id": int(row.id),
                "tenant_id": int(row.tenant_id),
                "context_type": row.context_type,
                "title": row.title,
                "source_ref": row.source_ref,
                "citation_anchor": row.citation_anchor,
                "revision_hash": row.revision_hash,
                "excerpt": row.excerpt,
                "data_boundary": row.data_boundary,
                "extraction_limit_note": row.extraction_limit_note,
                "fetched_at": row.created_at.isoformat() if getattr(row, "created_at", None) else "",
                "prompt_injection_detected": "prompt-injection" in (row.extraction_limit_note or "").lower(),
            }
            for row in rows
        ]

    async def save_snapshot_as_evidence(
        self,
        snapshot: PageSnapshotOut,
        *,
        tenant_id: int | None = None,
        boundary: str = "cloud",
        session: Any | None = None,
        require_durable: bool = False,
    ) -> dict[str, Any]:
        """Persist a verified live page snapshot as tenant-scoped `KnowledgeSnippet`; block stub snapshots."""
        tid = _resolve_tenant_id(tenant_id)
        if snapshot.is_stub or STUB_SEARCH_MARKER in snapshot.title or STUB_SEARCH_MARKER in snapshot.excerpt:
            raise WebResearchRefusal("stub_cannot_be_live_evidence")
        if boundary not in {"local_only", "company", "cloud"}:
            raise ValueError("invalid_boundary")

        if session is None and require_durable:
            try:
                from app.core.database import async_session_maker

                async with async_session_maker() as managed_session, managed_session.begin():
                    return await self.save_snapshot_as_evidence(
                        snapshot,
                        tenant_id=tid,
                        boundary=boundary,
                        session=managed_session,
                        require_durable=True,
                    )
            except WebResearchRefusal:
                raise
            except Exception:
                raise WebResearchRefusal("provider_unavailable") from None

        if session is not None:
            await _authorize_actor_in_db(session, tid, require_create=True)
            from app.models import KnowledgeSnippet

            record = KnowledgeSnippet(
                tenant_id=tid,
                context_type="knowledge",
                title=snapshot.title[:500],
                source_ref=snapshot.canonical_url,
                citation_anchor=snapshot.citation_anchor or snapshot.canonical_url,
                data_boundary=boundary,
                acl_scope="collection.source.view",
                revision_hash=snapshot.content_hash,
                excerpt=snapshot.excerpt[:2000],
                extraction_limit_note=snapshot.extraction_limit_note,
            )
            session.add(record)
            await session.flush()
            snippet_id = getattr(record, "id", None)
            if type(snippet_id) is not int or snippet_id <= 0:
                raise WebResearchRefusal("provider_unavailable")
        else:
            snippet_id = self._next_local_id
            self._next_local_id += 1

        entry = {
            "id": int(snippet_id),
            "tenant_id": tid,
            "context_type": "knowledge",
            "title": snapshot.title,
            "source_ref": snapshot.canonical_url,
            "citation_anchor": snapshot.citation_anchor or snapshot.canonical_url,
            "revision_hash": snapshot.content_hash,
            "excerpt": snapshot.excerpt,
            "data_boundary": boundary,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "prompt_injection_detected": snapshot.prompt_injection_detected,
        }
        self._tenant_snapshots.setdefault(tid, []).insert(0, entry)
        snapshot.saved_snippet_id = int(snippet_id)
        return entry

    async def _consume_budget(
        self,
        tid: int,
        account_key: str,
        *,
        cost: int = 1,
        budget: WebResearchBudget | None = None,
        session: Any | None = None,
    ) -> dict[str, int | float]:
        if session is not None:
            return await self.budget_tracker.consume_durable(session, tid, account_key, cost=cost, budget=budget)
        return self.budget_tracker.consume(tid, account_key, cost=cost, budget=budget)

    async def fetch_page(
        self,
        data: WebFetchRequest | dict[str, Any],
        *,
        tenant_id: int | None = None,
        account_key: str = "default",
        budget: WebResearchBudget | None = None,
        policy: StaticPolicy = StaticPolicy(),
        resolver: Resolver = default_dns_resolver,
        transport: HttpTransport | None = None,
        chromium_engine: ChromiumUnderHoodEngine | None = None,
        robots_override: str | None = None,
        session: Any | None = None,
        require_durable: bool = False,
    ) -> PageSnapshotOut:
        tid = _resolve_tenant_id(tenant_id)
        req = data if isinstance(data, WebFetchRequest) else WebFetchRequest.model_validate(data)
        # Reject syntactically unsafe/private targets before charging a durable
        # quota or opening a DB transaction. The actual fetch resolves again and
        # pins the socket to that request's newly validated answer.
        validate_public_url(req.url, policy=policy, resolver=resolver)

        if require_durable and session is not None:
            # A caller-owned transaction cannot be committed by this service,
            # and must never retain locks while a page is fetched.
            raise WebResearchRefusal("quota_state_unavailable")
        if require_durable:
            await self._authorize_and_consume_durable(
                tid,
                account_key,
                cost=1,
                budget=budget,
                require_create=bool(req.save_evidence),
            )
        elif session is not None:
            await _authorize_actor_in_db(session, tid, require_create=bool(req.save_evidence))
            await self._consume_budget(tid, account_key, cost=1, budget=budget, session=session)
        else:
            await self._consume_budget(tid, account_key, cost=1, budget=budget)

        engine = chromium_engine or self.chromium_engine
        effective_mode = "http_plain" if req.fetch_mode in {"auto", "http_plain"} else "chromium_read_only"

        snapshot = await fetch_public_page(
            req.url,
            fetch_mode=effective_mode,
            max_bytes=req.max_bytes,
            timeout_seconds=req.timeout_seconds,
            policy=policy,
            resolver=resolver,
            transport=transport,
            chromium_engine=engine,
            robots_override=robots_override,
        )
        if req.save_evidence:
            await self.save_snapshot_as_evidence(
                snapshot,
                tenant_id=tid,
                boundary=req.data_boundary,
                session=session,
                require_durable=require_durable,
            )
        return snapshot

    async def _execute_search(
        self,
        query: str,
        *,
        requested_provider: SearchProviderKind | str,
        max_results: int,
        resolver: Resolver,
        chromium_engine: ChromiumUnderHoodEngine,
        yandex_runner: YandexSearchRunner | None,
    ) -> tuple[list[SearchHitOut], SearchProviderKind, bool, bool]:
        """Return `(hits, provider_used, chromium_used_under_hood, is_stub)`."""
        cap = self._resolved_capability(chromium_engine=chromium_engine, yandex_runner=yandex_runner)

        if requested_provider == "stub":
            hits = build_stub_search_hits(query, max_results=max_results, provider="stub")
            return hits, "stub", False, True

        chosen: SearchProviderKind = (
            cap.preferred_provider if requested_provider == "auto" else requested_provider  # type: ignore[assignment]
        )

        if chosen == "yandex_search_api":
            if cap.yandex_configured and cap.yandex_live_enabled and yandex_runner is not None:
                raw_hits = await yandex_runner(query, max_results)
                hits = []
                for idx, item in enumerate(raw_hits[:max_results], start=1):
                    canonical_url, _ = validate_public_url(str(item.get("url") or ""), resolver=resolver)
                    hits.append(
                        SearchHitOut(
                            rank=idx,
                            title=str(item.get("title") or canonical_url)[:240],
                            url=canonical_url,
                            snippet=str(item.get("snippet") or "")[:400],
                            provider="yandex_search_api",
                            is_stub=False,
                            stub_badge="",
                        )
                    )
                return hits, "yandex_search_api", False, False
            chosen = "chromium_headless" if getattr(chromium_engine, "_search_runner", None) is not None else "stub"

        if chosen == "chromium_headless":
            hits, is_stub = await chromium_engine.search(
                query,
                max_results=max_results,
                resolver=resolver,
            )
            if is_stub:
                return hits, "stub", False, True
            return hits, "chromium_headless", True, False

        hits = build_stub_search_hits(query, max_results=max_results, provider="stub")
        return hits, "stub", False, True

    async def research(
        self,
        data: WebSearchRequest | dict[str, Any],
        *,
        tenant_id: int | None = None,
        account_key: str = "default",
        budget: WebResearchBudget | None = None,
        policy: StaticPolicy = StaticPolicy(),
        resolver: Resolver = default_dns_resolver,
        transport: HttpTransport | None = None,
        chromium_engine: ChromiumUnderHoodEngine | None = None,
        yandex_runner: YandexSearchRunner | None = None,
        robots_override: str | None = None,
        session: Any | None = None,
        require_durable: bool = False,
    ) -> WebResearchRunOut:
        """Execute search -> public page fetch -> evidence-linked summary."""
        tid = _resolve_tenant_id(tenant_id)
        req = data if isinstance(data, WebSearchRequest) else WebSearchRequest.model_validate(data)
        cfg_budget = budget or self.budget_tracker.default_budget

        if require_durable and session is not None:
            raise WebResearchRefusal("quota_state_unavailable")
        if require_durable:
            budget_state = await self._authorize_and_consume_durable(
                tid,
                account_key,
                cost=1,
                budget=cfg_budget,
                require_create=bool(req.save_evidence),
            )
        elif session is not None:
            await _authorize_actor_in_db(session, tid, require_create=bool(req.save_evidence))
            budget_state = await self._consume_budget(tid, account_key, cost=1, budget=cfg_budget, session=session)
        else:
            budget_state = await self._consume_budget(tid, account_key, cost=1, budget=cfg_budget)
        engine = chromium_engine or self.chromium_engine
        active_yandex = yandex_runner or self.yandex_runner

        hits, provider_used, chromium_under_hood, search_is_stub = await self._execute_search(
            req.query,
            requested_provider=req.provider,
            max_results=req.max_results,
            resolver=resolver,
            chromium_engine=engine,
            yandex_runner=active_yandex,
        )

        urls_to_fetch: list[str] = list(req.target_urls[: cfg_budget.max_fetches_per_run])
        if not urls_to_fetch and not search_is_stub and req.fetch_top_n > 0:
            urls_to_fetch = [hit.url for hit in hits[: min(req.fetch_top_n, cfg_budget.max_fetches_per_run)]]

        pages: list[PageSnapshotOut] = []
        refusals: list[dict[str, str]] = []

        for target_url in urls_to_fetch:
            try:
                if require_durable:
                    budget_state = await self._authorize_and_consume_durable(
                        tid,
                        account_key,
                        cost=1,
                        budget=cfg_budget,
                        require_create=bool(req.save_evidence),
                    )
                elif session is not None:
                    budget_state = await self._consume_budget(
                        tid, account_key, cost=1, budget=cfg_budget, session=session
                    )
                else:
                    budget_state = await self._consume_budget(tid, account_key, cost=1, budget=cfg_budget)
                page = await fetch_public_page(
                    target_url,
                    fetch_mode="http_plain",
                    policy=policy,
                    resolver=resolver,
                    transport=transport,
                    chromium_engine=engine,
                    robots_override=robots_override,
                )
                if req.save_evidence and not page.is_stub:
                    await self.save_snapshot_as_evidence(
                        page,
                        tenant_id=tid,
                        boundary=req.data_boundary,
                        session=session,
                        require_durable=require_durable,
                    )
                pages.append(page)
            except WebResearchRefusal as exc:
                refusals.append(
                    {
                        "url": target_url,
                        "code": exc.code,
                        "message": exc.user_message,
                    }
                )
                if exc.code in {"budget_exhausted", "rate_limited", "quota_state_unavailable", "permission_denied"}:
                    if exc.code in {"quota_state_unavailable", "permission_denied"}:
                        raise
                    break

        overall_is_stub = search_is_stub and len(pages) == 0
        if req.save_evidence and overall_is_stub:
            raise WebResearchRefusal("stub_cannot_be_live_evidence")

        citations: list[CitationItemOut] = []
        for idx, page in enumerate(pages, start=1):
            citations.append(
                CitationItemOut(
                    index=idx,
                    title=page.title,
                    url=page.canonical_url,
                    content_hash=page.content_hash,
                    fetched_at=page.fetched_at,
                    citation_anchor=page.citation_anchor,
                    saved_snippet_id=page.saved_snippet_id,
                )
            )

        preset_meta = RESEARCH_PRESETS.get(req.preset, RESEARCH_PRESETS["general_search"])
        summary_lines: list[str] = [f"### {preset_meta['title']}: «{req.query}»"]

        if overall_is_stub:
            stub_warning = (
                f"{STUB_SEARCH_MARKER} Поиск выполнен в режиме заглушки O-5 (живой поиск и браузер P20-T05 не активированы). "
                "Результаты помечены как демонстрационные и не могут использоваться как живые доказательства."
            )
            summary_lines.append(stub_warning)
            for hit in hits:
                summary_lines.append(f"- {hit.stub_badge} **{hit.title}** ({hit.url}) — {hit.snippet}")
            citation_coverage = 0.0
        else:
            stub_warning = ""
            cited_count = 0
            for idx, page in enumerate(pages, start=1):
                inj_note = (
                    " ⚠️ [Эвристические маркеры prompt injection — контент остаётся недоверенным]"
                    if page.prompt_injection_detected
                    else ""
                )
                summary_lines.append(
                    f"- **[{idx}] {page.title}** ({page.canonical_url}, `{page.content_hash[:19]}…`){inj_note}: "
                    f"{page.excerpt[:280]}"
                )
                cited_count += 1
            citation_coverage = round(cited_count / len(pages), 4) if pages else 0.0

        if refusals:
            summary_lines.append(
                "Отклонённые адреса: " + "; ".join(f"{r['url']} ({r['code']}: {r['message']})" for r in refusals)
            )

        recommended_actions = _build_recommended_actions(
            req.preset,
            req.query,
            citations,
            is_stub=overall_is_stub,
        )

        return WebResearchRunOut(
            query=req.query,
            preset=req.preset,
            preset_title=preset_meta["title"],
            provider_used=provider_used,
            default_under_hood="chromium_headless",
            chromium_used_under_hood=chromium_under_hood,
            is_stub=overall_is_stub,
            stub_warning=stub_warning,
            search_hits=hits,
            pages=pages,
            refusals=refusals,
            summary_text="\n".join(summary_lines),
            citations=citations,
            citation_coverage=citation_coverage,
            budget=budget_state,
            recommended_actions=recommended_actions,
        )

    async def run_research_task_step(
        self,
        payload: dict[str, Any],
        *,
        tenant_id: int | None = None,
        account_key: str = "default",
        transport: HttpTransport | None = None,
        resolver: Resolver = default_dns_resolver,
        chromium_engine: ChromiumUnderHoodEngine | None = None,
        session: Any | None = None,
        require_durable: bool = True,
    ) -> dict[str, Any]:
        """Execute a web-research step inside an AgentTask workflow and return structured evidence metadata."""
        tid = _resolve_tenant_id(tenant_id)
        query = str(payload.get("query") or payload.get("goal") or "").strip()
        target_urls = list(payload.get("target_urls") or [])
        save_evidence = bool(payload.get("save_evidence", False))
        run_out = await self.research(
            {
                "query": query,
                "preset": "task_delegation",
                "target_urls": target_urls,
                "save_evidence": save_evidence,
            },
            tenant_id=tid,
            account_key=account_key,
            transport=transport,
            resolver=resolver,
            chromium_engine=chromium_engine,
            session=session,
            require_durable=require_durable,
        )
        return {
            "status": "completed",
            "query": run_out.query,
            "provider_used": run_out.provider_used,
            "chromium_used_under_hood": run_out.chromium_used_under_hood,
            "is_stub": run_out.is_stub,
            "page_count": len(run_out.pages),
            "citation_coverage": run_out.citation_coverage,
            "citations": [c.model_dump(mode="json") for c in run_out.citations],
            "refusals": run_out.refusals,
            "summary_text": run_out.summary_text,
        }


web_research_service = WebResearchService()
