"""No-DB fault injection. Transaction double is NOT PostgreSQL lock evidence."""

import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList, False_, True_
from sqlalchemy.sql.selectable import ScalarSelect

from app.channels.senders.contracts import EMAIL, SendResult
from app.core.config import settings
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    Contact,
    ContactConsent,
    ContactIdentity,
    Tenant,
    TenantUser,
    User,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
    WorkspaceRole,
)
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.communications.outbox import OutboxMessage, OutboxTarget
from app.services.commands import ledger
from app.services.commands.registry import canonical_payload_hash, get_capability
from app.services.communications import outbox as pure
from app.services.communications import outbox_store as store
from app.services.communications.outbox_contracts import DraftIn, MessageOut, ScheduleIn
from app.utils.crypto import encrypt_secret

__source_only__ = True
UTC = timezone.utc
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
ACTOR = NS(id=3, is_active=True)


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    import app.core.database as database

    def forbidden(*args, **kwargs):
        raise AssertionError("Real DB forbidden in remote outbox tests")

    event.listen(Dialect, "do_connect", forbidden)
    monkeypatch.setattr(database, "async_session_maker", forbidden)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(ledger, "RUNTIME_ENABLED_CAPABILITIES", {EMAIL})
    monkeypatch.setattr(ledger, "RUNTIME_KILL_SWITCHES", {})
    with tenant_scope(7), permission_scope(ACTOR):
        yield
    event.remove(Dialect, "do_connect", forbidden)


class Memory:
    """Explicit ORM boundary double with committed state and rollback on failure.

    It serializes transactions (does NOT implement SKIP LOCKED); SQL lock shape is
    separately asserted. Restart means a new dispatcher and a fresh session read.
    """

    def __init__(self, data=None):
        self.data = data or {}
        self.lock = asyncio.Lock()
        self.commits = 0
        self.fail_commit = None
        self.queries = []

    def __call__(self):
        return Session(self)


class Session:
    def __init__(self, db):
        self.db, self.data = db, db.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    @asynccontextmanager
    async def begin(self):
        async with self.db.lock:
            self.data = copy.deepcopy(self.db.data)
            try:
                yield
            except BaseException:
                raise
            else:
                if self.db.fail_commit == self.db.commits + 1:
                    raise RuntimeError("synthetic commit failure")
                self.db.data = self.data
                self.db.commits += 1

    def add(self, obj):
        rows = self.data.setdefault(type(obj), [])
        if obj.id is None:
            obj.id = max([row.id for row in rows], default=10) + 1
        rows.append(obj)

    async def flush(self):
        pass

    def value(self, node, context):
        if isinstance(node, BindParameter):
            return node.value
        if isinstance(node, True_):
            return True
        if isinstance(node, False_):
            return False
        if isinstance(node, ScalarSelect):
            return self.select(node.element)
        if isinstance(node, BooleanClauseList):
            values = [self.value(c, context) for c in node.clauses]
            return all(values) if node.operator == operators.and_ else any(values)
        if hasattr(node, "operator") and hasattr(node, "left"):
            left, right = self.value(node.left, context), self.value(node.right, context)
            if node.operator == operators.in_op:
                return left in right
            if node.operator == operators.is_:
                return left is right
            return node.operator(left, right)
        if getattr(node, "table", None) is not None:
            return getattr(context[node.table.name], node.key)
        if str(node) == "NULL":
            return None
        raise AssertionError(f"Unsupported mock SQL: {node!r}")

    def select(self, query):
        self.db.queries.append(query)
        descriptor = query.column_descriptions[0]
        entity, expression = descriptor["entity"], descriptor["expr"]
        rows = []
        for row in self.data.get(entity, []):
            context = {entity.__tablename__: row}
            if entity is OutboxMessage and "JOIN" in str(query):
                contexts = [
                    {**context, "outbox_targets": target}
                    for target in self.data.get(OutboxTarget, [])
                    if target.message_id == row.id and target.tenant_id == row.tenant_id
                ]
            else:
                contexts = [context]
            if any(all(self.value(c, ctx) for c in query._where_criteria) for ctx in contexts):
                rows.append(row if expression is entity else getattr(row, expression.key))
        if query._limit_clause is not None:
            rows = rows[: query._limit_clause.value]
        return rows

    async def scalar(self, query):
        rows = self.select(query)
        return rows[0] if rows else None

    async def scalars(self, query):
        return NS(all=lambda: self.select(query))


