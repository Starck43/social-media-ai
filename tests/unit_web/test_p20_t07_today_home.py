"""Unit and template tests for P20-T07 («Сегодня»: read-time attention and decisions home).

Covers all acceptance criteria in `docs/tasks/p20-t07.md` and `docs/product/WORKSPACE_UX.md#today-page`:
- Section order (`brief`, `decisions`, `today_plan`, `awaiting_reply`, `attention`, `recent_results`)
- Cross-tenant and permission filtering per provider
- Provider failure isolation (one failing provider shows its reason, never blanks the page)
- Stale / unavailable source markers (`D24`)
- Empty workspace and bounded queries (`PROVIDER_LIMIT = 5`)
- Action links to authoritative owning routes
- «Моё / Команда» switch (`scope=mine|team`, default `mine`) within existing roles
- Per-actor dismiss / snooze / seen state storing only item keys + state (`D11`, `D23/D24`)
- Start-page selection (`O-2`) in `/app/settings?tab=account` and safe fallback to `/app/` («Сегодня»)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.services.workspace.today import (
    DECISIONS_LIMIT,
    PROVIDER_LIMIT,
    TODAY_SECTION_ORDER,
    TodayProjection,
    available_start_pages,
    clear_today_state_for_tests,
    get_actor_item_states,
    get_user_start_page,
    normalize_scope,
    record_actor_item_state,
    resolve_start_page,
    set_user_start_page,
)
from app.web.dashboard import _offers, _today_projection_for_request, today_item_state

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_today_state():
    clear_today_state_for_tests()
    yield
    clear_today_state_for_tests()


class FakePerms:
    def __init__(self, allowed=(), *, chat=True, is_owner=False, workspace_role="MEMBER"):
        self.allowed = set(allowed)
        self.can_open_chat = chat
        self.is_owner = is_owner
        self.workspace_role = workspace_role

    def can(self, resource: str, action: str = "view") -> bool:
        return (resource, action) in self.allowed

    def can_manage_workspace(self, _tenant_id) -> bool:
        return self.is_owner or self.workspace_role in {"OWNER", "ADMIN", "owner", "admin"}


ALL_RIGHTS = {
    ("botaction", "view"),
    ("scheduling.action", "view"),
    ("comms.message", "view"),
    ("outboxmessage", "view"),
    ("plannertask", "view"),
    ("planner.task", "view"),
    ("agenttask", "view"),
    ("scheduling.task", "view"),
    ("source", "view"),
    ("collection.source", "view"),
    ("digestrun", "view"),
    ("notifications.digest_run", "view"),
    ("aianalytics", "view"),
    ("analysis.result", "view"),
    ("collecteditem", "view"),
    ("job", "view"),
}


class FakeTodayRepository:
    def __init__(self, **overrides):
        self.data = overrides

    async def brief(self, workspace_id: int, user_id: int, scope: str):
        val = self.data.get("brief")
        if callable(val):
            return await val(workspace_id, user_id, scope)
        return val

    async def pending_actions(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("pending_actions", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def pending_commands(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("pending_commands", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def command_decisions(self, workspace_id, user_id, perms, scope, now):
        val = self.data.get("command_decisions", [])
        if callable(val):
            return await val(workspace_id, user_id, perms, scope, now)
        return val

    async def pending_outbox(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("pending_outbox", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def planner_tasks(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("planner_tasks", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def followups(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("followups", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def upcoming_agent_tasks(self, workspace_id: int, show_sources: bool = False, limit: int = 3):
        val = self.data.get("upcoming_agent_tasks", [])
        if callable(val):
            return await val(workspace_id, show_sources, limit)
        return val

    async def unread_threads(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("unread_threads", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def personal_connections(self, user_id: int):
        val = self.data.get("personal_connections", [])
        if callable(val):
            return await val(user_id)
        return val

    async def workspace_connections(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("workspace_connections", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def digest_runs(self, workspace_id: int, limit: int = PROVIDER_LIMIT):
        val = self.data.get("digest_runs", [])
        if callable(val):
            return await val(workspace_id, limit)
        return val

    async def budget_status(self, workspace_id: int):
        val = self.data.get("budget_status")
        if callable(val):
            return await val(workspace_id)
        return val


def _command_decision(
    command_id: int,
    *,
    requester_user_id: int,
    status: str = "pending",
    title: str | None = None,
    summary: str | None = None,
    created_at: datetime | None = None,
    tenant_id: int = 7,
) -> dict[str, Any]:
    """Build an already-authorized command projection for repository-boundary tests."""
    detail_url = f"/app/commands/{command_id}"
    return {
        "key": f"command:{command_id}",
        "id": command_id,
        "tenant_id": tenant_id,
        "requester_user_id": requester_user_id,
        "title": title or f"Command #{command_id}",
        "summary": summary or "Awaiting review",
        "status": status,
        "status_label": "Требует сверки" if status == "unknown" else "На согласовании",
        "href": detail_url,
        "authoritative_url": detail_url,
        "payload_hash": "sha256:" + "a" * 64,
        "freshness": {
            "state": "degraded" if status == "unknown" else "fresh",
            "label": "Требует сверки" if status == "unknown" else "На согласовании",
            "updated_at": created_at.isoformat() if created_at else "",
        },
    }


def _render_dashboard(today_snapshot: dict, *, perms=None, recent_results=(), recent_tasks=(), **extra) -> str:
    perms = perms or FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER")
    offer = _offers(perms)
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": (
                            "<html>{% block page_actions %}{% endblock %}" "{% block content %}{% endblock %}</html>"
                        ),
                    }
                ),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(("html", "htm", "xml"), default_for_string=True),
        undefined=StrictUndefined,
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    env.globals["static_v"] = lambda path: "/static/" + path
    env.globals["url_for"] = lambda name, path="": "/static/" + path
    context = {
        "tenant": {"id": 7, "name": "Workspace 7"},
        "user": NS(id=10, username="alice", email="alice@example.com", role=None, has_admin_access=lambda: False),
        "section": "dashboard",
        "nav": [],
        "mobile_nav": [],
        "workspaces": [],
        "connection_alerts": [],
        "unread_notifications": 0,
        "flashes": [],
        "csrf": "csrf-token-123",
        "can_open_chat": perms.can_open_chat,
        "is_superuser": False,
        "tenants": [],
        "filter_tenant_id": None,
        "next_step": None,
        "offer": offer,
        "recent_results": list(recent_results),
        "recent_tasks": list(recent_tasks),
        "source_schedule_status_unknown": False,
        "pipeline": None,
        "_tz": "Europe/Moscow",
        "request": NS(url=NS(path="/app/", query=""), query_params={}),
        "perms": perms,
        "member_level": None,
        "today": today_snapshot,
    }
    context.update(extra)
    return env.get_template("web/dashboard.html").render(**context)


@pytest.mark.asyncio
async def test_today_section_order_and_empty_workspace():
    """Today sections always appear in canonical WORKSPACE_UX.md order and handle an empty workspace cleanly."""
    perms = FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER")
    proj = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=perms,
        repository=FakeTodayRepository(),
    )
    snap = await proj.snapshot(merged_results=[])

    assert tuple(snap["section_order"]) == TODAY_SECTION_ORDER
    assert [s["key"] for s in snap["sections"]] == [
        "brief",
        "decisions",
        "today_plan",
        "awaiting_reply",
        "attention",
        "recent_results",
    ]
    assert snap["by_key"]["brief"]["status"] == "not_configured"
    for key in ("decisions", "today_plan", "awaiting_reply", "attention", "recent_results"):
        assert snap["by_key"][key]["status"] == "ok"
        assert snap["by_key"][key]["items"] == []

    html = _render_dashboard(snap, perms=perms)
    offsets = [html.index(f'data-today-section="{key}"') for key in TODAY_SECTION_ORDER]
    assert offsets == sorted(offsets)


@pytest.mark.asyncio
async def test_cross_tenant_and_permission_filtering_per_provider():
    """Foreign tenant rows are excluded and unpermitted providers are denied without leaking items."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    repo = FakeTodayRepository(
        pending_actions=[
            NS(id=1, tenant_id=7, status="PENDING", capability="telegram.send", approved_user_id=10, created_at=now),
            NS(id=2, tenant_id=999, status="PENDING", capability="foreign.secret", approved_user_id=10, created_at=now),
        ],
        unread_threads=[
            NS(
                id=11,
                tenant_id=7,
                is_archived=False,
                unread_count=2,
                channel="email",
                subject_encrypted="Visible Thread",
                last_message_at=now,
                unavailable_at=None,
            ),
            NS(
                id=12,
                tenant_id=999,
                is_archived=False,
                unread_count=5,
                channel="email",
                subject_encrypted="Foreign Thread",
                last_message_at=now,
                unavailable_at=None,
            ),
        ],
        planner_tasks=[
            NS(
                id=21,
                tenant_id=7,
                owner_user_id=10,
                assignee_id=10,
                status="todo",
                due_date=date(2026, 11, 7),
                due_at=None,
                title_encrypted="My Task",
                recurrence_source="planner",
            ),
            NS(
                id=22,
                tenant_id=999,
                owner_user_id=10,
                assignee_id=10,
                status="todo",
                due_date=date(2026, 11, 7),
                due_at=None,
                title_encrypted="Foreign Planner",
                recurrence_source="planner",
            ),
        ],
    )

    # 1. With all rights, only tenant 7 rows appear
    full_proj = TodayProjection(workspace_id=7, user_id=10, perms=FakePerms(ALL_RIGHTS), repository=repo, now=now)
    full_snap = await full_proj.snapshot()
    decision_titles = [i["title"] for i in full_snap["by_key"]["decisions"]["items"]]
    assert any("telegram.send" in t for t in decision_titles)
    assert not any("foreign.secret" in t for t in decision_titles)
    reply_titles = [i["title"] for i in full_snap["by_key"]["awaiting_reply"]["items"]]
    assert reply_titles == ["Visible Thread"]
    plan_titles = [i["title"] for i in full_snap["by_key"]["today_plan"]["items"]]
    assert plan_titles == ["My Task"]

    # 2. Without permissions, sections report 'denied' and return zero items
    no_perms_proj = TodayProjection(workspace_id=7, user_id=10, perms=FakePerms(), repository=repo, now=now)
    denied_snap = await no_perms_proj.snapshot()
    for key in ("decisions", "today_plan", "awaiting_reply", "recent_results"):
        assert denied_snap["by_key"][key]["status"] == "denied"
        assert denied_snap["by_key"][key]["items"] == []


