"""P20-T20: one action card and a full-width, evidence-backed daily brief."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest
from test_p20_t07_today_home import ALL_RIGHTS, FakePerms, FakeTodayRepository, _render_dashboard

from app.services.workspace.today import TODAY_SECTION_ORDER, TodayProjection, clear_today_state_for_tests


@pytest.fixture(autouse=True)
def _reset_today_state():
    clear_today_state_for_tests()
    yield
    clear_today_state_for_tests()


def _fixture_repo() -> FakeTodayRepository:
    now = datetime(2026, 11, 22, 8, 45, tzinfo=timezone.utc)
    return FakeTodayRepository(
        brief={
            "tenant_id": 7,
            "id": "2026-11-22",
            "title": "Сводка дня",
            "summary": "Доступных результатов: 3",
            "social_results": 3,
            "social_top": [{"title": "Отзывы о меню", "category": "needs_attention", "reason": "рост упоминаний"}],
            "window": "2026-11-21 — 2026-11-22",
            "notices": ["Охват ограничен сохранёнными результатами."],
            "href": "/app/digests/morning",
            "created_at": now,
            "marker_label": "Ограниченный охват",
        },
        pending_actions=[
            NS(
                id=31,
                tenant_id=7,
                status="PENDING",
                capability="telegram.send",
                approved_user_id=10,
                created_at=now,
            )
        ],
        unread_threads=[
            NS(
                id=44,
                tenant_id=7,
                is_archived=False,
                unread_count=2,
                channel="email",
                subject_encrypted="Когда будет дайджест?",
                last_message_at=now,
                unavailable_at=None,
            )
        ],
        planner_tasks=[
            NS(
                id=55,
                tenant_id=7,
                owner_user_id=10,
                assignee_id=10,
                status="todo",
                due_date=date(2026, 11, 22),
                due_at=None,
                title_encrypted="Проверить цифры",
                recurrence_source="planner",
            )
        ],
    )


@pytest.mark.asyncio
async def test_today_uses_one_action_section_with_per_item_actions():
    perms = FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER")
    projection = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=perms,
        repository=_fixture_repo(),
        now=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
    )
    snap = await projection.snapshot(merged_results=[])

    assert (
        tuple(snap["section_order"])
        == TODAY_SECTION_ORDER
        == ("brief", "needs_action", "today_plan", "attention", "recent_results")
    )
    assert [section["key"] for section in snap["sections"]] == list(TODAY_SECTION_ORDER)
    actions = snap["by_key"]["needs_action"]["items"]
    kinds = {item["item_key"]: item["action_kind"] for item in actions}
    assert kinds["decision:action:7:31"] == "decision"
    assert kinds["reply:thread:7:44"] == "reply"
    assert all(item["action_label"] in {"Решить", "Ответить"} for item in actions)
    # Legacy read-only aliases stay consistent for callers that still read by_key.
    assert [item["title"] for item in snap["by_key"]["decisions"]["items"]][0].startswith("Действие #31")
    assert [item["title"] for item in snap["by_key"]["awaiting_reply"]["items"]] == ["Когда будет дайджест?"]

    html = _render_dashboard(snap, perms=perms)
    assert html.index('data-today-section="brief"') < html.index('data-today-section="needs_action"')
    assert 'data-today-section="decisions"' not in html
    assert 'data-today-section="awaiting_reply"' not in html
    assert ">Решить</span>" in html
    assert ">Ответить</span>" in html
    assert html.count('class="ui-button ui-button-small ui-button-quiet"') >= 4


@pytest.mark.asyncio
async def test_today_brief_is_full_width_and_summarizes_existing_sections():
    perms = FakePerms(ALL_RIGHTS, is_owner=True, workspace_role="OWNER")
    projection = TodayProjection(
        workspace_id=7,
        user_id=10,
        perms=perms,
        repository=_fixture_repo(),
        now=datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc),
    )
    snap = await projection.snapshot(merged_results=[])

    brief = snap["by_key"]["brief"]["brief"]
    assert brief["status"] == "ok"
    text = " ".join(line["text"] for line in brief["lines"])
    assert "2026-11-21 — 2026-11-22" in text
    assert "Отзывы о меню" in text
    assert "В плане на сегодня: 1 задач" in text
    assert "Требуют решения: 1. Требуют ответа: 1." in text
    assert brief["coverage"]

    html = _render_dashboard(snap, perms=perms)
    assert 'class="ui-card today-brief-card"' in html
    assert "today-brief-lines" in html
    assert "Обсудить в чате" in html