def seeded(count=1):
    message = OutboxMessage(
        id=1,
        tenant_id=7,
        author_id=3,
        contact_id=4,
        idempotency_key="draft-1",
        request_hash="request",
        subject_encrypted=None,
        content_encrypted="Synthetic private body",
        purpose="transactional",
        timezone="UTC",
        state="approved",
        approved_by=3,
        scheduled_at=NOW,
        approval_expires_at=NOW + timedelta(hours=1),
    )
    connection = WorkspaceConnection(
        id=5,
        tenant_id=7,
        owner_user_id=3,
        provider_id="mailru_mail",
        platform="email",
        auth_method="app_password",
        schema_version=1,
        label="Synthetic",
        account_key="sender@example.com",
        config={"account_login": "sender@example.com", "send_enabled": True},
        state="verified",
        is_active=True,
    )
    secret = WorkspaceConnectionSecret(connection_id=5, secret_encrypted=encrypt_secret("synthetic-password"))
    credential = store.digest(
        {"config": connection.config, "provider": connection.provider_id, "secret_ciphertext": secret.secret_encrypted}
    )
    targets = [
        OutboxTarget(
            id=n,
            tenant_id=7,
            message_id=1,
            channel="email",
            connection_id=5,
            identity_id=10 + n,
            recipient_encrypted=f"r{n}@example.com",
            credential_hash=credential,
            state="approved",
            attempts=0,
            dispatch_after=NOW,
            idempotency_key=f"key-{n}",
            command_id=n,
            content_hash=pure.compute_message_hash(f"r{n}@example.com", "email", "", message.content_encrypted),
        )
        for n in range(1, count + 1)
    ]
    message.content_hash = store.content_digest(message, targets)
    message.approved_hash = store.approval_digest(message)
    commands, children = [], []
    for target in targets:
        payload = {"outbox_id": 1, "target_id": target.id, "intent_hash": message.approved_hash}
        refs = [f"outbox:{target.id}"]
        hashed = canonical_payload_hash(EMAIL, refs, payload)
        commands.append(
            BusinessCommand(
                id=target.id,
                tenant_id=7,
                capability=EMAIL,
                capability_version=1,
                state="approved",
                actor_user_id=3,
                approved_user_id=3,
                approved_payload_hash=hashed,
                payload_hash=hashed,
                payload=payload,
                target_refs=refs,
                connection_id=5,
                approval_expires_at=message.approval_expires_at,
            )
        )
        children.append(
            BusinessCommandTarget(id=target.id, tenant_id=7, command_id=target.id, target_ref=refs[0], state="pending")
        )
    return Memory(
        {
            OutboxMessage: [message],
            OutboxTarget: targets,
            BusinessCommand: commands,
            BusinessCommandTarget: children,
            User: [NS(id=3, is_active=True)],
            Tenant: [NS(id=7, is_active=True)],
            TenantUser: [NS(id=1, tenant_id=7, user_id=3, is_active=True, role_id=1)],
            WorkspaceRole: [NS(id=1, codename="owner")],
            Contact: [Contact(id=4, tenant_id=7, merged_into_id=None)],
            ContactIdentity: [
                ContactIdentity(
                    id=t.identity_id,
                    tenant_id=7,
                    contact_id=4,
                    kind="email",
                    connection_id=5,
                    verified=True,
                    value_encrypted=t.recipient_encrypted,
                )
                for t in targets
            ],
            WorkspaceConnection: [connection],
            WorkspaceConnectionSecret: [secret],
            ContactConsent: [
                ContactConsent(
                    id=1,
                    tenant_id=7,
                    contact_id=4,
                    channel="email",
                    purpose="transactional",
                    granted_at=NOW - timedelta(days=1),
                )
            ],
        }
    )