@pytest.mark.asyncio
async def test_provider_failure_isolation_never_blanks_page():
    """A failing provider marks only its section as unavailable with a reason; other sections still render."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

    async def broken_threads(_workspace_id, _limit):
        raise RuntimeError("imap connection pool exhausted")

    repo = FakeTodayRepository(
        pending_actions=[
            NS(id=5, tenant_id=7, status="PENDING", capability="telegram.send", approved_user_id=10, created_at=now),
        ],
        unread_threads=broken_threads,
    )
    proj = TodayProjection(workspace_id=7, user_id=10, perms=FakePerms(ALL_RIGHTS), repository=repo, now=now)
    snap = await proj.snapshot()

    assert snap["by_key"]["decisions"]["status"] == "ok"
    assert len(snap["by_key"]["decisions"]["items"]) == 1
    assert snap["by_key"]["awaiting_reply"]["status"] == "unavailable"
    assert "временно недоступны" in snap["by_key"]["awaiting_reply"]["reason"]

    html = _render_dashboard(snap)
    assert 'data-today-section-unavailable="awaiting_reply"' in html
    assert "Действие #5" in html


@pytest.mark.asyncio
async def test_stale_and_unavailable_markers_and_authoritative_links():
    """Stale/unavailable items surface honest markers and every item links to its owning module route."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    repo = FakeTodayRepository(
        pending_actions=[
            NS(id=101, tenant_id=7, status="PENDING", capability="telegram.send", approved_user_id=10, created_at=now),
        ],
        command_decisions=[
            _command_decision(
                202,
                requester_user_id=10,
                status="unknown",
                title="CRM deal #202",
                summary="Update CRM deal",
                created_at=now,
            )
        ],
        pending_outbox=[
            NS(id=303, tenant_id=7, author_id=10, state="draft", subject_encrypted="Draft Quote", created_at=now),
            NS(
                id=304,
                tenant_id=7,
                author_id=10,
                state="uncertain_delivery",
                subject_encrypted="Uncertain Quote",
                created_at=now,
                updated_at=now,
            ),
        ],
        planner_tasks=[
            NS(
                id=404,
                tenant_id=7,
                owner_user_id=10,
                assignee_id=10,
                status="todo",
                due_date=date(2026, 11, 6),
                due_at=None,
                title_encrypted="Overdue Call",
                recurrence_source="external_cal",
            ),
        ],
        unread_threads=[
            NS(
                id=505,
                tenant_id=7,
                is_archived=False,
                unread_count=3,
                channel="email",
                subject_encrypted="Removed Upstream Email",
                last_message_at=now,
                unavailable_at=now,
            ),
        ],
        personal_connections=[
            NS(platform="vk", state="reauth", needs_action=True, can_connect=True),
        ],
        budget_status={"tenant_id": 7, "daily_cost_limit": 10.0, "spent_usd": 10.0, "frozen": True},
    )
    perms = FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER")
    proj = TodayProjection(workspace_id=7, user_id=10, perms=perms, repository=repo, now=now)
    snap = await proj.snapshot(
        merged_results=[
            {
                "id": 606,
                "kind": "analysis",
                "href": "/app/analytics/606",
                "title": "Partial Analysis",
                "summary": "Summary text",
                "created_at": now,
                "analysis_incomplete": True,
            }
        ]
    )

    decisions = snap["by_key"]["decisions"]["items"]
    assert {d["href"] for d in decisions} == {
        "/app/actions/101",
        "/app/commands",
        "/app/communications?tab=outbox",
    }
    unknown_cmd = next(d for d in decisions if d["kind"] == "command")
    assert unknown_cmd["stale_or_unavailable"] is True
    assert "Статус не подтверждён" in unknown_cmd["marker_label"]

    plan_items = snap["by_key"]["today_plan"]["items"]
    assert plan_items[0]["href"] == "/app/planner/404/edit"
    assert plan_items[0]["stale_or_unavailable"] is True

    reply_items = snap["by_key"]["awaiting_reply"]["items"]
    assert reply_items[0]["href"] == "/app/communications?thread_id=505"
    assert reply_items[0]["stale_or_unavailable"] is True
    assert "Источник недоступен" in reply_items[0]["marker_label"]

    attention_items = snap["by_key"]["attention"]["items"]
    assert {a["href"] for a in attention_items} == {
        "/app/settings?tab=connections",
        "/app/communications?tab=outbox",
        "/app/settings?tab=llm",
    }

    html = _render_dashboard(snap, perms=perms)
    assert "data-today-stale-marker" in html
    assert 'href="/app/actions/101"' in html
    assert 'href="/app/planner/404/edit"' in html
    assert 'href="/app/communications?thread_id=505"' in html


