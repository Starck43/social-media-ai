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

    def status(
        self,
        *,
        tenant_id: int | None = None,
        account_key: str = "default",
        budget: WebResearchBudget | None = None,
    ) -> WebResearchStatusOut:
        tid = _resolve_tenant_id(tenant_id)
        cap = resolve_search_capability(self.capability_config)
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
        """Return evidence snapshots saved in the caller's tenant only (strict tenant isolation)."""
        tid = _resolve_tenant_id(tenant_id)
        rows = self._tenant_snapshots.get(tid, [])
        return [dict(item) for item in rows]

    async def save_snapshot_as_evidence(
        self,
        snapshot: PageSnapshotOut,
        *,
        tenant_id: int | None = None,
        boundary: str = "cloud",
        session: Any | None = None,
    ) -> dict[str, Any]:
        """Persist a verified live page snapshot as tenant-scoped evidence; block stub snapshots."""
        tid = _resolve_tenant_id(tenant_id)
        if snapshot.is_stub or STUB_SEARCH_MARKER in snapshot.title or STUB_SEARCH_MARKER in snapshot.excerpt:
            raise WebResearchRefusal("stub_cannot_be_live_evidence")
        if boundary not in {"local_only", "company", "cloud"}:
            raise ValueError("invalid_boundary")

        snippet_id: int | None = None
        if session is not None:
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

        if snippet_id is None:
            snippet_id = self._next_local_id
            self._next_local_id += 1

        entry = {
            "id": snippet_id,
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
        snapshot.saved_snippet_id = snippet_id
        return entry

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
    ) -> PageSnapshotOut:
        tid = _resolve_tenant_id(tenant_id)
        req = data if isinstance(data, WebFetchRequest) else WebFetchRequest.model_validate(data)
        self.budget_tracker.consume(tid, account_key, cost=1, budget=budget)

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
        cap = resolve_search_capability(self.capability_config)

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
            chosen = "chromium_headless"

        if chosen == "chromium_headless":
            hits, is_stub = await chromium_engine.search(
                query,
                max_results=max_results,
                resolver=resolver,
            )
            return hits, "chromium_headless", True, is_stub

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
    ) -> WebResearchRunOut:
        """Execute search -> public page fetch -> evidence-linked summary."""
        tid = _resolve_tenant_id(tenant_id)
        req = data if isinstance(data, WebSearchRequest) else WebSearchRequest.model_validate(data)
        cfg_budget = budget or self.budget_tracker.default_budget

        budget_state = self.budget_tracker.consume(tid, account_key, cost=1, budget=cfg_budget)
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
                budget_state = self.budget_tracker.consume(tid, account_key, cost=1, budget=cfg_budget)
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
                if exc.code in {"budget_exhausted", "rate_limited"}:
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
                f"{STUB_SEARCH_MARKER} Поиск выполнен в режиме заглушки (O-5; движок по умолчанию: Chromium). "
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
                    " ⚠️ [Отфильтрована попытка prompt injection]" if page.prompt_injection_detected else ""
                )
                summary_lines.append(
                    f"- **[{idx}] {page.title}** ({page.canonical_url}, `{page.content_hash[:19]}…`){inj_note}: "
                    f"{page.excerpt[:280]}"
                )
                cited_count += 1
            citation_coverage = round(cited_count / len(pages), 4) if pages else 0.0

        if refusals:
            summary_lines.append(
                "Отклонённые адреса: "
                + "; ".join(f"{r['url']} ({r['code']}: {r['message']})" for r in refusals)
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