def worker(db, result=None):
    sender = AsyncMock(return_value=result or SendResult("sent", "smtp_accepted", message_id="<synthetic@example.com>"))
    return store.OutboxDispatcher(session_factory=db, sender=sender), sender


def target(db, index=0):
    return db.data[OutboxTarget][index]


@pytest.mark.asyncio
async def test_claim_is_committed_before_io_and_sent_cannot_repeat():
    db = seeded()
    dispatcher, sender = worker(db)
    ticket = await dispatcher.claim(NOW)
    assert db.commits == 1 and target(db).state == "claimed" and sender.await_count == 0
    await dispatcher.deliver(ticket, NOW)
    assert target(db).state == "sent" and target(db).attempts == 1
    assert db.data[BusinessCommand][0].state == "succeeded"
    restarted = store.OutboxDispatcher(session_factory=db, sender=sender)
    assert await restarted.claim(NOW) is None
    assert not await restarted.deliver(ticket, NOW)
    assert await restarted.recover(NOW + timedelta(hours=2)) == 0
    assert sender.await_count == 1
    MessageOut.model_validate(store.projection(db.data[OutboxMessage][0], db.data[OutboxTarget]))


@pytest.mark.asyncio
async def test_crash_before_io_becomes_uncertain_never_reclaimed():
    db = seeded()
    first, sender = worker(db)
    ticket = await first.claim(NOW)
    restarted = store.OutboxDispatcher(session_factory=db, sender=sender)
    assert await restarted.claim(NOW) is None
    assert await restarted.recover(NOW + store.CLAIM_GRACE) == 1
    assert target(db).state == "uncertain_delivery"
    assert not await restarted.deliver(ticket, NOW + store.CLAIM_GRACE)
    assert await restarted.claim(NOW + store.CLAIM_GRACE) is None
    assert sender.await_count == 0


@pytest.mark.asyncio
async def test_claim_commit_failure_never_calls_sender():
    db = seeded()
    db.fail_commit = 1
    dispatcher, sender = worker(db)
    with pytest.raises(RuntimeError, match="commit"):
        await dispatcher.claim(NOW)
    assert target(db).state == "approved" and target(db).attempts == 0
    assert sender.await_count == 0


@pytest.mark.asyncio
async def test_ack_then_commit_failure_leaves_claim_fenced_on_restart():
    db = seeded()
    dispatcher, sender = worker(db)
    ticket = await dispatcher.claim(NOW)
    db.fail_commit = 3
    with pytest.raises(RuntimeError, match="commit"):
        await dispatcher.deliver(ticket, NOW)
    assert sender.await_count == 1 and target(db).state == "claimed"
    db.fail_commit = None
    # Even the ORIGINAL ticket may not replay after a post-I/O commit failure.
    assert not await dispatcher.deliver(ticket, NOW)
    assert sender.await_count == 1
    restarted = store.OutboxDispatcher(session_factory=db, sender=sender)
    await restarted.recover(NOW + store.CLAIM_GRACE)
    assert target(db).state == "uncertain_delivery"
    assert not await restarted.deliver(ticket, NOW + store.CLAIM_GRACE)
    assert await restarted.claim(NOW + store.CLAIM_GRACE) is None
    assert sender.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_inside_provider_leaves_durable_claim():
    db = seeded()
    dispatcher, sender = worker(db)
    sender.side_effect = asyncio.CancelledError
    ticket = await dispatcher.claim(NOW)
    with pytest.raises(asyncio.CancelledError):
        await dispatcher.deliver(ticket, NOW)
    assert target(db).state == "claimed"
    await dispatcher.recover(NOW + store.CLAIM_GRACE)
    assert target(db).state == "uncertain_delivery" and sender.await_count == 1


