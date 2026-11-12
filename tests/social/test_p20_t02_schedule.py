from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.social import brief_schedule

__source_only__ = True

UTC = timezone.utc


def test_configure_validates_local_time_zone_and_window():
    schedule = brief_schedule.configure(12, 34, local_time="07:05", timezone_name="Europe/Kirov", days=7)

    assert schedule.cron_expr == "5 7 * * *"
    assert schedule.as_payload() == {
        "kind": "social_morning_brief",
        "user_id": 34,
        "local_time": "07:05",
        "timezone": "Europe/Kirov",
        "days": 7,
        "channel": "inbox",
        "notify_confirmed": False,
    }
    for kwargs in (
        {"local_time": "7:05", "timezone_name": "UTC", "days": 1},
        {"local_time": "24:00", "timezone_name": "UTC", "days": 1},
        {"local_time": "07:00", "timezone_name": "Not/AZone", "days": 1},
        {"local_time": "07:00", "timezone_name": "UTC", "days": 0},
        {"local_time": "07:00", "timezone_name": "UTC", "days": True},
        {"local_time": "07:00", "timezone_name": "UTC", "days": 1, "channel": "unsupported"},
        {"local_time": "07:00", "timezone_name": "UTC", "days": 1, "channel": "telegram"},
    ):
        with pytest.raises(ValueError):
            brief_schedule.configure(12, 34, **kwargs)


def test_next_occurrence_uses_configured_timezone_across_dst():
    schedule = brief_schedule.configure(12, 34, local_time="09:00", timezone_name="America/New_York", days=1)

    before_transition = datetime(2026, 3, 7, 14, 0, tzinfo=UTC)
    after_transition = datetime(2026, 3, 8, 14, 0, tzinfo=UTC)

    assert brief_schedule.next_occurrence(schedule, after=before_transition) == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)
    assert brief_schedule.next_occurrence(schedule, after=after_transition) == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="aware_time_required"):
        brief_schedule.next_occurrence(schedule, after=datetime(2026, 3, 7, 14, 0))