@pytest.mark.asyncio
async def test_bounded_queries_enforce_provider_limit():
    """Each section caps visible items to PROVIDER_LIMIT (5) even when upstream returns more."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    many_threads = [
        NS(
            id=i,
            tenant_id=7,
            is_archived=False,
            unread_count=1,
            channel="telegram",
            subject_encrypted=f"Thread {i}",
            last_message_at=now,
            unavailable_at=None,
        )
        for i in range(1, 15)
    ]
    proj = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=FakePerms(ALL_RIGHTS),
        repository=FakeTodayRepository(unread_threads=many_threads),
        now=now,
    )
    snap = await proj.snapshot()
    assert len(snap["by_key"]["awaiting_reply"]["items"]) == PROVIDER_LIMIT


@pytest.mark.asyncio
async def test_decisions_section_enforces_total_limit_of_seven():
    """Each decision provider is capped at PROVIDER_LIMIT (5) and combined decisions at DECISIONS_LIMIT (7)."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    many_actions = [
        NS(id=i, tenant_id=7, status="PENDING", capability="telegram.send", approved_user_id=10, created_at=now)
        for i in range(1, 9)
    ]
    many_commands = [
        _command_decision(
            100 + i,
            requester_user_id=10,
            title=f"Cmd {i}",
            created_at=now,
        )
        for i in range(1, 9)
    ]
    many_outbox = [
        NS(id=200 + i, tenant_id=7, author_id=10, state="draft", subject_encrypted=f"Outbox {i}", created_at=now)
        for i in range(1, 9)
    ]
    proj = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=FakePerms(ALL_RIGHTS),
        repository=FakeTodayRepository(
            pending_actions=many_actions,
            command_decisions=many_commands,
            pending_outbox=many_outbox,
        ),
        now=now,
    )
    snap = await proj.snapshot()
    decision_items = snap["by_key"]["decisions"]["items"]
    assert DECISIONS_LIMIT == 7
    assert len(decision_items) == DECISIONS_LIMIT
    assert sum(1 for item in decision_items if item["kind"] == "action") == PROVIDER_LIMIT
    assert sum(1 for item in decision_items if item["kind"] == "command") == DECISIONS_LIMIT - PROVIDER_LIMIT