@pytest.mark.asyncio
async def test_two_workers_cannot_send_same_target_in_transaction_double():
    db = seeded()
    left, sender = worker(db)
    right = store.OutboxDispatcher(session_factory=db, sender=sender)
    tickets = await asyncio.gather(left.claim(NOW), right.claim(NOW))
    assert sum(ticket is not None for ticket in tickets) == 1
    ticket = next(ticket for ticket in tickets if ticket is not None)
    await asyncio.gather(left.deliver(ticket, NOW), right.deliver(ticket, NOW))
    assert sender.await_count == 1 and target(db).attempts == 1


@pytest.mark.asyncio
async def test_partial_delivery_restart_only_handles_remaining_target():
    db = seeded(2)
    first, sender = worker(db)
    await first.deliver(await first.claim(NOW), NOW)
    restarted = store.OutboxDispatcher(session_factory=db, sender=sender)
    await restarted.deliver(await restarted.claim(NOW), NOW)
    assert [t.state for t in db.data[OutboxTarget]] == ["sent", "sent"]
    assert sender.await_count == 2 and await restarted.claim(NOW) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,state",
    [
        (SendResult("unknown", "smtp_timeout"), "uncertain_delivery"),
        (SendResult("rejected", "smtp_rejected"), "failed"),
        (SendResult("blocked", "capability_disabled"), "failed"),
        (None, "uncertain_delivery"),
    ],
)
async def test_outcomes_never_reenter_eligible_set(result, state):
    db = seeded()
    dispatcher, sender = worker(db)
    sender.return_value = result
    ticket = await dispatcher.claim(NOW)
    await dispatcher.deliver(ticket, NOW)
    assert target(db).state == state
    assert await dispatcher.claim(NOW) is None
    assert not await dispatcher.deliver(ticket, NOW)
    assert sender.await_count == 1


@pytest.mark.asyncio
async def test_exception_after_io_is_unknown_and_does_not_leak_provider_text():
    db = seeded()
    dispatcher, sender = worker(db)
    sender.side_effect = RuntimeError("password recipient private-body")
    await dispatcher.deliver(await dispatcher.claim(NOW), NOW)
    assert target(db).state == "uncertain_delivery"
    assert target(db).error_code == "outbox_transport_unconfirmed"
    assert target(db).provider_message_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "body",
        "recipient",
        "credential",
        "author",
        "ttl",
        "role",
        "connection",
        "consent",
        "capability",
        "payload",
        "target_hash",
    ],
)
async def test_fail_closed_admission(change, monkeypatch):
    db = seeded()
    if change == "body":
        db.data[OutboxMessage][0].content_encrypted += " "
    if change == "recipient":
        target(db).recipient_encrypted = "foreign@example.com"
    if change == "credential":
        db.data[WorkspaceConnectionSecret][0].secret_encrypted = encrypt_secret("rotated")
    if change == "author":
        db.data[OutboxMessage][0].approved_by = 999
    if change == "ttl":
        db.data[OutboxMessage][0].approval_expires_at = NOW
    if change == "role":
        db.data[WorkspaceRole][0].codename = "manager"
    if change == "connection":
        db.data[WorkspaceConnection][0].owner_user_id = 999
    if change == "consent":
        db.data[ContactConsent][0].withdrawn_at = NOW
    if change == "capability":
        monkeypatch.setattr(ledger, "RUNTIME_ENABLED_CAPABILITIES", set())
    if change == "payload":
        db.data[BusinessCommand][0].payload = {"tampered": True}
    if change == "target_hash":
        target(db).content_hash = "sha256:" + "0" * 64
    dispatcher, sender = worker(db)
    assert await dispatcher.claim(NOW) == {"denied": True}
    assert target(db).state == "failed" and sender.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["role", "ttl", "credential", "consent"])
