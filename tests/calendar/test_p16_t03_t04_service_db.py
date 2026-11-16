"""PostgreSQL calendar orchestration contracts; run only on isolated test_schema."""

import asyncio
import json
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, text

from app.core.access_catalog import resolve_permission_key, workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.oauth.service import OAuthFlowError, encode_secret_bundle
from app.models import (
    ActionAuditEvent,
    BusinessCommand,
    BusinessCommandTarget,
    Tenant,
    TenantUser,
    User,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
)
from app.models.calendar import CalendarChoice, CalendarEvent, SourceObject
from app.models.planner import PlannerReminder, PlannerTask
from app.services.calendar.contracts import (
    CalendarError,
    CallbackInput,
    EventInput,
    GoogleConnect,
    PreconditionFailed,
    WritePreview,
    YandexConnect,
)
from app.services.calendar.service import CalendarService, calendar_service
from app.services.commands import ledger as command_ledger
from app.services.commands.ledger import CommandDenied
from app.services.planner.contracts import TaskCreate, TaskUpdate
from app.services.planner.reminder_contracts import ReminderCreate
from app.services.planner.reminders import reminder_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from app.utils.crypto import encrypt_secret
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def calendar_test_schema_gate(monkeypatch):
    require_isolated_schema()
    assert resolve_permission_key("planner.task.update") is not None, "planner access catalog is required"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())


def scope(world, workspace=0, user=0):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(permission_scope(world.users[user], member_grants=workspace_grants("owner")))
    return stack