@pytest.mark.asyncio
async def test_mine_vs_team_scope_switch_respects_role_boundaries():
    """'mine' shows only user 10's items; 'team' widens for managers but keeps non-managers restricted in Planner."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    repo = FakeTodayRepository(
        command_decisions=[
            _command_decision(1, requester_user_id=10, title="Mine Cmd", created_at=now),
            _command_decision(2, requester_user_id=20, title="Teammate Cmd", created_at=now),
        ],
        planner_tasks=[
            NS(
                id=10,
                tenant_id=7,
                owner_user_id=10,
                assignee_id=10,
                status="todo",
                due_date=date(2026, 11, 7),
                due_at=None,
                title_encrypted="Mine Task",
                recurrence_source="planner",
            ),
            NS(
                id=20,
                tenant_id=7,
                owner_user_id=20,
                assignee_id=20,
                status="todo",
                due_date=date(2026, 11, 7),
                due_at=None,
                title_encrypted="Teammate Task",
                recurrence_source="planner",
            ),
        ],
    )

    # Mine scope (default)
    mine_proj = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER"),
        scope=normalize_scope(None),
        repository=repo,
        now=now,
    )
    mine_snap = await mine_proj.snapshot()
    assert mine_snap["scope"] == "mine"
    assert [i["id"] for i in mine_snap["by_key"]["decisions"]["items"]] == [1]
    assert [i["id"] for i in mine_snap["by_key"]["today_plan"]["items"]] == [10]

    # Team scope as OWNER: sees both Mine and Teammate tasks/commands
    team_mgr_proj = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER"),
        scope="team",
        repository=repo,
        now=now,
    )
    team_mgr_snap = await team_mgr_proj.snapshot()
    assert team_mgr_snap["scope"] == "team"
    assert [i["id"] for i in team_mgr_snap["by_key"]["decisions"]["items"]] == [1, 2]
    assert [i["id"] for i in team_mgr_snap["by_key"]["today_plan"]["items"]] == [10, 20]

    # Team scope as regular MEMBER: Planner visibility still restricts to user 10's own/assigned tasks
    team_member_proj = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=FakePerms(ALL_RIGHTS, is_owner=False, workspace_role="MEMBER"),
        scope="team",
        repository=repo,
        now=now,
    )
    team_member_snap = await team_member_proj.snapshot()
    assert [i["id"] for i in team_member_snap["by_key"]["today_plan"]["items"]] == [10]


@pytest.mark.asyncio
async def test_per_actor_dismiss_snooze_seen_stores_only_keys_and_filters_items():
    """Dismiss/snooze state stores only opaque item_key + state metadata and hides items until restored."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    session: dict = {}
    key = "decision:action:7:101"

    entry = record_actor_item_state(10, 7, key, "dismiss", session=session, now=now)
    assert entry is not None
    assert set(entry.keys()) == {"item_key", "state", "until_iso", "updated_at_iso"}
    assert entry["state"] == "dismissed"

    states = get_actor_item_states(10, 7, session=session, now=now)
    assert states[key]["state"] == "dismissed"

    repo = FakeTodayRepository(
        pending_actions=[
            NS(id=101, tenant_id=7, status="PENDING", capability="telegram.send", approved_user_id=10, created_at=now),
        ]
    )
    proj_hidden = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=FakePerms(ALL_RIGHTS),
        repository=repo,
        actor_states=states,
        include_dismissed=False,
        now=now,
    )
    snap_hidden = await proj_hidden.snapshot()
    assert snap_hidden["by_key"]["decisions"]["items"] == []
    assert snap_hidden["by_key"]["decisions"]["hidden_count"] == 1

    # With include_dismissed=True, item is shown with actor_state='dismissed'
    proj_shown = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=FakePerms(ALL_RIGHTS),
        repository=repo,
        actor_states=states,
        include_dismissed=True,
        now=now,
    )
    snap_shown = await proj_shown.snapshot()
    assert len(snap_shown["by_key"]["decisions"]["items"]) == 1
    assert snap_shown["by_key"]["decisions"]["items"][0]["actor_state"] == "dismissed"

    # Snooze expires automatically after until_iso
    snooze_key = "reply:thread:7:505"
    record_actor_item_state(10, 7, snooze_key, "snooze", snooze_minutes=30, session=session, now=now)
    assert snooze_key in get_actor_item_states(10, 7, session=session, now=now + timedelta(minutes=10))
    assert snooze_key not in get_actor_item_states(10, 7, session=session, now=now + timedelta(minutes=31))

    # Restore removes state
    record_actor_item_state(10, 7, key, "restore", session=session, now=now)
    assert key not in get_actor_item_states(10, 7, session=session, now=now)