async def test_authority_rechecked_between_claim_and_io(change):
    db = seeded()
    dispatcher, sender = worker(db)
    ticket = await dispatcher.claim(NOW)
    if change == "role":
        db.data[WorkspaceRole][0].codename = "manager"
    if change == "ttl":
        db.data[OutboxMessage][0].approval_expires_at = NOW
    if change == "credential":
        db.data[WorkspaceConnectionSecret][0].secret_encrypted = encrypt_secret("rotated")
    if change == "consent":
        db.data[ContactConsent][0].withdrawn_at = NOW
    await dispatcher.deliver(ticket, NOW)
    assert target(db).state == "failed" and sender.await_count == 0


@pytest.mark.asyncio
async def test_forged_or_stale_claim_ticket_has_no_effect():
    db = seeded()
    dispatcher, sender = worker(db)
    ticket = await dispatcher.claim(NOW)
    assert not await dispatcher.deliver((ticket[0], ticket[1], "forged"), NOW)
    assert target(db).state == "claimed" and sender.await_count == 0
    assert not await dispatcher.deliver(ticket, NOW + store.CLAIM_GRACE)
    assert target(db).state == "uncertain_delivery" and sender.await_count == 0


@pytest.mark.asyncio
async def test_due_time_quiet_deferral_and_no_schedule_hash_mutation():
    db = seeded()
    dispatcher, sender = worker(db)
    assert await dispatcher.claim(NOW - timedelta(seconds=1)) is None
    original = db.data[OutboxMessage][0].approved_hash
    late = NOW.replace(hour=22)
    assert await dispatcher.claim(late) == {"deferred": True}
    assert target(db).dispatch_after == datetime(2026, 11, 6, 8, tzinfo=UTC)
    assert db.data[OutboxMessage][0].approved_hash == original
    assert sender.await_count == 0


@pytest.mark.parametrize(
    "zone,anchor,expected",
    [
        ("Europe/Kirov", "2026-11-05T19:00:00+00:00", "2026-11-06T05:00:00+00:00"),
        ("UTC", "2026-11-05T10:00:00+00:00", "2026-11-05T10:00:00+00:00"),
        ("America/New_York", "2026-11-01T01:30:00-04:00", "2026-11-01T13:00:00+00:00"),
    ],
)
def test_quiet_hours_and_dst(zone, anchor, expected):
    assert store.due_after_quiet_hours(datetime.fromisoformat(anchor), zone) == datetime.fromisoformat(expected)


@pytest.mark.asyncio
async def test_claim_sql_fences_tenant_due_and_skip_locked():
    db = seeded()
    dispatcher, _ = worker(db)
    await dispatcher.claim(NOW)
    query = db.queries[0]
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF" in sql and "SKIP LOCKED" in sql
    assert "tenant_id" in sql and "dispatch_after <=" in sql and "scheduled_at <=" in sql


@pytest.mark.parametrize("model", [OutboxMessage, OutboxTarget])
def test_model_ddl_has_tenant_fences_unique_keys_and_encryption(model):
    ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
    assert "tenant_id" in ddl and "UNIQUE" in ddl
    assert "FOREIGN KEY(tenant_id," in ddl
    if model is OutboxTarget:
        assert "ck_outbox_single_attempt" in ddl and "ck_outbox_claim_fence" in ddl


@pytest.mark.parametrize(
    "role,allowed", [("owner", True), ("admin", True), ("manager", False), ("analyst", False), ("viewer", False)]
)
@pytest.mark.asyncio
async def test_approval_explicitly_requires_configure(role, allowed):
    db = seeded()
    db.data[WorkspaceRole][0].codename = role
    session = Session(db)
    if allowed:
        await store.current_actor(session, store.ActionType.CONFIGURE)
    else:
        with pytest.raises(PermissionError):
            await store.current_actor(session, store.ActionType.CONFIGURE)


@pytest.mark.asyncio
async def test_bot_evidence_is_not_invented_from_numeric_identity():
    db = seeded()
    target(db).channel = "telegram"
    with pytest.raises(store.OutboxConflict, match="bot_evidence_unavailable"):
        await store.resolve_route(Session(db), db.data[OutboxMessage][0], target(db), NOW)


