"""No-DB notification and quiet-hours contracts for P16-T02."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.core.tenant_context import tenant_scope
from app.services.notifications import reminders as module
from app.services.notifications.reminders import ReminderNotifications

__source_only__ = True
UTC = timezone.utc


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import app.core.database as database

    def forbidden(*args, **kwargs):
        raise AssertionError("real DB forbidden in notification unit tests")

    event.listen(Dialect, "do_connect", forbidden)
    monkeypatch.setattr(database, "async_session_maker", forbidden)
    with tenant_scope(7):
        yield
    event.remove(Dialect, "do_connect", forbidden)


class Session:
    def __init__(self, preference=None):
        self.preference = preference
        self.added = []
        self.flushes = 0
        self.rows = []

    async def get(self, model, key):
        return self.preference

    async def scalar(self, query):
        return None

    async def scalars(self, query):
        return SimpleNamespace(all=lambda: self.rows)

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushes += 1


def prefs(**changes):
    values = {**module.DEFAULTS, **changes}
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_defaults_allow_inbox_tasks_when_preferences_absent():
    assert await ReminderNotifications().allowed_at(
        Session(), 3, "Europe/Moscow", datetime(2026, 11, 8, 12, tzinfo=UTC)
    ) == (None, None)


@pytest.mark.asyncio
async def test_tasks_disabled_and_corrupt_preferences_defer_without_dropping():
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    allowed_at, reason = await ReminderNotifications().allowed_at(Session(prefs(tasks=False)), 3, "UTC", now)
    assert allowed_at == now.replace(hour=13) and reason == "tasks_disabled"
    allowed_at, reason = await ReminderNotifications().allowed_at(Session(prefs(start="invalid")), 3, "UTC", now)
    assert allowed_at == now.replace(hour=13) and reason == "invalid_preferences"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 11, 8, 23, 30, tzinfo=UTC), datetime(2026, 11, 9, 8, tzinfo=UTC)),
        (datetime(2026, 11, 8, 7, 30, tzinfo=UTC), datetime(2026, 11, 8, 8, tzinfo=UTC)),
        (datetime(2026, 11, 8, 8, 0, tzinfo=UTC), None),
        (datetime(2026, 11, 8, 21, 59, tzinfo=UTC), None),
        (datetime(2026, 11, 8, 22, 0, tzinfo=UTC), datetime(2026, 11, 9, 8, tzinfo=UTC)),
    ],
)
async def test_quiet_cross_midnight_boundaries(now, expected):
    value, reason = await ReminderNotifications().allowed_at(
        Session(prefs(quiet=True, start="22:00", end="08:00")), 3, "UTC", now
    )
    assert value == expected
    assert reason == ("quiet_hours" if expected else None)


@pytest.mark.asyncio
async def test_quiet_minutes_and_timezone_are_used():
    value, reason = await ReminderNotifications().allowed_at(
        Session(prefs(quiet=True, start="22:15", end="08:30")),
        3,
        "America/Los_Angeles",
        datetime(2026, 11, 8, 7, 20, tzinfo=UTC),  # 23:20 PST
    )
    assert value == datetime(2026, 11, 8, 16, 30, tzinfo=UTC)  # 08:30 PST
    assert reason == "quiet_hours"


@pytest.mark.asyncio
async def test_quiet_end_in_dst_gap_normalizes_to_first_valid_instant():
    # 02:30 does not exist on the spring transition; the first valid wall time
    # after the gap is 03:30 local, not an instant in the past.
    value, reason = await ReminderNotifications().allowed_at(
        Session(prefs(quiet=True, start="22:00", end="02:30")),
        3,
        "America/New_York",
        datetime(2026, 3, 8, 6, 45, tzinfo=UTC),  # 01:45 EST
    )
    assert value == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert reason == "quiet_hours"


@pytest.mark.asyncio
async def test_publish_uses_same_transaction_and_generation_scoped_dedup(monkeypatch):
    captured = {}

    async def publish(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=41)

    monkeypatch.setattr(module.NotificationReceipt.objects, "publish", publish)
    session = Session()
    reminder = SimpleNamespace(id=19, tenant_id=7, recipient_user_id=3, generation=2, channel="telegram", late=False)
    task = SimpleNamespace(
        id=11, tenant_id=7, due_at=datetime(2026, 11, 8, 11, tzinfo=UTC), due_date=None, timezone="UTC"
    )
    event_row = await ReminderNotifications().publish(
        session=session, reminder=reminder, task=task, now=datetime(2026, 11, 8, 12, tzinfo=UTC)
    )
    assert event_row.id == 41
    assert captured["session"] is session and captured["recipient_user_ids"] == [3]
    assert captured["dedup_key"] == "planner-reminder:19:2"
    assert captured["related_entity_id"] == 19 and captured["related_entity_type"] == "planner_reminder"
    assert "Late reminder" in captured["message"] and reminder.late is True
    assert len(session.added) == 1
    assert (session.added[0].channel, session.added[0].state) == ("telegram", "pending")


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["vk", "email", "max"])
async def test_unimplemented_external_channels_are_explicitly_blocked_with_inbox_fallback(monkeypatch, channel):
    captured = {}
    monkeypatch.setattr(
        module.NotificationReceipt.objects,
        "publish",
        AsyncMock(side_effect=lambda **kw: captured.update(kw) or SimpleNamespace(id=8)),
    )
    session = Session()
    reminder = SimpleNamespace(id=4, tenant_id=7, recipient_user_id=3, generation=0, channel=channel, late=False)
    task = SimpleNamespace(id=11, tenant_id=7, due_at=None, due_date=None, timezone="UTC")
    await ReminderNotifications().publish(
        session=session, reminder=reminder, task=task, now=datetime(2026, 11, 8, 12, tzinfo=UTC)
    )
    row = session.added[0]
    assert row.state == "blocked" and row.error_code == "channel_not_implemented"
    assert channel in captured["message"] and captured["severity"] == "warning"


@pytest.mark.asyncio
async def test_publish_rejects_cross_workspace_and_unscoped_bypass(monkeypatch):
    reminder = SimpleNamespace(id=1, tenant_id=8, recipient_user_id=3, generation=0, channel="inbox", late=False)
    task = SimpleNamespace(id=2, tenant_id=8, due_at=None, due_date=None, timezone="UTC")
    with pytest.raises(PermissionError):
        await ReminderNotifications().publish(
            session=Session(), reminder=reminder, task=task, now=datetime(2026, 11, 8, 12, tzinfo=UTC)
        )
    with tenant_scope(7, bypass=True), pytest.raises(PermissionError):
        await ReminderNotifications().publish(
            session=Session(), reminder=reminder, task=task, now=datetime(2026, 11, 8, 12, tzinfo=UTC)
        )


@pytest.mark.asyncio
async def test_destination_requires_fresh_planner_visibility(monkeypatch):
    service = ReminderNotifications()

    class Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, query):
            return 11

    monkeypatch.setattr(module, "async_session_maker", lambda: Context())
    monkeypatch.setattr(module, "select", lambda *args: SimpleNamespace(where=lambda *args: object()))
    from app.services.planner import tasks

    monkeypatch.setattr(tasks.planner_service, "get", AsyncMock(return_value=SimpleNamespace(id=11)))
    assert (
        await service.destination(SimpleNamespace(tenant_id=7, related_entity_id=19), 3)
        == "/app/planner/11/edit#reminders"
    )
    tasks.planner_service.get.side_effect = PermissionError
    assert await service.destination(SimpleNamespace(tenant_id=7, related_entity_id=19), 3) is None


@pytest.mark.asyncio
async def test_telegram_recipient_requires_one_verified_identity_and_private_join(monkeypatch):
    service = ReminderNotifications()
    session = Session()
    session.rows = [SimpleNamespace(subject_key="12345")]

    async def scalar(query):
        return 99

    session.scalar = scalar
    monkeypatch.setattr(module, "select", lambda *args: SimpleNamespace(where=lambda *args: Query()))

    class Query:
        def order_by(self, *args):
            return self

        def limit(self, *args):
            return self

        def with_for_update(self, **kwargs):
            return self

    assert await service._telegram_recipient(session, 3) == "12345"
    session.rows = [SimpleNamespace(subject_key="12345"), SimpleNamespace(subject_key="12345")]
    assert await service._telegram_recipient(session, 3) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101, True])
async def test_dispatch_rejects_invalid_batch_limit(limit):
    with pytest.raises(ValueError, match="invalid_page"):
        await ReminderNotifications().dispatch_pending(limit=limit)


@pytest.mark.asyncio
async def test_dispatch_rejects_naive_clock():
    with pytest.raises(ValueError, match="aware_time_required"):
        await ReminderNotifications().dispatch_pending(now=datetime(2026, 11, 8, 12))


class DispatchSession:
    def __init__(self, row, order):
        self.row, self.order = row, order

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model, key):
        if model is module.NotificationDelivery:
            return self.row
        if model is module.Notification:
            return SimpleNamespace(
                id=41, title="Planner reminder", message="Task reminder", notification_type="SYSTEM_UPDATE"
            )
        return None

    async def scalars(self, query):
        # The first session enumerates due work; the later sessions lock one
        # selected row and its authority in separate transactions.
        return SimpleNamespace(all=lambda: [self.row.id])

    async def scalar(self, query):
        return self.row

    async def commit(self):
        self.order.append(("commit", self.row.state))


class DispatchContext:
    def __init__(self, row, order):
        self.row, self.order = row, order

    def __call__(self):
        return DispatchSession(self.row, self.order)


def delivery(state="pending", *, started_at=None):
    return SimpleNamespace(
        id=9,
        tenant_id=7,
        notification_id=41,
        user_id=3,
        channel="telegram",
        state=state,
        started_at=started_at,
        sent_at=None,
        error_code=None,
        next_attempt_at=datetime(2026, 11, 8, 12, tzinfo=UTC),
    )


def mock_dispatch(monkeypatch, row, order, *, rights=True, recipient="12345", allowed=(None, None), send=None):
    service = ReminderNotifications()
    monkeypatch.setattr(module, "async_session_maker", DispatchContext(row, order))
    monkeypatch.setattr(service, "_rights", AsyncMock(return_value=SimpleNamespace(timezone="UTC") if rights else None))
    monkeypatch.setattr(service, "_telegram_recipient", AsyncMock(return_value=recipient))
    monkeypatch.setattr(service, "allowed_at", AsyncMock(return_value=allowed))
    messenger = AsyncMock(return_value={"telegram": {"success": True}}) if send is None else send
    monkeypatch.setattr(module.messenger_service, "send_notification", messenger)
    return service, messenger


@pytest.mark.asyncio
async def test_dispatch_commits_single_attempt_before_external_send(monkeypatch):
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    row, order = delivery(), []
    service, send = mock_dispatch(monkeypatch, row, order)

    async def ordered_send(*args, **kwargs):
        order.append(("send", row.state))
        return {"telegram": {"success": True}}

    send.side_effect = ordered_send
    assert await service.dispatch_pending(now=now) == {"sent": 1, "blocked": 0, "unknown": 0, "deferred": 0}
    assert [kind for kind, _ in order] == ["commit", "send", "commit"]
    assert order[0] == ("commit", "sending") and order[1] == ("send", "sending")
    assert row.state == "sent" and row.sent_at == now and send.await_count == 1
    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 0, "unknown": 0, "deferred": 0}
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_dispatch_never_retries_a_crash_left_sending_state(monkeypatch):
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    row, order = delivery("sending", started_at=now - module.timedelta(minutes=3)), []
    service, send = mock_dispatch(monkeypatch, row, order)
    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 0, "unknown": 1, "deferred": 0}
    assert row.state == "unknown" and row.error_code == "outcome_unconfirmed"
    assert send.await_count == 0

    row, order = delivery("sending", started_at=now), []
    service, send = mock_dispatch(monkeypatch, row, order)
    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 0, "unknown": 0, "deferred": 0}
    assert row.state == "sending" and send.await_count == 0


@pytest.mark.asyncio
async def test_dispatch_marks_transport_exception_unknown_without_retry(monkeypatch):
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    row, order = delivery(), []
    send = AsyncMock(side_effect=TimeoutError("private provider details"))
    service, send = mock_dispatch(monkeypatch, row, order, send=send)
    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 0, "unknown": 1, "deferred": 0}
    assert row.state == "unknown" and row.error_code == "outcome_unconfirmed"
    assert "private provider details" not in str(row.error_code) and send.await_count == 1


@pytest.mark.asyncio
async def test_dispatch_blocks_revoked_visibility_or_missing_private_recipient(monkeypatch):
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    row, order = delivery(), []
    service, send = mock_dispatch(monkeypatch, row, order, rights=False)
    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 1, "unknown": 0, "deferred": 0}
    assert row.state == "blocked" and row.error_code == "recipient_or_task_unavailable"
    assert send.await_count == 0

    row, order = delivery(), []
    service, send = mock_dispatch(monkeypatch, row, order, recipient=None)
    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 1, "unknown": 0, "deferred": 0}
    assert row.state == "blocked" and row.error_code == "personal_telegram_unavailable"
    assert send.await_count == 0


@pytest.mark.asyncio
async def test_dispatch_defers_quiet_hours_without_consuming_attempt(monkeypatch):
    now = datetime(2026, 11, 8, 7, 30, tzinfo=UTC)
    row, order = delivery(), []
    row.next_attempt_at = now
    future = datetime(2026, 11, 8, 8, tzinfo=UTC)
    service, send = mock_dispatch(monkeypatch, row, order, allowed=(future, "quiet_hours"))
    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 0, "unknown": 0, "deferred": 1}
    assert row.next_attempt_at == future and row.error_code == "quiet_hours" and row.state == "pending"
    assert row.started_at is None and send.await_count == 0


@pytest.mark.asyncio
async def test_dispatch_rechecks_preferences_before_send_and_deduplicates_after_defer(monkeypatch):
    now = datetime(2026, 11, 8, 7, 30, tzinfo=UTC)
    later = datetime(2026, 11, 8, 8, tzinfo=UTC)
    row, order = delivery(), []
    row.next_attempt_at = now
    service, send = mock_dispatch(monkeypatch, row, order)
    service.allowed_at = AsyncMock(side_effect=[(None, None), (later, "tasks_disabled")])

    assert await service.dispatch_pending(now=now) == {"sent": 0, "blocked": 0, "unknown": 0, "deferred": 1}
    assert row.state == "pending" and row.started_at is None
    assert row.next_attempt_at == later and row.error_code == "tasks_disabled"
    assert send.await_count == 0

    order.clear()
    service.allowed_at = AsyncMock(return_value=(None, None))
    assert await service.dispatch_pending(now=later) == {"sent": 1, "blocked": 0, "unknown": 0, "deferred": 0}
    assert row.state == "sent" and send.await_count == 1
    assert await service.dispatch_pending(now=later) == {"sent": 0, "blocked": 0, "unknown": 0, "deferred": 0}
    assert send.await_count == 1