@pytest.fixture
async def calendar_world(personal_world):
    """Clear command/audit rows that reference owned actors before shared fixture cleanup."""
    world = personal_world
    tenant_ids = [row.id for row in world.tenants]
    try:
        yield world
    finally:
        with tenant_scope(bypass=True), operator_permission_scope():
            async with async_session_maker() as session, session.begin():
                owned_connection_ids = list(
                    (
                        await session.scalars(
                            select(WorkspaceConnection.id).where(WorkspaceConnection.tenant_id.in_(tenant_ids))
                        )
                    ).all()
                )
                command_ids = select(BusinessCommand.id).where(BusinessCommand.tenant_id.in_(tenant_ids))
                await session.execute(
                    delete(BusinessCommandTarget).where(BusinessCommandTarget.command_id.in_(command_ids))
                )
                await session.execute(delete(BusinessCommand).where(BusinessCommand.tenant_id.in_(tenant_ids)))
                await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
                await session.execute(delete(ActionAuditEvent).where(ActionAuditEvent.tenant_id.in_(tenant_ids)))
            for tenant_id in tenant_ids:
                await Tenant.objects.delete_by_id(tenant_id)
            for user_id in (row.id for row in world.users):
                await User.objects.delete_by_id(user_id)
            async with async_session_maker() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(BusinessCommand)
                        .where(BusinessCommand.tenant_id.in_(tenant_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ActionAuditEvent)
                        .where(ActionAuditEvent.tenant_id.in_(tenant_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(SourceObject).where(SourceObject.tenant_id.in_(tenant_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(CalendarEvent).where(CalendarEvent.tenant_id.in_(tenant_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(CalendarChoice).where(CalendarChoice.tenant_id.in_(tenant_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(WorkspaceConnection)
                        .where(WorkspaceConnection.tenant_id.in_(tenant_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(WorkspaceConnectionSecret)
                        .where(WorkspaceConnectionSecret.connection_id.in_(owned_connection_ids))
                    )
                    == 0
                )
                assert (
                    await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.id.in_(tenant_ids))) == 0
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(User).where(User.id.in_([row.id for row in world.users]))
                    )
                    == 0
                )


async def connection(world, *, workspace=0, user=0, provider="google_calendar", consent=True, read_only=False):
    with (
        tenant_scope(world.tenants[workspace].id),
        permission_scope(world.users[user], member_grants=workspace_grants("owner")),
    ):
        async with async_session_maker() as session, session.begin():
            row = WorkspaceConnection(
                tenant_id=world.tenants[workspace].id,
                owner_user_id=world.users[user].id,
                platform="calendar",
                provider_id=provider,
                auth_method="oauth2",
                schema_version=1,
                label="Synthetic calendar",
                account_key=f"calendar-{workspace}-{user}-{provider}-{uuid.uuid4().hex}",
                config={
                    "calendar_id": "/calendars/alice/work/" if provider == "yandex_calendar" else "primary",
                    "read_only": read_only,
                    "write_consent": consent,
                    "consented_by": world.users[user].id,
                },
                is_active=True,
                state="verified",
            )
            session.add(row)
            await session.flush()
            return row.id


class Adapter:
    def __init__(self, *, pull=None, read=None, write=None):
        self.pull_result = pull or ([], "cursor-next")
        self.read_result = read or {"external_id": "remote", "etag": "v2", "deleted": False, "payload": {}}
        self.write_result = write or {"external_id": "remote", "etag": "v2", "deleted": False, "payload": {}}
        self.writes = 0
        self.reads = 0

    async def pull(self, cursor=None):
        if isinstance(self.pull_result, Exception):
            raise self.pull_result
        return self.pull_result

    async def read(self, external_id):
        self.reads += 1
        if isinstance(self.read_result, Exception):
            raise self.read_result
        return self.read_result

    async def write(self, external_id, event, etag=None):
        self.writes += 1
        if isinstance(self.write_result, Exception):
            raise self.write_result
        return self.write_result

    async def delete(self, external_id, etag):
        self.writes += 1


class BlockingUnknownAdapter(Adapter):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def write(self, external_id, event, etag=None):
        self.writes += 1
        self.entered.set()
        await self.release.wait()
        raise OSError("transport outcome unknown")


class BlockingSuccessAdapter(Adapter):
    def __init__(self):
        super().__init__(write={"external_id": "remote", "etag": "provider-v1", "deleted": False, "payload": {}})
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def write(self, external_id, event, etag=None):
        self.writes += 1
        self.entered.set()
        await self.release.wait()
        return self.write_result


class OAuthStub:
    def __init__(self):
        self.redirect_uri = None

    async def start_authorization(
        self,
        *,
        tenant_id,
        actor_id,
        provider_id,
        method_id,
        account_key,
        config_overrides,
        redirect_uri,
        session,
        **kwargs,
    ):
        self.redirect_uri = redirect_uri
        row = WorkspaceConnection(
            tenant_id=tenant_id,
            owner_user_id=actor_id,
            platform="calendar",
            provider_id=provider_id,
            auth_method=method_id,
            schema_version=1,
            label="Synthetic Google",
            account_key=account_key,
            config=config_overrides,
            is_active=True,
            state="unverified",
        )
        session.add(row)
        await session.flush()
        return {
            "connection_id": row.id,
            "authorize_url": "https://accounts.google.test/authorize",
            "state": "single-use-state",
        }

    async def complete_callback(
        self, *, tenant_id, actor_id, provider_id, method_id, state_token, code, expected_connection_id, session
    ):
        assert (state_token, code, provider_id, method_id) == (
            "single-use-state",
            "authorization-code",
            "google_calendar",
            "oauth2_readonly",
        )
        row = await session.get(WorkspaceConnection, expected_connection_id)
        row.state = "verified"


class RefreshOAuth:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def refresh_tokens(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def service(adapter):
    return CalendarService(adapter_factory=lambda conn: adapter)


async def test_choice_is_explicit_stable_and_never_falls_back_across_tenants(calendar_world):
    world = calendar_world
    first = await connection(world, provider="google_calendar")
    second = await connection(world, provider="yandex_calendar")
    foreign = await connection(world, workspace=1, provider="google_calendar")
    adapter = Adapter()
    svc = service(adapter)
    with scope(world):
        listed = await svc.connections()
        assert listed["selected_connection_id"] is None and listed["choice_required"] is True
        with pytest.raises(PlannerConflict, match="calendar_choice_required"):
            await svc.events()
        with pytest.raises(PlannerUnavailable):
            await svc.choose(foreign)
        await svc.choose(first)
        assert (await svc.connections())["selected_connection_id"] == first
        await svc.choose(second)
        assert (await svc.connections())["selected_connection_id"] == second


async def test_unavailable_source_never_looks_empty_or_advances_sync_cursor(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter(pull=CalendarError("source_unavailable"), read=CalendarError("source_unavailable"))
    svc = service(adapter)
    with scope(world):
        async with async_session_maker() as session, session.begin():
            source = SourceObject(tenant_id=world.tenants[0].id, connection_id=connection_id, external_id="unavailable")
            session.add(source)
            await session.flush()
            session.add(
                CalendarEvent(
                    tenant_id=world.tenants[0].id,
                    source_object_id=source.id,
                    origin="external",
                    state="synced",
                    etag="v1",
                    version=1,
                )
            )
        await svc.choose(connection_id)
        synced = await svc.sync(connection_id)
        assert synced == {"state": "source_unavailable", "events": 0}
        events = await svc.events()
        assert events["state"] == "source_unavailable" and events["items"] is None
        async with async_session_maker() as session:
            conn = await session.get(WorkspaceConnection, connection_id)
            assert "cursor" not in conn.config


async def test_empty_event_inventory_distinguishes_unavailable_nonempty_and_genuinely_empty_source(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter(pull=CalendarError("source_unavailable"))
    svc = service(adapter)
    with scope(world):
        await svc.choose(connection_id)
        unavailable = await svc.events()
        assert unavailable == {"state": "source_unavailable", "items": None, "next_before_id": None}

        adapter.pull_result = (
            [
                {
                    "external_id": "unindexed-provider-event",
                    "etag": "v1",
                    "deleted": False,
                    "payload": {},
                }
            ],
            "provider-cursor",
        )
        populated = await svc.events()
        assert populated == {"state": "sync_required", "items": None, "next_before_id": None}

        adapter.pull_result = ([], "empty-provider-cursor")
        genuinely_empty = await svc.events()
        assert genuinely_empty == {"state": "available", "items": [], "next_before_id": None}
        async with async_session_maker() as session:
            conn = await session.get(WorkspaceConnection, connection_id)
            assert "cursor" not in conn.config


async def test_repeated_sync_reuses_shared_source_identity_and_missing_live_event_is_tombstone(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    item = {"external_id": "provider-event-1", "etag": "v1", "deleted": False, "payload": {"id": "provider-event-1"}}
    adapter = Adapter(pull=([item], "cursor-1"))
    svc = service(adapter)
    with scope(world):
        await svc.choose(connection_id)
        assert (await svc.sync(connection_id))["events"] == 1
        adapter.pull_result = ([{**item, "etag": "v2"}], "cursor-2")
        assert (await svc.sync(connection_id))["events"] == 1
        async with async_session_maker() as session:
            sources = list(
                (
                    await session.scalars(
                        select(SourceObject).where(
                            SourceObject.tenant_id == world.tenants[0].id, SourceObject.connection_id == connection_id
                        )
                    )
                ).all()
            )
            events = list(
                (
                    await session.scalars(select(CalendarEvent).where(CalendarEvent.tenant_id == world.tenants[0].id))
                ).all()
            )
            assert len(sources) == len(events) == 1
            assert events[0].etag == "v2"
        adapter.read_result = CalendarError("event_missing")
        projection = await svc.events()
        assert projection["state"] == "available"
        assert projection["items"][0]["source"] == {"deleted": True}


async def test_remote_deletion_with_local_proposal_becomes_conflict_and_advances_complete_cursor(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter(pull=([{"external_id": "remote-1", "etag": "v2", "deleted": True, "payload": {}}], "complete-2"))
    svc = service(adapter)
    with scope(world):
        async with async_session_maker() as session, session.begin():
            source = SourceObject(tenant_id=world.tenants[0].id, connection_id=connection_id, external_id="remote-1")
            session.add(source)
            await session.flush()
            event = CalendarEvent(
                tenant_id=world.tenants[0].id,
                source_object_id=source.id,
                origin="external",
                state="synced",
                etag="v1",
                version=1,
                proposal_encrypted=json.dumps({"title": "local"}),
            )
            session.add(event)
        result = await svc.sync(connection_id)
        assert result["state"] == "synced" and result["events"] == 1
        async with async_session_maker() as session:
            event = await session.scalar(select(CalendarEvent).where(CalendarEvent.tenant_id == world.tenants[0].id))
            conn = await session.get(WorkspaceConnection, connection_id)
            assert event.state == "conflict" and json.loads(event.conflict_encrypted)["remote"]["deleted"] is True
            assert conn.config["cursor"] == "complete-2"


async def test_preview_idempotency_binds_exact_request_and_consents_per_connection(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    no_consent = await connection(world, provider="yandex_calendar", consent=False)
    svc = service(Adapter())
    with scope(world):
        task = await planner_service.create(TaskCreate(title="Calendar binding"))
        event = EventInput(title="Planning", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z")
        await svc.choose(no_consent)
        with pytest.raises(PermissionError, match="calendar_consent_required"):
            await svc.preview(WritePreview(connection_id=no_consent, task_id=task.id, event=event), "deny-consent")
        await svc.choose(connection_id)
        request = WritePreview(connection_id=connection_id, task_id=task.id, event=event)
        first = await svc.preview(request, "same-key")
        replay = await svc.preview(request, "same-key")
        assert first["id"] == replay["id"] and first["payload_hash"] == replay["payload_hash"]
        changed = WritePreview(
            connection_id=connection_id,
            task_id=task.id,
            event=EventInput(title="Changed", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z"),
        )
        with pytest.raises(PlannerConflict, match="calendar_idempotency_conflict"):
            await svc.preview(changed, "same-key")


async def test_unknown_provider_outcome_is_claimed_and_never_resent(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world)
    adapter = BlockingUnknownAdapter()
    svc = service(adapter)
    monkeypatch.setattr(command_ledger, "RUNTIME_ENABLED_CAPABILITIES", {"calendar.event_write.v1"})
    with scope(world):
        await svc.choose(connection_id)
        task = await planner_service.create(TaskCreate(title="Unknown calendar write"))
        request = WritePreview(
            connection_id=connection_id,
            task_id=task.id,
            event=EventInput(title="Remote", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z"),
        )
        command = await svc.preview(request, "unknown-once")
        first_attempt = asyncio.create_task(svc.execute(command["id"]))
        await asyncio.wait_for(adapter.entered.wait(), timeout=10)
        with pytest.raises(PlannerConflict, match="calendar_command_unavailable"):
            await svc.execute(command["id"])
        adapter.release.set()
        first = await first_attempt
        assert first["state"] == "unknown" and adapter.writes == 1
        with pytest.raises(PlannerConflict, match="calendar_command_unavailable"):
            await svc.execute(command["id"])
        assert adapter.writes == 1
        async with async_session_maker() as session:
            saved = await session.get(BusinessCommand, command["id"])
            bound_task = await session.get(PlannerTask, task.id)
            assert saved.state == "unknown" and "never resend" in saved.reconcile_hint
            assert bound_task.recurrence_source == "planner"
        recovery = await svc.preview(
            WritePreview(connection_id=connection_id, event_id=first["event_id"], operation="resolve_remote"),
            "recover-unknown",
        )
        assert recovery["state"] == "previewed" and adapter.writes == 1
        await svc.ledger.approve_command(recovery["id"], expected_payload_hash=recovery["payload_hash"])
        recovered = await svc.execute(recovery["id"])
        assert recovered["state"] == "succeeded" and adapter.writes == 1
        with pytest.raises(PlannerConflict, match="calendar_command_unavailable"):
            await svc.execute(command["id"])


async def test_kill_switch_blocks_provider_io_after_preview(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter()
    svc = service(adapter)
    monkeypatch.setattr(command_ledger, "RUNTIME_ENABLED_CAPABILITIES", {"calendar.event_write.v1"})
    with scope(world):
        await svc.choose(connection_id)
        task = await planner_service.create(TaskCreate(title="Kill switch calendar write"))
        command = await svc.preview(
            WritePreview(
                connection_id=connection_id,
                task_id=task.id,
                event=EventInput(title="Remote", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z"),
            ),
            "kill-switch",
        )
        monkeypatch.setitem(command_ledger.RUNTIME_KILL_SWITCHES, "KILL_CALENDAR_EVENT_WRITE", True)
        with pytest.raises(CommandDenied):
            await svc.execute(command["id"])
        assert adapter.writes == 0


async def test_conflict_resolution_preview_requires_separate_per_item_approval(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    svc = service(Adapter())
    with scope(world):
        await svc.choose(connection_id)
        async with async_session_maker() as session, session.begin():
            source = SourceObject(
                tenant_id=world.tenants[0].id, connection_id=connection_id, external_id="remote-delete"
            )
            session.add(source)
            await session.flush()
            event = CalendarEvent(
                tenant_id=world.tenants[0].id,
                source_object_id=source.id,
                origin="external",
                state="conflict",
                etag="v1",
                version=2,
                conflict_encrypted=json.dumps({"remote": {"etag": "v2", "deleted": True}}),
            )
            session.add(event)
        command = await svc.preview(
            WritePreview(
                connection_id=connection_id,
                event_id=event.id,
                operation="resolve_local",
                event=EventInput(title="Keep local", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z"),
            ),
            "conflict-approval",
        )
        assert command["state"] == "previewed"
        assert command["preview_snapshot"]["per_item_approval_required"] is True


async def test_readonly_connection_can_accept_remote_deletion_only_after_item_approval(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world, consent=False, read_only=True)
    adapter = Adapter(
        read={
            "external_id": "readonly-remote",
            "etag": None,
            "deleted": True,
            "payload": {},
        }
    )
    svc = service(adapter)
    monkeypatch.setattr(command_ledger, "RUNTIME_ENABLED_CAPABILITIES", set())
    with scope(world):
        await svc.choose(connection_id)
        async with async_session_maker() as session, session.begin():
            source = SourceObject(
                tenant_id=world.tenants[0].id, connection_id=connection_id, external_id="readonly-remote"
            )
            session.add(source)
            await session.flush()
            event = CalendarEvent(
                tenant_id=world.tenants[0].id,
                source_object_id=source.id,
                origin="external",
                state="conflict",
                version=1,
                conflict_encrypted=json.dumps({"remote": {"etag": None, "deleted": True}}),
            )
            session.add(event)
        command = await svc.preview(
            WritePreview(connection_id=connection_id, event_id=event.id, operation="resolve_remote"),
            "readonly-deletion-review",
        )
        assert command["state"] == "previewed" and adapter.writes == 0
        with pytest.raises(PlannerConflict, match="calendar_command_unavailable"):
            await svc.execute(command["id"])
        assert adapter.writes == 0
        await svc.ledger.approve_command(command["id"], expected_payload_hash=command["payload_hash"])
        result = await svc.execute(command["id"])
        assert result["state"] == "succeeded" and adapter.writes == 0
        async with async_session_maker() as session:
            saved = await session.get(CalendarEvent, event.id)
            assert saved.state == "deleted"


async def test_expired_approval_and_revoked_membership_are_denied_before_io(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter()
    svc = service(adapter)
    monkeypatch.setattr(command_ledger, "RUNTIME_ENABLED_CAPABILITIES", {"calendar.event_write.v1"})
    with scope(world):
        await svc.choose(connection_id)
        task = await planner_service.create(TaskCreate(title="Expired approval"))
        command = await svc.preview(
            WritePreview(
                connection_id=connection_id,
                task_id=task.id,
                event=EventInput(title="Remote", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z"),
            ),
            "expired-approval",
        )
        async with async_session_maker() as session, session.begin():
            stored = await session.get(BusinessCommand, command["id"])
            from datetime import datetime, timedelta, timezone

            stored.approval_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        with pytest.raises(CommandDenied):
            await svc.execute(command["id"])
        assert adapter.writes == 0
        with tenant_scope(bypass=True), operator_permission_scope():
            await TenantUser.objects.update_by_id(world.memberships[0].id, is_active=False)
        with pytest.raises(PermissionError):
            await svc.execute(command["id"])
        assert adapter.writes == 0


async def test_precondition_failure_records_conflict_and_does_not_retry_write(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter(read={"external_id": "remote", "etag": "provider-new", "deleted": False, "payload": {}})
    adapter.write_result = PreconditionFailed()
    svc = service(adapter)
    monkeypatch.setattr(command_ledger, "RUNTIME_ENABLED_CAPABILITIES", {"calendar.event_write.v1"})
    with scope(world):
        await svc.choose(connection_id)
        task = await planner_service.create(TaskCreate(title="Concurrent remote update"))
        command = await svc.preview(
            WritePreview(
                connection_id=connection_id,
                task_id=task.id,
                event=EventInput(title="Local", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z"),
            ),
            "precondition-conflict",
        )
        result = await svc.execute(command["id"])
        assert result["state"] == "failed" and result["error_code"] == "calendar_conflict"
        assert adapter.writes == 1
        async with async_session_maker() as session:
            row = await session.get(CalendarEvent, result["event_id"])
            assert row.state == "conflict" and json.loads(row.conflict_encrypted)["remote"]["etag"] == "provider-new"


async def test_successful_calendar_write_transfers_recurrence_authority(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter(write={"external_id": "created", "etag": "v1", "deleted": False, "payload": {}})
    svc = service(adapter)
    monkeypatch.setattr(command_ledger, "RUNTIME_ENABLED_CAPABILITIES", {"calendar.event_write.v1"})
    with scope(world):
        await svc.choose(connection_id)
        task = await planner_service.create(
            TaskCreate(title="Planner recurrence", due_date="2026-11-09", recurrence="FREQ=WEEKLY;COUNT=4")
        )
        command = await svc.preview(
            WritePreview(
                connection_id=connection_id,
                task_id=task.id,
                event=EventInput(
                    title="External recurrence",
                    start="2026-11-09T10:00:00Z",
                    end="2026-11-09T11:00:00Z",
                    recurrence=["RRULE:FREQ=WEEKLY;COUNT=4"],
                ),
            ),
            "calendar-authority",
        )
        result = await svc.execute(command["id"])
        assert result["state"] == "succeeded"
        async with async_session_maker() as session:
            saved_task = await session.get(PlannerTask, task.id)
            saved_event = await session.get(CalendarEvent, result["event_id"])
            assert saved_task.recurrence_source == "calendar"
            assert saved_event.state == "synced" and saved_event.etag == "v1"


async def test_task_edit_during_provider_io_keeps_success_receipt_and_records_both_versions(
    calendar_world, monkeypatch
):
    world = calendar_world
    connection_id = await connection(world)
    adapter = BlockingSuccessAdapter()
    svc = service(adapter)
    monkeypatch.setattr(command_ledger, "RUNTIME_ENABLED_CAPABILITIES", {"calendar.event_write.v1"})
    with scope(world):
        await svc.choose(connection_id)
        task = await planner_service.create(TaskCreate(title="Task before write", due_date="2026-11-09"))
        command = await svc.preview(
            WritePreview(
                connection_id=connection_id,
                task_id=task.id,
                event=EventInput(title="Remote snapshot", start="2026-11-09T10:00:00Z", end="2026-11-09T11:00:00Z"),
            ),
            "edit-during-write",
        )
        executing = asyncio.create_task(svc.execute(command["id"]))
        await asyncio.wait_for(adapter.entered.wait(), timeout=10)
        await planner_service.update(
            task.id,
            TaskUpdate(expected_version=1, scope="occurrence", title="Task changed while provider call was pending"),
        )
        adapter.release.set()
        result = await executing
        assert result["state"] == "succeeded" and adapter.writes == 1
        async with async_session_maker() as session:
            saved_command = await session.get(BusinessCommand, command["id"])
            saved_event = await session.get(CalendarEvent, result["event_id"])
            conflict = json.loads(saved_event.conflict_encrypted)
            assert saved_command.state == "succeeded"
            assert saved_event.state == "conflict"
            assert conflict["remote"]["etag"] == "provider-v1"
            assert conflict["local"]["title"] == "Remote snapshot"
            assert conflict["planner"]["title"] == "Task changed while provider call was pending"


async def test_linked_task_reads_provider_schedule_and_fails_without_local_fallback(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter(
        read={
            "external_id": "remote-event",
            "etag": "provider-v1",
            "deleted": False,
            "payload": {
                "start": {"dateTime": "2031-05-06T09:30:00+03:00", "timeZone": "Europe/Moscow"},
                "recurrence": ["RRULE:FREQ=MONTHLY;BYDAY=1MO"],
            },
        }
    )
    monkeypatch.setattr(calendar_service, "adapter_factory", lambda conn: adapter)
    with scope(world):
        task = await planner_service.create(TaskCreate(title="Calendar-owned schedule", due_date="2027-01-01"))
        async with async_session_maker() as session, session.begin():
            saved = await session.get(PlannerTask, task.id)
            saved.recurrence_source = "calendar"
            source = SourceObject(
                tenant_id=world.tenants[0].id, connection_id=connection_id, external_id="remote-event"
            )
            session.add(source)
            await session.flush()
            session.add(
                CalendarEvent(
                    tenant_id=world.tenants[0].id,
                    source_object_id=source.id,
                    task_id=task.id,
                    origin="external",
                    state="synced",
                    etag="provider-v1",
                    version=1,
                )
            )
        projection = await planner_service.get(task.id)
        assert projection.due_at.isoformat() == "2031-05-06T09:30:00+03:00"
        assert projection.due_date is None and projection.timezone == "Europe/Moscow"
        assert projection.recurrence == "FREQ=MONTHLY;BYDAY=1MO"
        adapter.read_result = CalendarError("source_unavailable")
        with pytest.raises(PlannerConflict, match="planner_source_unavailable"):
            await planner_service.get(task.id)


async def test_yandex_connect_discovers_exact_calendar_and_disconnect_clears_secret(calendar_world, monkeypatch):
    world = calendar_world

    class Discovery:
        path = "/cal/alice/work/"

        def __init__(self, login, password, calendar_id):
            assert (login, password, calendar_id) == ("alice", "synthetic-secret", self.path)

        async def discover(self):
            return [{"id": self.path, "label": "Work"}]

    monkeypatch.setattr("app.services.calendar.service.YandexCalendar", Discovery)
    svc = CalendarService()
    with scope(world):
        result = await svc.connect_yandex(
            YandexConnect(
                calendar_id=Discovery.path,
                login="alice",
                app_password="synthetic-secret",
                read_only=False,
                write_consent=True,
            )
        )
        connection_id = result["connection_id"]
        assert result["state"] == "verified"
        assert (await svc.connections())["selected_connection_id"] is None
        await svc.choose(connection_id)
        await svc.disconnect(connection_id)
        async with async_session_maker() as session:
            saved = await session.get(WorkspaceConnection, connection_id)
            from app.models import WorkspaceConnectionSecret

            secret = await session.get(WorkspaceConnectionSecret, connection_id)
            choice = await session.scalar(select(CalendarChoice).where(CalendarChoice.tenant_id == world.tenants[0].id))
            assert saved.state == "disabled" and not saved.is_active
            assert secret.secret_encrypted == "" and choice.connection_id is None


async def test_google_adapter_uses_encrypted_bundle_after_oauth_refresh(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    oauth = RefreshOAuth()
    svc = CalendarService(oauth=oauth)
    with scope(world):
        async with async_session_maker() as session, session.begin():
            conn = await session.get(WorkspaceConnection, connection_id)
            session.add(
                WorkspaceConnectionSecret(
                    connection_id=connection_id,
                    secret_encrypted=encode_secret_bundle(
                        access_token="synthetic-google-access",
                        refresh_token="synthetic-google-refresh",
                        expires_at=datetime(2031, 1, 1, tzinfo=timezone.utc),
                    ),
                )
            )
            adapter = await svc._adapter(session, conn, (world.tenants[0].id, world.users[0].id))
        assert adapter.headers["Authorization"] == "Bearer synthetic-google-access"
        assert oauth.calls[0]["connection_id"] == connection_id
        assert oauth.calls[0]["method_id"] == "oauth2"


async def test_google_refresh_revocation_sets_connection_reauth(calendar_world):
    world = calendar_world
    connection_id = await connection(world)
    svc = CalendarService(oauth=RefreshOAuth(OAuthFlowError("oauth_reauth_required")))
    with scope(world):
        result = await svc.sync(connection_id)
        assert result == {"state": "reauth", "events": 0}
        async with async_session_maker() as session:
            conn = await session.get(WorkspaceConnection, connection_id)
            assert conn.state == "reauth"


async def test_yandex_adapter_reads_only_its_encrypted_connection_credentials(calendar_world):
    world = calendar_world
    connection_id = await connection(world, provider="yandex_calendar")
    svc = CalendarService()
    with scope(world):
        async with async_session_maker() as session, session.begin():
            conn = await session.get(WorkspaceConnection, connection_id)
            session.add(
                WorkspaceConnectionSecret(
                    connection_id=connection_id,
                    secret_encrypted=encrypt_secret(
                        json.dumps({"login": "alice", "password": "synthetic-yandex-app-password"})
                    ),
                )
            )
            adapter = await svc._adapter(session, conn, (world.tenants[0].id, world.users[0].id))
        import base64

        encoded = adapter.headers["Authorization"].removeprefix("Basic ")
        credentials = base64.b64decode(encoded).decode()
        assert credentials == "alice:synthetic-yandex-app-password"


async def test_google_callback_binds_single_use_state_to_its_connection(calendar_world, monkeypatch):
    world = calendar_world
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://assistant.example.test")
    oauth = OAuthStub()
    svc = CalendarService(oauth=oauth)
    with scope(world):
        started = await svc.connect_google(GoogleConnect(calendar_id="primary", read_only=True))
        assert started["authorize_url"].startswith("https://")
        assert oauth.redirect_uri == "https://assistant.example.test/app/settings/oauth/google_calendar/callback"
        from app.services.planner.tasks import PlannerUnavailable

        with pytest.raises(PlannerUnavailable):
            await svc.callback_google(CallbackInput(state="wrong-state", code="authorization-code"))
        completed = await svc.callback_google(CallbackInput(state=started["state"], code="authorization-code"))
        assert completed == {"connection_id": started["connection_id"], "state": "verified"}
        assert (await svc.connections())["selected_connection_id"] is None


async def test_custom_external_reminder_uses_live_provider_schedule(calendar_world, monkeypatch):
    world = calendar_world
    connection_id = await connection(world)
    adapter = Adapter(
        read={
            "external_id": "remote-reminder",
            "etag": "provider-v1",
            "deleted": False,
            "payload": {
                "start": {"dateTime": "2031-05-06T09:30:00+03:00", "timeZone": "Europe/Moscow"},
                "recurrence": [],
            },
        }
    )
    monkeypatch.setattr(calendar_service, "adapter_factory", lambda conn: adapter)
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    with scope(world):
        task = await planner_service.create(TaskCreate(title="Calendar reminder", due_date="2020-01-01"))
        async with async_session_maker() as session, session.begin():
            saved = await session.get(PlannerTask, task.id)
            saved.recurrence_source = "calendar"
            source = SourceObject(
                tenant_id=world.tenants[0].id, connection_id=connection_id, external_id="remote-reminder"
            )
            session.add(source)
            await session.flush()
            session.add(
                CalendarEvent(
                    tenant_id=world.tenants[0].id,
                    source_object_id=source.id,
                    task_id=task.id,
                    origin="external",
                    state="synced",
                    etag="provider-v1",
                    version=1,
                )
            )
        reminder = await reminder_service.create(
            task.id, ReminderCreate(preset="custom", remind_at=now - timedelta(minutes=1))
        )
    with tenant_scope(world.tenants[0].id):
        result = await reminder_service.dispatch_due(now=now)
    assert result["sent"] == 1 and adapter.reads >= 1
    async with async_session_maker() as session:
        stored = await session.get(PlannerReminder, reminder.id)
        assert stored.state == "delivered"
        # Local task date was 2020; the current provider schedule is in 2031.
        assert stored.late is False