@pytest.mark.parametrize("code", list(store.CAPABILITIES.values()))
def test_capability_registry_is_default_off_and_configure_only(code):
    spec = get_capability(code)
    assert spec.required_resource == "outboxmessage" and spec.required_action == store.ActionType.CONFIGURE
    assert not spec.enabled_by_default


@pytest.mark.asyncio
async def test_generic_ledger_cannot_manufacture_transport_success():
    db = seeded()
    session = NS(execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: db.data[BusinessCommand][0])))
    with pytest.raises(ledger.CommandDenied, match="persistent outbox"):
        await ledger.BusinessCommandLedger().execute_command(1, session=session)


@pytest.mark.parametrize(
    "extra", [{"route_verified": True}, {"approved": True}, {"started_bot": True}, {"actor_id": 999}]
)
def test_untrusted_flags_are_rejected(extra):
    with pytest.raises(ValidationError):
        DraftIn.model_validate(
            {
                "contact_id": 4,
                "body": "synthetic",
                "targets": [{"channel": "email", "connection_id": 5, "identity_id": 11}],
                **extra,
            }
        )


def test_timezone_naive_schedule_and_duplicate_targets_rejected():
    with pytest.raises(ValidationError):
        ScheduleIn(message_id=1, expected_content_hash="sha256:" + "a" * 64, scheduled_at=datetime(2026, 11, 5))
    with pytest.raises(ValidationError):
        DraftIn(
            contact_id=4,
            body="synthetic",
            timezone="Not/AZone",
            targets=[{"channel": "email", "connection_id": 5, "identity_id": 11}],
        )
    target_input = {"channel": "email", "connection_id": 5, "identity_id": 11}
    with pytest.raises(ValidationError):
        DraftIn(contact_id=4, body="synthetic", targets=[target_input, target_input])


@pytest.mark.asyncio
async def test_run_counts_and_invalid_scope():
    db = seeded()
    dispatcher, sender = worker(db)
    result = await dispatcher.run(now=NOW)
    assert result["handled"] == 1 and sender.await_count == 1
    with pytest.raises(ValueError):
        await dispatcher.run(limit=True)
    with tenant_scope(None), pytest.raises(PermissionError):
        await dispatcher.run(now=NOW)
    with tenant_scope(7, bypass=True), pytest.raises(PermissionError):
        await dispatcher.run(now=NOW)


@pytest.mark.asyncio
async def test_draft_empty_subject_real_projection_idempotent_replay_and_conflict():
    db = seeded()
    db.data[OutboxMessage] = []
    db.data[OutboxTarget] = []
    session = Session(db)
    request = DraftIn(
        contact_id=4,
        body="Synthetic private body",
        targets=[{"channel": "email", "connection_id": 5, "identity_id": 11}],
    )
    service = store.OutboxStore()
    result = await service.draft(request, "synthetic-request", session=session)
    assert result["subject"] == "" and result["body"] == request.body
    MessageOut.model_validate(result)
    replay = await service.draft(request, "synthetic-request", session=session)
    assert replay["id"] == result["id"] and len(db.data[OutboxMessage]) == 1
    with pytest.raises(store.OutboxConflict, match="idempotency"):
        await service.draft(request.model_copy(update={"body": "different"}), "synthetic-request", session=session)
    with pytest.raises(ValueError):
        await service.draft(request, "invalid key", session=session)


