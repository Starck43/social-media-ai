"""P16-T02 API, web form and scheduled-job boundary checks; no DB/provider calls."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.services.planner.reminder_contracts import ReminderOut

__source_only__ = True


def reminder(**changes):
    values = {
        "id": 9,
        "tenant_id": 7,
        "task_id": 3,
        "recipient_user_id": 5,
        "channel": "inbox",
        "preset": "day",
        "remind_at": datetime(2026, 11, 10, tzinfo=timezone.utc),
        "snoozed_until": None,
        "next_attempt_at": datetime(2026, 11, 10, tzinfo=timezone.utc),
        "state": "pending",
        "sent_at": None,
        "version": 1,
        "generation": 0,
        "notification_id": None,
        "last_error": None,
        "late": False,
    }
    values.update(changes)
    return ReminderOut(**values)


async def test_api_reminder_routes_are_typed_and_delegate_to_service(monkeypatch):
    from app.api.v1.conventions import validate_business_routes
    from app.api.v1.endpoints import planner as api

    validate_business_routes(api.router)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = lambda: None
    expected = reminder()
    monkeypatch.setattr(api.reminder_service, "list", AsyncMock(return_value=[expected]))
    monkeypatch.setattr(api.reminder_service, "create", AsyncMock(return_value=expected))
    monkeypatch.setattr(api.reminder_service, "snooze", AsyncMock(return_value=expected))
    monkeypatch.setattr(api.reminder_service, "reschedule", AsyncMock(return_value=expected))
    monkeypatch.setattr(api.reminder_service, "cancel", AsyncMock(return_value=expected))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid") as client:
        assert (await client.get("/api/v1/planner/tasks/3/reminders")).json()[0]["task_id"] == 3
        created = await client.post("/api/v1/planner/tasks/3/reminders", json={"preset": "day", "channel": "inbox"})
        assert created.status_code == 201 and created.json()["id"] == 9
        assert (
            await client.post("/api/v1/planner/reminders/9/snooze", json={"expected_version": 1, "preset": "week"})
        ).status_code == 200
        assert (
            await client.post(
                "/api/v1/planner/reminders/9/reschedule",
                json={"expected_version": 1, "remind_at": "2026-11-12T09:00:00+03:00"},
            )
        ).status_code == 200
        assert (
            await client.post("/api/v1/planner/reminders/9/cancel", json={"expected_version": 1})
        ).status_code == 200
        invalid = await client.post("/api/v1/planner/tasks/3/reminders", json={"preset": "custom"})
        assert invalid.status_code == 422
    api.reminder_service.create.assert_awaited_once()
    api.reminder_service.snooze.assert_awaited_once_with(9, api.ReminderSnooze(expected_version=1, preset="week"))


async def test_web_reminder_forms_enforce_csrf_and_version_then_redirect(monkeypatch):
    from app.web import planner as web

    app = FastAPI()
    app.include_router(web.router, prefix="/app")
    app.state.csrf_manager = SimpleNamespace(verify_token=lambda value: value == "synthetic-csrf")
    monkeypatch.setattr(web, "get_current_user", lambda: SimpleNamespace(id=5))
    monkeypatch.setattr(web, "has_permission", lambda *args: True)
    monkeypatch.setattr(web.reminder_service, "create", AsyncMock(return_value=reminder()))
    monkeypatch.setattr(web.reminder_service, "snooze", AsyncMock(return_value=reminder()))
    monkeypatch.setattr(web.reminder_service, "reschedule", AsyncMock(return_value=reminder()))
    monkeypatch.setattr(web.reminder_service, "cancel", AsyncMock(return_value=reminder()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid") as client:
        assert (
            await client.post("/app/planner-reminders/3/create", data={"preset": "day", "_csrf": "bad"})
        ).status_code == 403
        valid = {"_csrf": "synthetic-csrf", "expected_version": "1"}
        assert (
            await client.post(
                "/app/planner-reminders/3/create",
                data={"_csrf": "synthetic-csrf", "preset": "custom", "remind_at": "", "channel": "inbox"},
            )
        ).status_code == 422
        response = await client.post(
            "/app/planner-reminders/3/create",
            data={"_csrf": "synthetic-csrf", "preset": "day", "remind_at": "", "channel": "telegram"},
        )
        assert response.status_code == 303 and response.headers["location"].endswith("/3/edit#reminders")
        assert (
            await client.post("/app/planner-reminders/9/snooze", data={**valid, "preset": "hour"})
        ).status_code == 303
        assert (
            await client.post(
                "/app/planner-reminders/9/reschedule",
                data={**valid, "remind_at": "2026-11-12T09:00:00+03:00"},
            )
        ).status_code == 303
        assert (await client.post("/app/planner-reminders/9/cancel", data=valid)).status_code == 303
        duplicate = await client.post(
            "/app/planner-reminders/3/create",
            content="_csrf=synthetic-csrf&preset=day&preset=week&remind_at=&channel=inbox",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert duplicate.status_code == 422


async def test_scheduled_handler_runs_due_then_pending_channel_delivery(monkeypatch):
    from app.jobs.job_handlers import planner_reminders

    monkeypatch.setattr(planner_reminders.reminder_service, "dispatch_due", AsyncMock(return_value={"sent": 1}))
    monkeypatch.setattr(
        planner_reminders.reminder_notifications,
        "dispatch_pending",
        AsyncMock(return_value={"sent": 1, "blocked": 0, "unknown": 0, "deferred": 0}),
    )
    result = await planner_reminders.handle_planner_reminders({"limit": 7})
    assert result == {
        "reminders": {"sent": 1},
        "deliveries": {"sent": 1, "blocked": 0, "unknown": 0, "deferred": 0},
    }
    assert planner_reminders.reminder_service.dispatch_due.await_args.kwargs == {"limit": 7}
    assert planner_reminders.reminder_notifications.dispatch_pending.await_args.kwargs == {"limit": 7}
    with pytest.raises(ValueError):
        await planner_reminders.handle_planner_reminders({"limit": True})


def test_dispatch_job_is_bootstrapped_each_minute():
    from app.tasks.bootstrap import DEFAULT_TASKS

    assert DEFAULT_TASKS["planner-reminders-dispatch"] == ("* * * * *", "planner.reminders.dispatch", {})


def test_merged_planner_template_keeps_event_forms_and_reminder_controls_safe():
    from bs4 import BeautifulSoup

    from app.web.deps import templates
    from app.web.planner import DELIVERY_ERROR_LABELS, PRIORITY_LABELS, REMINDER_ERROR_LABELS, STATUS_LABELS

    task = SimpleNamespace(
        id=3,
        title="Synthetic",
        description="",
        version=1,
        priority="medium",
        assignee_id=None,
        parent_id=None,
        contact_id=None,
        status="todo",
        category="",
        tags=[],
        due_at=None,
        due_date=None,
        timezone="UTC",
        recurrence=None,
        series_id=3,
    )
    item = reminder(state="delivered", delivery_state="blocked", delivery_error_code="channel_not_implemented")
    html = templates.env.get_template("web/planner.html").render(
        request=SimpleNamespace(query_params={}),
        user=None,
        tenant=None,
        perms=None,
        csrf="synthetic-csrf",
        section="planner",
        view="list",
        editing=task,
        items=[task],
        status_labels=STATUS_LABELS,
        priority_labels=PRIORITY_LABELS,
        can_create=False,
        can_update=True,
        can_remind=True,
        can_delete=False,
        can_draft_greeting=True,
        event_settings=SimpleNamespace(can_configure=True, enabled=True),
        greeting_ids={3},
        reminders=[item],
        reminder_error_labels=REMINDER_ERROR_LABELS,
        delivery_error_labels=DELIVERY_ERROR_LABELS,
        next_before_id=None,
        url_for=lambda name, **kwargs: "/static/" + kwargs.get("path", ""),
        static_v=lambda path: "/static/" + path,
    )
    soup = BeautifulSoup(html, "html.parser")
    assert "blocked" not in soup.text and "channel_not_implemented" not in soup.text
    assert "заблокировано" in soup.text and "Канал пока недоступен" in soup.text
    assert soup.select_one('form[action="/app/planner/events/settings"]')
    assert soup.select_one('form[action="/app/planner/events/3/draft"]')
    forms = soup.select('#reminders form[method="post"]')
    assert len(forms) == 4
    assert all(form.select_one('input[name="_csrf"]')["value"] == "synthetic-csrf" for form in forms)
    assert all(form.select_one('input[name="expected_version"]')["value"] == "1" for form in forms[1:])
    all_forms = soup.select('form[method="post"]')
    assert all(form.select_one('input[name="_csrf"]')["value"] == "synthetic-csrf" for form in all_forms)