@pytest.mark.asyncio
async def test_today_item_state_endpoint_enforces_csrf_and_auth():
    """POST /app/today/item-state validates user and CSRF before updating per-actor state."""
    import app.web.deps as deps_mod

    orig_csrf = deps_mod.ensure_csrf
    deps_mod.ensure_csrf = lambda req, tok: tok == "valid-csrf"
    try:
        form_data = {"_csrf": "valid-csrf", "item_key": "decision:action:7:9", "operation": "dismiss", "scope": "mine"}

        async def fake_form():
            return form_data

        req = NS(
            state=NS(web_user=NS(id=10, is_active=True), tenant_id=7),
            headers={"accept": "application/json"},
            session={},
            form=fake_form,
        )
        resp = await today_item_state(req)
        assert resp.status_code == 200
        assert get_actor_item_states(10, 7, session=req.session)["decision:action:7:9"]["state"] == "dismissed"

        # Bad CSRF -> 403
        form_data["_csrf"] = "bad"
        resp_bad = await today_item_state(req)
        assert resp_bad.status_code == 403
    finally:
        if orig_csrf is not None:
            deps_mod.ensure_csrf = orig_csrf


@pytest.mark.asyncio
async def test_start_page_selection_and_safe_fallback():
    """Start page defaults to 'dashboard' (/app/), redirects when permitted, and falls back to /app/ when denied."""
    session: dict = {}
    assert get_user_start_page(10, session=session) == "dashboard"

    perms_with_planner = FakePerms({("planner.task", "view")}, chat=True)
    opts = available_start_pages(perms_with_planner)
    opt_keys = [o["key"] for o in opts]
    assert "dashboard" in opt_keys
    assert "planner" in opt_keys
    assert "chat" in opt_keys
    assert "sources" not in opt_keys  # Not permitted for this actor

    # Select 'planner' as start page
    set_user_start_page(10, "planner", session=session)
    res_ok = resolve_start_page(10, perms_with_planner, session=session)
    assert res_ok == {
        "preferred_key": "planner",
        "effective_key": "planner",
        "href": "/app/planner",
        "fell_back": False,
    }

    # Default entry to /app/ redirects to /app/planner when permitted
    req_default = NS(
        state=NS(tenant_id=7, web_user=NS(id=10), web_perms=perms_with_planner, today_repository=FakeTodayRepository()),
        query_params={},
        session=session,
    )
    outcome_redirect = await _today_projection_for_request(
        req_default, 7, req_default.state.web_user, perms_with_planner, []
    )
    assert "redirect" in outcome_redirect
    assert outcome_redirect["redirect"].headers["location"] == "/app/planner"

    # Explicit ?view=today stays on «Сегодня» even when start_page is 'planner'
    req_explicit = NS(
        state=NS(tenant_id=7, web_user=NS(id=10), web_perms=perms_with_planner, today_repository=FakeTodayRepository()),
        query_params={"view": "today"},
        session=session,
    )
    outcome_explicit = await _today_projection_for_request(
        req_explicit, 7, req_explicit.state.web_user, perms_with_planner, []
    )
    assert "today" in outcome_explicit

    # When switching to a workspace where 'planner' is NOT permitted, resolve_start_page safely falls back to /app/
    perms_no_planner = FakePerms(set(), chat=False)
    res_fallback = resolve_start_page(10, perms_no_planner, session=session)
    assert res_fallback == {
        "preferred_key": "planner",
        "effective_key": "dashboard",
        "href": "/app/",
        "fell_back": True,
    }
    req_fallback = NS(
        state=NS(tenant_id=8, web_user=NS(id=10), web_perms=perms_no_planner, today_repository=FakeTodayRepository()),
        query_params={},
        session=session,
    )
    outcome_fallback = await _today_projection_for_request(
        req_fallback, 8, req_fallback.state.web_user, perms_no_planner, []
    )
    assert "redirect" not in outcome_fallback
    assert outcome_fallback["today"]["start_page"]["fell_back"] is True

    html = _render_dashboard(outcome_fallback["today"], perms=perms_no_planner)
    assert "data-start-page-fallback" in html
    assert "открыта страница «Сегодня»" in html

    # Invalid start page key raises ValueError
    with pytest.raises(ValueError):
        set_user_start_page(10, "nonexistent_page", session=session)
    with pytest.raises(ValueError):
        set_user_start_page(10, "settings", session=session)