@pytest.mark.asyncio
async def test_schedule_uses_real_ledger_hash_binding_and_never_renews_approval(monkeypatch):
    db = seeded()
    message = db.data[OutboxMessage][0]
    message.state = "validated"
    message.scheduled_at = message.approved_by = message.approved_hash = message.approval_expires_at = None
    target(db).state = "validated"
    target(db).command_id = None
    db.data[BusinessCommand] = []
    db.data[BusinessCommandTarget] = []

    async def execute(query):
        rows = Session(db).select(query)
        return NS(scalar_one_or_none=lambda: rows[0] if rows else None, scalars=lambda: NS(all=lambda: rows))

    session = Session(db)
    session.execute = execute
    monkeypatch.setattr(ledger.ActionAuditEvent.objects, "record_success", AsyncMock())
    requested = store.due_after_quiet_hours(datetime.now(UTC) + timedelta(minutes=2), "UTC")
    request = ScheduleIn(message_id=1, expected_content_hash=message.content_hash, scheduled_at=requested)
    service = store.OutboxStore()
    result = await service.schedule(request, session=session)
    assert result["state"] == "approved" and target(db).command_id is not None
    assert db.data[BusinessCommand][0].state == "approved"
    assert "Synthetic private body" not in repr(db.data[BusinessCommand][0].payload)
    expires = result["approval_expires_at"]
    result = await service.schedule(request, session=session)
    assert result["approval_expires_at"] == expires and len(db.data[BusinessCommand]) == 1
    MessageOut.model_validate(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["past", "too_far", "hash", "wrong_actor", "already_claimed"])
async def test_schedule_rejects_stale_or_changed_intent(mutation):
    db = seeded()
    message = db.data[OutboxMessage][0]
    message.state = "validated"
    target(db).state = "validated"
    scheduled = store.due_after_quiet_hours(datetime.now(UTC) + timedelta(minutes=2), "UTC")
    hashed = message.content_hash
    if mutation == "past":
        scheduled = NOW - timedelta(days=2)
    if mutation == "too_far":
        scheduled = datetime.now(UTC) + timedelta(days=2)
    if mutation == "hash":
        hashed = "sha256:" + "f" * 64
    if mutation == "wrong_actor":
        message.author_id = 999
    if mutation == "already_claimed":
        target(db).attempts = 1
    with pytest.raises(store.OutboxConflict):
        await store.OutboxStore().schedule(
            ScheduleIn(message_id=1, expected_content_hash=hashed, scheduled_at=scheduled), session=Session(db)
        )


@pytest.mark.asyncio
async def test_manager_update_can_cancel_but_not_approve():
    db = seeded()
    db.data[WorkspaceRole][0].codename = "manager"
    service = store.OutboxStore()
    with pytest.raises(PermissionError):
        await service.schedule(
            ScheduleIn(message_id=1, expected_content_hash=db.data[OutboxMessage][0].content_hash, scheduled_at=NOW),
            session=Session(db),
        )
    cancelled = await service.cancel(1, session=Session(db))
    assert cancelled["state"] == "cancelled" and target(db).state == "cancelled"
    assert db.data[BusinessCommand][0].state == "cancelled"
    dispatcher, sender = worker(db)
    assert await dispatcher.claim(NOW) is None and sender.await_count == 0


@pytest.mark.asyncio
async def test_cancel_after_claim_is_rejected_and_foreign_item_is_opaque():
    db = seeded()
    dispatcher, _ = worker(db)
    await dispatcher.claim(NOW)
    with pytest.raises(store.OutboxConflict):
        await store.OutboxStore().cancel(1, session=Session(db))
    with tenant_scope(99), pytest.raises(store.OutboxUnavailable):
        await store.owned_message(Session(db), 99, 1, lock=True)


@pytest.mark.asyncio
async def test_get_page_and_no_actor_rejection():
    db = seeded()
    service = store.OutboxStore()
    assert (await service.get(1, session=Session(db)))["id"] == 1
    assert len((await service.scheduled(session=Session(db)))["items"]) == 1
    assert not (await service.scheduled(before_id=1, session=Session(db)))["items"]
    with pytest.raises(ValueError):
        await service.scheduled(limit=True, session=Session(db))
    with permission_scope(None), pytest.raises(PermissionError):
        await service.get(1, session=Session(db))