def test_signed_config_rejects_tampering_and_cross_task_or_tenant(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr(brief_schedule, "require_configuring_actor", lambda schedule: None)
    schedule = brief_schedule.configure(12, 34, local_time="07:05", timezone_name="Europe/Kirov", days=3)
    signed = schedule.signed_payload(56)

    assert brief_schedule.MorningBriefSchedule.from_signed_payload(12, 56, signed) == schedule
    for tenant_id, task_id, payload in (
        (13, 56, signed),
        (12, 57, signed),
        (12, 56, {**signed, "user_id": 99}),
        (12, 56, {**signed, "signed_config": signed["signed_config"] + "tampered"}),
    ):
        with pytest.raises(ValueError, match="brief_schedule_config_invalid"):
            brief_schedule.MorningBriefSchedule.from_signed_payload(tenant_id, task_id, payload)


def test_signed_occurrence_rejects_timestamp_tampering(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SECRET_KEY", "unit-test-secret")
    scheduled_for = datetime(2026, 11, 11, 2, 5, tzinfo=UTC)
    token = brief_schedule.occurrence_token(12, 56, "config-token", scheduled_for)
    assert brief_schedule.verify_occurrence_token(
        token,
        tenant_id=12,
        task_id=56,
        config_token="config-token",
        scheduled_for=scheduled_for,
    )
    assert not brief_schedule.verify_occurrence_token(
        token,
        tenant_id=12,
        task_id=56,
        config_token="config-token",
        scheduled_for=scheduled_for + timedelta(days=1),
    )
    assert not brief_schedule.verify_occurrence_token(
        token,
        tenant_id=12,
        task_id=57,
        config_token="config-token",
        scheduled_for=scheduled_for,
    )


@pytest.mark.asyncio
async def test_handler_rejects_edited_occurrence_before_publish(monkeypatch):
    from app.jobs.job_handlers import social_brief

    monkeypatch.setattr("app.core.config.settings.SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr(brief_schedule, "require_configuring_actor", lambda schedule: None)
    monkeypatch.setattr(social_brief, "current_tenant_id", lambda: 12)
    monkeypatch.setattr(social_brief, "is_bypass", lambda: False)
    monkeypatch.setattr(social_brief, "_recipient", lambda *args: None)

    schedule = brief_schedule.configure(12, 34, local_time="07:05", timezone_name="Europe/Kirov", days=1)
    signed = schedule.signed_payload(56)
    task = SimpleNamespace(
        tenant_id=12,
        id=56,
        job_type="social.brief",
        cron_expr=schedule.cron_expr,
        payload=signed,
        is_active=True,
    )
    scheduled_for = datetime(2026, 11, 11, 2, 5, tzinfo=UTC)
    payload = {
        **signed,
        "agent_task_id": 56,
        "scheduled_for": (scheduled_for + timedelta(days=30)).isoformat(),
        "occurrence_token": brief_schedule.occurrence_token(12, 56, signed["signed_config"], scheduled_for),
    }
    published = []

    async def publish(*args, **kwargs):
        published.append((args, kwargs))

    monkeypatch.setattr(social_brief, "publish_dispatch", publish)
    result = await social_brief.handle_social_brief(payload, load_task=lambda task_id: _return(task))

    assert result == {"status": "skipped", "reason": "occurrence_invalid"}
    assert published == []


async def _return(value):
    return value


def test_schedule_for_task_requires_signed_config_and_matching_cron(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr(brief_schedule, "require_configuring_actor", lambda schedule: None)
    schedule = brief_schedule.configure(12, 34, local_time="07:05", timezone_name="Europe/Kirov", days=3)
    task = SimpleNamespace(
        tenant_id=12,
        id=56,
        job_type="social.brief",
        cron_expr=schedule.cron_expr,
        payload=schedule.signed_payload(56),
    )

    assert brief_schedule.schedule_for_task(task) == schedule
    task.cron_expr = "0 8 * * *"
    with pytest.raises(ValueError, match="brief_schedule_cron_mismatch"):
        brief_schedule.schedule_for_task(task)
    task.job_type = "digest"
    with pytest.raises(ValueError, match="brief_schedule_task_type_invalid"):
        brief_schedule.schedule_for_task(task)


def test_require_configuring_actor_requires_recipient_identity_and_rights(monkeypatch):
    schedule = brief_schedule.configure(12, 34, local_time="07:05", timezone_name="Europe/Kirov", days=3)
    actor = SimpleNamespace(id=34)
    monkeypatch.setattr("app.core.permissions.get_current_user", lambda: actor)
    monkeypatch.setattr("app.core.tenant_context.current_tenant_id", lambda: 12)
    monkeypatch.setattr("app.core.tenant_context.is_bypass", lambda: False)
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *args: True)

    brief_schedule.require_configuring_actor(schedule)

    monkeypatch.setattr("app.core.permissions.get_current_user", lambda: SimpleNamespace(id=99))
    with pytest.raises(PermissionError, match="social_brief_schedule_denied"):
        brief_schedule.require_configuring_actor(schedule)


@pytest.mark.asyncio
async def test_delivery_window_delegates_quiet_hours_policy(monkeypatch):
    expected = (datetime(2026, 11, 11, 5, 0, tzinfo=UTC), "quiet_hours")
    calls = []

    async def allowed_at(session, user_id, timezone_name, now):
        calls.append((session, user_id, timezone_name, now))
        return expected

    monkeypatch.setattr("app.services.notifications.reminders.reminder_notifications.allowed_at", allowed_at)
    session = object()
    now = datetime(2026, 11, 11, 2, 0, tzinfo=UTC)

    assert await brief_schedule.delivery_window(session, 34, "Europe/Kirov", now=now) == expected
    assert calls == [(session, 34, "Europe/Kirov", now)]


@pytest.mark.asyncio
async def test_prepare_dispatch_pins_window_and_emits_only_generic_link(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr("app.core.config.settings.PUBLIC_BASE_URL", "")
    monkeypatch.setattr(brief_schedule, "require_configuring_actor", lambda schedule: None)
    schedule = brief_schedule.configure(12, 34, local_time="07:05", timezone_name="Europe/Kirov", days=3)
    task_id = 56
    signed = schedule.signed_payload(task_id)
    scheduled_for = datetime(2026, 11, 11, 2, 5, tzinfo=UTC)
    brief = SimpleNamespace(
        start=date(2026, 11, 9),
        end=date(2026, 11, 11),
        generated_at=scheduled_for,
    )
    calls = []

    async def preview(tenant_id, user_id, *, days, now, timezone_name):
        calls.append((tenant_id, user_id, days, now, timezone_name))
        return brief

    monkeypatch.setattr("app.services.social.morning_brief.preview", preview)

    first = await brief_schedule.prepare_dispatch(
        schedule,
        task_id=task_id,
        task_config_token=signed["signed_config"],
        scheduled_for=scheduled_for,
    )
    retry = await brief_schedule.prepare_dispatch(
        schedule,
        task_id=task_id,
        task_config_token=signed["signed_config"],
        scheduled_for=scheduled_for,
    )

    assert first == retry
    assert first["dedup_key"] == brief_schedule.idempotency_key(schedule, start=brief.start, end=brief.end)
    parsed = urlsplit(first["link"])
    assert parsed.path == "/app/digests/morning"
    query = parse_qs(parsed.query)
    assert query["tz"] == ["Europe/Kirov"]
    assert query["days"] == ["3"]
    assert query["start"] == ["2026-11-09"]
    assert query["end"] == ["2026-11-11"]
    assert len(query["brief_token"]) == 1
    assert first["message"] == f"Откройте обзор: {first['link']}"
    assert set(first) == {"title", "message", "link", "external_link_available", "dedup_key", "generated_at"}
    verified = brief_schedule.verify_report_token(
        query["brief_token"][0],
        task=SimpleNamespace(
            tenant_id=12,
            id=task_id,
            job_type="social.brief",
            cron_expr=schedule.cron_expr,
            payload=signed,
        ),
        user_id=34,
    )
    assert verified == (brief.start, brief.end)
    task = SimpleNamespace(
        tenant_id=12,
        id=task_id,
        job_type="social.brief",
        cron_expr=schedule.cron_expr,
        payload=signed,
    )
    event = SimpleNamespace(message=first["message"])
    assert brief_schedule.report_window_from_message(event, task=task, user_id=34) == (
        brief.start,
        brief.end,
    )
    event.message += "tampered"
    assert brief_schedule.report_window_from_message(event, task=task, user_id=34) is None
    assert calls == [(12, 34, 3, scheduled_for, "Europe/Kirov")] * 2


@pytest.mark.asyncio
async def test_prepare_dispatch_propagates_preview_authorization_failure(monkeypatch):
    schedule = brief_schedule.configure(12, 34, local_time="07:05", timezone_name="Europe/Kirov", days=1)

    async def denied(*args, **kwargs):
        raise PermissionError("social_brief_denied")

    monkeypatch.setattr("app.services.social.morning_brief.preview", denied)

    with pytest.raises(PermissionError, match="social_brief_denied"):
        await brief_schedule.prepare_dispatch(
            schedule,
            task_id=56,
            task_config_token="signed-config",
            scheduled_for=datetime(2026, 11, 11, 2, 5, tzinfo=UTC),
        )


def test_telegram_link_requires_valid_public_base_url(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.PUBLIC_BASE_URL", "")
    path = "/app/digests/morning?tz=Europe%2FKirov"
    assert brief_schedule._public_url(path) is None

    monkeypatch.setattr("app.core.config.settings.PUBLIC_BASE_URL", "https://example.test/")
    assert brief_schedule._public_url(path) == f"https://example.test{path}"

    monkeypatch.setattr("app.core.config.settings.PUBLIC_BASE_URL", "https://example.test/app")
    assert brief_schedule._public_url(path) is None

    monkeypatch.setattr(
        "app.core.config.settings.PUBLIC_BASE_URL",
        "https://user:pass@example.test",  # pragma: allowlist secret -- synthetic invalid URL fixture
    )
    assert brief_schedule._public_url(path) is None


@pytest.mark.asyncio
async def test_telegram_message_is_absolute_only_with_valid_public_base(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr("app.core.config.settings.PUBLIC_BASE_URL", "")
    monkeypatch.setattr(brief_schedule, "require_configuring_actor", lambda schedule: None)
    schedule = brief_schedule.configure(
        12,
        34,
        local_time="07:05",
        timezone_name="Europe/Kirov",
        days=1,
        channel="telegram",
        notify_confirmed=True,
    )
    scheduled_for = datetime(2026, 11, 11, 2, 5, tzinfo=UTC)
    brief = SimpleNamespace(start=date(2026, 11, 11), end=date(2026, 11, 11), generated_at=scheduled_for)

    async def preview(*args, **kwargs):
        return brief

    monkeypatch.setattr("app.services.social.morning_brief.preview", preview)
    signed = schedule.signed_payload(56)["signed_config"]
    unavailable = await brief_schedule.prepare_dispatch(
        schedule,
        task_id=56,
        task_config_token=signed,
        scheduled_for=scheduled_for,
    )
    assert unavailable["external_link_available"] is False
    assert unavailable["message"].startswith("Откройте обзор: /app/digests/morning?")

    monkeypatch.setattr("app.core.config.settings.PUBLIC_BASE_URL", "https://brief.example.test")
    available = await brief_schedule.prepare_dispatch(
        schedule,
        task_id=56,
        task_config_token=signed,
        scheduled_for=scheduled_for,
    )
    assert available["external_link_available"] is True
    assert available["message"].startswith("Откройте обзор: https://brief.example.test/app/digests/morning?")


@pytest.mark.asyncio
async def test_schedule_form_requires_csrf_and_explicit_external_confirmation(monkeypatch):
    from fastapi import HTTPException

    from app.web import digests

    calls = []
    request = SimpleNamespace(
        state=SimpleNamespace(web_user=SimpleNamespace(id=34), tenant_id=12),
        session={},
    )
    monkeypatch.setattr(digests, "ensure_csrf", lambda request, token: token == "valid")
    monkeypatch.setattr(digests, "_brief_timezone", lambda timezone_name: timezone_name)
    monkeypatch.setattr(digests, "add_flash", lambda *args, **kwargs: None)

    async def configure_schedule(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(brief_schedule, "configure_schedule", configure_schedule)

    with pytest.raises(HTTPException) as csrf_error:
        await digests.morning_brief_schedule(request, local_time="07:05", tz="Europe/Kirov", token="invalid")
    assert csrf_error.value.status_code == 403

    with pytest.raises(HTTPException) as confirmation_error:
        await digests.morning_brief_schedule(
            request,
            local_time="07:05",
            tz="Europe/Kirov",
            channel="telegram",
            confirm_external=False,
            token="valid",
        )
    assert confirmation_error.value.status_code == 400
    assert calls == []

    response = await digests.morning_brief_schedule(
        request,
        local_time="07:05",
        tz="Europe/Kirov",
        days=3,
        channel="telegram",
        confirm_external=True,
        token="valid",
    )
    assert response.status_code == 303
    assert calls == [
        (
            (12, 34),
            {
                "local_time": "07:05",
                "timezone_name": "Europe/Kirov",
                "days": 3,
                "channel": "telegram",
                "notify_confirmed": True,
            },
        )
    ]