@pytest.mark.asyncio
async def test_brief_followups_upcoming_tasks_workspace_conns_and_digest_attention():
    """Cover brief item rendering, followups, upcoming agent tasks, workspace connections, and digest deliveries."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    repo = FakeTodayRepository(
        brief={
            "id": "b-1",
            "tenant_id": 7,
            "title": "Утренний бриф за 07.11",
            "summary": "3 решения, 2 письма ждут ответа",
            "href": "/app/digests",
            "created_at": now,
            "stale_or_unavailable": False,
        },
        followups=[
            NS(
                id=51,
                tenant_id=7,
                data_boundary="cloud",
                title="Перезвонить клиенту",
                excerpt="До пятницы",
                extraction_limit_note="Неточная дата требует подтверждения",
                created_at=now,
            ),
        ],
        upcoming_agent_tasks=[
            {"id": 61, "tenant_id": 7, "name": "Ежедневный сбор ВК", "is_due": True, "next_run_at": now},
            {
                "id": 62,
                "tenant_id": 7,
                "name": "Дайджест Telegram",
                "is_due": False,
                "next_run_at": now + timedelta(hours=2),
            },
        ],
        workspace_connections=[
            NS(
                id=71,
                tenant_id=7,
                state="reauth_required",
                last_error_code="AUTH_FAILED",
                display_name="Корпоративная почта",
                updated_at=now,
            ),
            NS(
                id=72,
                tenant_id=7,
                state="verified",
                last_error_code=None,
                display_name="Здоровое подключение",
                updated_at=now,
            ),
        ],
        digest_runs=[
            NS(id=81, tenant_id=7, delivery_state=None, status="failed", created_at=now),
            NS(id=82, tenant_id=7, delivery_state={"broken": True}, status="sent", created_at=now),
            NS(id=83, tenant_id=7, delivery_state=None, status="sent", created_at=now),
        ],
    )
    perms = FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER")
    proj = TodayProjection(workspace_id=7, user_id=10, perms=perms, repository=repo, now=now)
    snap = await proj.snapshot()

    assert snap["by_key"]["brief"]["status"] == "ok"
    assert snap["by_key"]["brief"]["items"][0]["title"] == "Утренний бриф за 07.11"

    plan_kinds = [i["kind"] for i in snap["by_key"]["today_plan"]["items"]]
    assert "followup" in plan_kinds
    assert "agent_task" in plan_kinds

    att_items = snap["by_key"]["attention"]["items"]
    att_kinds = [i["kind"] for i in att_items]
    assert "workspace_connection" in att_kinds
    assert "digest_delivery" in att_kinds
    assert not any("Здоровое подключение" in i["title"] for i in att_items)


@pytest.mark.asyncio
async def test_partial_provider_errors_keep_surviving_items_in_same_section():
    """If one sub-provider in decisions/today_plan/attention fails, surviving sub-providers still return items."""
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)

    async def fail_Async(*_args, **_kwargs):
        raise RuntimeError("sub-provider error")

    repo = FakeTodayRepository(
        brief=fail_Async,
        pending_actions=fail_Async,
        command_decisions=[
            _command_decision(1, requester_user_id=10, title="Surviving Cmd", created_at=now),
        ],
        pending_outbox=fail_Async,
        planner_tasks=fail_Async,
        followups=[
            NS(
                id=2,
                tenant_id=7,
                data_boundary="cloud",
                title="Surviving Followup",
                excerpt="ok",
                extraction_limit_note="",
                created_at=now,
            ),
        ],
        upcoming_agent_tasks=fail_Async,
        personal_connections=fail_Async,
        workspace_connections=fail_Async,
        digest_runs=fail_Async,
        budget_status={"tenant_id": 7, "daily_cost_limit": 5.0, "spent_usd": 4.5, "frozen": False},
    )
    perms = FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER")
    proj = TodayProjection(workspace_id=7, user_id=10, perms=perms, repository=repo, now=now)
    snap = await proj.snapshot()

    assert snap["by_key"]["brief"]["status"] == "unavailable"
    assert len(snap["by_key"]["decisions"]["items"]) == 1
    assert "Часть источников решений временно недоступна" in snap["by_key"]["decisions"]["reason"]
    assert len(snap["by_key"]["today_plan"]["items"]) == 1
    assert "Часть элементов плана временно недоступна" in snap["by_key"]["today_plan"]["reason"]
    assert len(snap["by_key"]["attention"]["items"]) == 1
    assert "Часть сигналов внимания временно недоступна" in snap["by_key"]["attention"]["reason"]


@pytest.mark.asyncio
async def test_settings_account_start_page_template_and_route():
    """Verify /app/settings?tab=account renders the start-page selector and POST /app/settings/start-page updates it."""
    import app.web.settings as settings_mod

    orig_csrf = settings_mod.ensure_csrf
    settings_mod.ensure_csrf = lambda req, tok: tok == "valid-csrf"
    try:
        req = NS(
            state=NS(web_user=NS(id=10, username="alice", is_active=True), tenant_id=7),
            session={},
        )
        resp = await settings_mod.start_page_update(req, start_page="communications", token="valid-csrf")
        assert resp.status_code == 303
        assert get_user_start_page(10, session=req.session) == "communications"

        # Invalid start page key redirects with error flash and leaves previous value intact
        resp_bad = await settings_mod.start_page_update(req, start_page="settings", token="valid-csrf")
        assert resp_bad.status_code == 302
        assert get_user_start_page(10, session=req.session) == "communications"
    finally:
        settings_mod.ensure_csrf = orig_csrf

    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": "<html>{% block content %}{% endblock %}</html>",
                    }
                ),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        autoescape=select_autoescape(("html", "htm", "xml"), default_for_string=True),
    )
    env.filters["ui_label"] = str
    env.globals["static_v"] = lambda path: "/static/" + path
    env.globals["url_for"] = lambda name, **kw: "/static/" + kw.get("path", "")
    perms = FakePerms(ALL_RIGHTS, chat=True)
    html = env.get_template("web/settings.html").render(
        tab="account",
        visible_tabs=["account", "connections"],
        user=NS(id=10, username="alice", email="alice@example.com", has_admin_access=lambda: False),
        tenant=NS(id=7, name="WS 7"),
        perms=perms,
        csrf="csrf-123",
        start_page_options=available_start_pages(perms),
        current_start_page="communications",
    )
    assert "data-settings-start-page" in html
    assert 'action="/app/settings/start-page"' in html
    assert '<option value="communications" selected>' in html