@pytest.mark.asyncio
async def test_api_exact_routes_explicit_configure_and_safe_errors(monkeypatch):
    from fastapi import FastAPI

    from app.api.v1.conventions import require_business_scope
    from app.api.v1.endpoints import outbox as api

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    # Replace actual permission/middleware boundaries; service tests above still
    # exercise real fresh-role decisions. No real authentication/DB here.
    for route in api.router.routes:
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = lambda: None
    app.dependency_overrides[require_business_scope] = lambda: None
    monkeypatch.setattr(api.outbox_store, "get", AsyncMock(side_effect=store.OutboxUnavailable("secret marker")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.get("/api/v1/communication/outbox/1")
    assert response.status_code == 404 and "secret marker" not in response.text
    paths = {route.path for route in api.router.routes}
    assert paths == {
        "/communication/outbox/draft",
        "/communication/outbox/schedule",
        "/communication/outbox/scheduled",
        "/communication/outbox/scheduled/{message_id}",
        "/communication/outbox/{message_id}",
    }
    schedule_route = next(r for r in api.router.routes if r.path.endswith("/schedule"))
    # Permission dependency closure is produced by require_model_perm.
    action_values = [
        cell.cell_contents
        for d in schedule_route.dependencies
        for cell in (getattr(d.dependency, "__closure__", None) or ())
    ]
    assert store.ActionType.CONFIGURE in action_values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception,status",
    [
        (store.OutboxConflict("marker"), 409),
        (store.OutboxUnavailable("marker"), 404),
        (PermissionError("marker"), 403),
        (ValueError("marker"), 422),
    ],
)
async def test_api_error_mapping(exception, status):
    from fastapi import HTTPException

    from app.api.v1.endpoints.outbox import invoke

    async def fail():
        raise exception

    with pytest.raises(HTTPException) as caught:
        await invoke(fail())
    assert caught.value.status_code == status and "marker" not in str(caught.value.detail)


@pytest.mark.parametrize(
    "enabled,killed,expected",
    [("0", "0", False), ("1", "0", True), ("1", "1", False), ("true", "0", False), ("1", "invalid", False)],
)
def test_runtime_transport_activation_is_default_off_and_uses_operator_switches(monkeypatch, enabled, killed, expected):
    monkeypatch.setattr(ledger, "RUNTIME_ENABLED_CAPABILITIES", set())
    monkeypatch.setenv("P15_EMAIL_SEND_ENABLED", enabled)
    monkeypatch.setenv("P15_EMAIL_SEND_KILL", killed)
    assert ledger._is_capability_enabled(get_capability(EMAIL)) is expected


@pytest.mark.asyncio
async def test_lost_ticket_consumption_commit_never_calls_provider():
    db = seeded()
    dispatcher, sender = worker(db)
    ticket = await dispatcher.claim(NOW)
    db.fail_commit = 2
    with pytest.raises(RuntimeError, match="commit"):
        await dispatcher.deliver(ticket, NOW)
    assert sender.await_count == 0 and target(db).delivery_started_at is None


@pytest.mark.asyncio
async def test_duplicate_ticket_after_cancelled_sender_is_fenced_before_recovery():
    db = seeded()
    dispatcher, sender = worker(db)
    ticket = await dispatcher.claim(NOW)
    sender.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await dispatcher.deliver(ticket, NOW)
    assert target(db).delivery_started_at == NOW
    assert not await dispatcher.deliver(ticket, NOW)
    assert sender.await_count == 1


@pytest.mark.asyncio
async def test_foreign_tenant_ticket_cannot_reach_provider():
    db = seeded()
    dispatcher, sender = worker(db)
    ticket = await dispatcher.claim(NOW)
    with tenant_scope(99), pytest.raises(store.OutboxUnavailable):
        await dispatcher.deliver(ticket, NOW)
    assert sender.await_count == 0


@pytest.mark.asyncio
async def test_outbox_receipt_fits_central_ledger_column():
    db = seeded()
    dispatcher, sender = worker(db, SendResult("sent", "smtp_accepted", receipt_id="r" * 250))
    await dispatcher.deliver(await dispatcher.claim(NOW), NOW)
    assert len(target(db).provider_message_id) == 250
    assert len(db.data[BusinessCommandTarget][0].provider_receipt) == 160
    assert sender.await_count == 1
