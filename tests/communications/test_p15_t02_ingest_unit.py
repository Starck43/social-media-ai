"""DB-exempt unit and contract tests for P15-T02 unified message model, migration 0137, and ingestion."""

from __future__ import annotations

import runpy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.tenant_context import TenantContextError, tenant_scope
from app.models.communications import ContactChannelStat, MessageThread, UnifiedMessage
from app.models.contacts import Contact, ContactIdentity
from app.models.integrations import PersonalConnectorAuthorization, WorkspaceAuthorizationGrant, WorkspaceConnection
from app.models.managers.unified_message_manager import (
    ContactChannelStatManager,
    MessageThreadManager,
    UnifiedMessageManager,
)
from app.services.communications.ingest import (
    CommunicationIngestError,
    IngestMessageInput,
    PersonalConnectorDisabledError,
    compute_content_hash,
    derive_thread_key,
    extract_reference_ids,
    hash_message_id,
    ingest,
    ingest_max_update,
    ingest_telegram_update,
    mark_message_unavailable,
    normalize_message_id,
    normalize_subject_for_thread,
    prune_expired_messages,
)
from cli.commands.pii import REGISTRY
from cryptography.fernet import Fernet
import base64
from app.core.config import settings

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _pii_keys(monkeypatch):
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode(), raising=False)
    monkeypatch.setattr(settings, "PII_INDEX_KEY", base64.urlsafe_b64encode(b"u" * 32).decode(), raising=False)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode(), raising=False)



class _FakeResult:
    def __init__(self, rows: list[Any], rowcount: int = 0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return SimpleNamespace(
            all=lambda: list(self._rows),
            first=lambda: self._rows[0] if self._rows else None,
        )

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _InMemoryCommsSession:
    """In-memory async session stub for deterministic P15-T02 ingestion tests without PostgreSQL."""

    def __init__(self):
        self.connections: list[WorkspaceConnection] = []
        self.grants: list[tuple[WorkspaceAuthorizationGrant, PersonalConnectorAuthorization]] = []
        self.contacts: list[Contact] = []
        self.identities: list[ContactIdentity] = []
        self.threads: list[MessageThread] = []
        self.messages: list[UnifiedMessage] = []
        self.stats: list[ContactChannelStat] = []
        self._next_id = 100

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            self._next_id += 1
            obj.id = self._next_id
        if isinstance(obj, WorkspaceConnection) and obj not in self.connections:
            self.connections.append(obj)
        elif isinstance(obj, Contact) and obj not in self.contacts:
            self.contacts.append(obj)
        elif isinstance(obj, ContactIdentity) and obj not in self.identities:
            self.identities.append(obj)
        elif isinstance(obj, MessageThread) and obj not in self.threads:
            self.threads.append(obj)
        elif isinstance(obj, UnifiedMessage) and obj not in self.messages:
            self.messages.append(obj)
        elif isinstance(obj, ContactChannelStat) and obj not in self.stats:
            self.stats.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = str(stmt)
        params = stmt.compile().params if hasattr(stmt, "compile") else {}

        if "DELETE FROM" in sql and "unified_messages" in sql:
            cutoff = next((v for v in params.values() if isinstance(v, datetime)), None)
            tenant_id = next((v for k, v in params.items() if "tenant_id" in k), None)
            before = len(self.messages)
            self.messages = [
                m
                for m in self.messages
                if not (m.tenant_id == tenant_id and cutoff is not None and m.received_at < cutoff)
            ]
            return _FakeResult([], rowcount=before - len(self.messages))

        if "workspace_authorization_grants" in sql and "personal_connector_authorizations" in sql:
            t_id = params.get("tenant_id_1")
            auth_id = params.get("authorization_id_1")
            matches = [
                pair
                for pair in self.grants
                if pair[0].tenant_id == t_id
                and pair[0].authorization_id == auth_id
                and pair[0].is_active
                and pair[1].state == "active"
                and pair[0].authorization_revision == pair[1].revision
            ]
            return _FakeResult(matches)

        if "FROM" in sql and "workspace_connections" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("id_1")
            matches = [c for c in self.connections if c.tenant_id == t_id and c.id == c_id]
            return _FakeResult(matches)

        if "FROM" in sql and "contact_identities" in sql:
            t_id = params.get("tenant_id_1")
            kind = params.get("kind_1")
            b_idx = params.get("blind_index_1")
            conn_id = params.get("connection_id_1")
            matches = [
                ident
                for ident in self.identities
                if ident.tenant_id == t_id
                and ident.kind == kind
                and ident.blind_index == b_idx
                and ident.connection_id == conn_id
            ]
            return _FakeResult(matches)

        if "FROM" in sql and "contacts" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("id_1")
            if c_id is not None:
                matches = [c for c in self.contacts if c.tenant_id == t_id and c.id == c_id]
                return _FakeResult(matches)
            pairs = [(c.id, c.merged_into_id) for c in self.contacts if c.tenant_id == t_id]
            return _FakeResult(pairs)

        if "FROM" in sql and "message_threads" in sql:
            t_id = params.get("tenant_id_1")
            th_id = params.get("id_1")
            if th_id is not None:
                return _FakeResult([t for t in self.threads if t.tenant_id == t_id and t.id == th_id])
            conn_id = params.get("connection_id_1")
            channel = params.get("channel_1")
            t_key = params.get("thread_key_1")
            return _FakeResult(
                [
                    t
                    for t in self.threads
                    if t.tenant_id == t_id
                    and t.connection_id == conn_id
                    and t.channel == channel
                    and t.thread_key == t_key
                ]
            )

        if "FROM" in sql and "unified_messages" in sql:
            t_id = params.get("tenant_id_1")
            conn_id = params.get("connection_id_1")
            channel = params.get("channel_1")
            orig_id = params.get("original_id_1")
            ext_hash = params.get("external_message_id_hash_1")
            c_hash = params.get("content_hash_1")
            if orig_id is not None:
                return _FakeResult(
                    [
                        m
                        for m in self.messages
                        if m.tenant_id == t_id
                        and m.connection_id == conn_id
                        and m.channel == channel
                        and m.original_id == orig_id
                    ]
                )
            if ext_hash is not None:
                return _FakeResult(
                    [
                        m
                        for m in self.messages
                        if m.tenant_id == t_id
                        and m.connection_id == conn_id
                        and m.channel == channel
                        and m.external_message_id_hash == ext_hash
                    ]
                )
            if c_hash is not None:
                return _FakeResult(
                    [
                        m
                        for m in self.messages
                        if m.tenant_id == t_id
                        and m.connection_id == conn_id
                        and m.channel == channel
                        and m.content_hash == c_hash
                    ]
                )

        if "FROM" in sql and "contact_channel_stats" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            channel = params.get("channel_1")
            return _FakeResult(
                [
                    s
                    for s in self.stats
                    if s.tenant_id == t_id and s.contact_id == c_id and s.channel == channel
                ]
            )

        return _FakeResult([])


def test_migration_0139_and_pii_registry_contracts(monkeypatch):
    migration = runpy.run_path(str(ROOT / "migrations/versions/0139_unified_messages.py"))
    assert migration["revision"] == "0139"
    assert migration["down_revision"] == "0138"

    source_text = (ROOT / "migrations/versions/0139_unified_messages.py").read_text(encoding="utf-8")
    assert "message_content_cache" not in source_text
    assert "tsvector" not in source_text
    assert "workspace_role_permission" in source_text
    assert "WORKSPACE_ROLE_PRESETS" in source_text

    registered = {(tbl, col) for tbl, col, _ in REGISTRY}
    assert ("message_threads", "subject_encrypted") in registered
    assert ("message_threads", "participants_encrypted") in registered
    assert ("unified_messages", "subject_encrypted") in registered
    assert ("unified_messages", "sender_encrypted") in registered
    assert ("unified_messages", "recipient_encrypted") in registered

    created_tables: list[str] = []
    dropped_tables: list[str] = []
    executed_sql: list[str] = []
    op = migration["op"]
    monkeypatch.setattr(op, "create_table", lambda name, *a, **kw: created_tables.append(name))
    monkeypatch.setattr(op, "create_index", lambda *a, **kw: None)
    monkeypatch.setattr(op, "drop_table", lambda name, *a, **kw: dropped_tables.append(name))
    monkeypatch.setattr(op, "execute", lambda sql, *a, **kw: executed_sql.append(str(sql)))
    monkeypatch.setattr(
        op,
        "get_bind",
        lambda: SimpleNamespace(
            dialect=SimpleNamespace(identifier_preparer=SimpleNamespace(quote_schema=lambda s: s)),
            execute=lambda *a, **kw: None,
        ),
    )
    migration["upgrade"]()
    assert created_tables == ["message_threads", "unified_messages", "contact_channel_stats"]
    migration["downgrade"]()
    assert dropped_tables == ["contact_channel_stats", "unified_messages", "message_threads"]


def test_managers_require_active_workspace_context():
    from app.core.access_catalog import resource_for, workspace_grants
    from app.core.permissions import permission_scope
    from app.models.managers.unified_message_manager import require_message_permission
    from app.types import ActionType

    res = resource_for("comms.message")
    assert res is not None
    assert res.table_name == "unified_messages"
    assert res.actions == frozenset({ActionType.VIEW, ActionType.CREATE, ActionType.UPDATE, ActionType.DELETE})
    assert ("unifiedmessage", ActionType.DELETE) in workspace_grants("owner")
    assert ("unifiedmessage", ActionType.UPDATE) in workspace_grants("manager")
    assert ("unifiedmessage", ActionType.DELETE) not in workspace_grants("manager")
    assert ("unifiedmessage", ActionType.VIEW) in workspace_grants("viewer")
    assert ("unifiedmessage", ActionType.UPDATE) not in workspace_grants("viewer")

    with pytest.raises(TenantContextError):
        UnifiedMessageManager().get_queryset()
    with pytest.raises(TenantContextError):
        MessageThreadManager().get_queryset()
    with pytest.raises(TenantContextError):
        ContactChannelStatManager().get_queryset()

    actor = SimpleNamespace(id=5, is_active=True)
    with tenant_scope(1), permission_scope(actor, member_grants=workspace_grants("viewer")):
        assert require_message_permission(ActionType.VIEW) == 1
        with pytest.raises(PermissionError):
            require_message_permission(ActionType.UPDATE)


def test_message_id_and_subject_normalization_helpers():
    assert normalize_message_id("  <ABC-123@Example.COM> ") == "abc-123@example.com"
    assert normalize_message_id("") is None
    assert hash_message_id("<ABC-123@Example.COM>") == hash_message_id("abc-123@example.com")
    assert hash_message_id(None) is None
    assert extract_reference_ids("<root@ex.com> <child@ex.com> <root@ex.com>") == (
        "root@ex.com",
        "child@ex.com",
    )
    assert extract_reference_ids(["<r1@ex.com>", "r2@ex.com"]) == ("r1@ex.com", "r2@ex.com")
    assert normalize_subject_for_thread("Re: Fwd: [2]:  Quarterly   Report ") == "quarterly report"
    assert compute_content_hash("hello world").startswith("sha256:")
    assert compute_content_hash(None) is None


@pytest.mark.asyncio
async def test_idempotent_ingestion_10_times_produces_one_row_and_accurate_stats():
    session = _InMemoryCommsSession()
    session.add(
        WorkspaceConnection(
            id=10,
            tenant_id=1,
            owner_user_id=5,
            platform="email",
            provider_id="yandex_mail",
            auth_method="app_password",
            schema_version=1,
            label="Work Mail",
            account_key="alice@yandex.ru",
            config={"imap_host": "imap.yandex.ru"},
            is_active=True,
            state="verified",
        )
    )
    now = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    envelope = IngestMessageInput(
        connection_id=10,
        channel="email",
        original_id="INBOX:42",
        direction="inbound",
        sent_at=now,
        received_at=now,
        subject="Re: Contract Proposal",
        sender="Bob Client <Bob@Example.com>",
        recipient="alice@yandex.ru",
        participants=("Bob@Example.com", "alice@yandex.ru"),
        body_for_hash_only="Secret body that must only be hashed, never stored",
        external_message_id="<msg-42@example.com>",
        in_reply_to="<root-1@example.com>",
        references=("<root-1@example.com>",),
    )

    with tenant_scope(1):
        results = [await ingest(session, envelope) for _ in range(10)]

    assert results[0].created is True
    assert all(r.created is False for r in results[1:])
    assert len(session.messages) == 1
    assert len(session.threads) == 1
    assert len(session.contacts) == 1
    assert len(session.stats) == 1

    msg = session.messages[0]
    assert msg.original_id == "INBOX:42"
    assert msg.content_hash == compute_content_hash("Secret body that must only be hashed, never stored")
    assert not hasattr(msg, "body")
    assert msg.is_unavailable is False

    thread = session.threads[0]
    assert thread.message_count == 1
    assert thread.unread_count == 1
    assert thread.thread_key == "email:msgid:root-1@example.com"
    assert "Bob@Example.com" in thread.participants

    stat = session.stats[0]
    assert stat.inbound_count == 1
    assert stat.outbound_count == 0
    assert stat.last_inbound_at == now


@pytest.mark.asyncio
async def test_email_threading_and_uidvalidity_reset_rebind_by_message_id_hash():
    session = _InMemoryCommsSession()
    session.add(
        WorkspaceConnection(
            id=11,
            tenant_id=1,
            owner_user_id=5,
            platform="email",
            provider_id="yandex_mail",
            auth_method="app_password",
            schema_version=1,
            label="Mail",
            account_key="team@yandex.ru",
            config={},
            is_active=True,
            state="verified",
        )
    )
    t1 = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 4, 9, 15, tzinfo=timezone.utc)

    with tenant_scope(1):
        first = await ingest(
            session,
            IngestMessageInput(
                connection_id=11,
                channel="email",
                original_id="100:1",
                direction="inbound",
                sent_at=t1,
                subject="Initial inquiry",
                sender="customer@example.org",
                recipient="team@yandex.ru",
                external_message_id="<root-thread@example.org>",
                body_for_hash_only="first email",
            ),
        )
        reply = await ingest(
            session,
            IngestMessageInput(
                connection_id=11,
                channel="email",
                original_id="100:2",
                direction="outbound",
                sent_at=t2,
                subject="Re: Initial inquiry",
                sender="team@yandex.ru",
                recipient="customer@example.org",
                external_message_id="<reply-2@example.org>",
                in_reply_to="<root-thread@example.org>",
                references=("<root-thread@example.org>",),
                body_for_hash_only="reply email",
            ),
        )
        # Simulate UIDVALIDITY reset from 100 -> 200 where message 1 gets new UID 501
        rebound = await ingest(
            session,
            IngestMessageInput(
                connection_id=11,
                channel="email",
                original_id="200:501",
                direction="inbound",
                sent_at=t1,
                subject="Initial inquiry",
                sender="customer@example.org",
                recipient="team@yandex.ru",
                external_message_id="<root-thread@example.org>",
                body_for_hash_only="first email",
                dedupe_by_external_message_id_hash=True,
            ),
        )

    assert first.thread.id == reply.thread.id
    assert first.contact_id == reply.contact_id
    assert first.thread.message_count == 2
    assert first.thread.unread_count == 1
    assert rebound.created is False
    assert rebound.uid_rebound is True
    assert first.message.original_id == "200:501"
    assert len(session.messages) == 2
    assert session.stats[0].inbound_count == 1
    assert session.stats[0].outbound_count == 1


@pytest.mark.asyncio
async def test_telegram_and_max_ingestion_and_personal_telegram_hard_off():
    session = _InMemoryCommsSession()
    # 1. Bot Telegram connection (allowed)
    session.add(
        WorkspaceConnection(
            id=20,
            tenant_id=1,
            owner_user_id=5,
            platform="telegram",
            provider_id="telegram_bot",
            auth_method="bot_token",
            schema_version=1,
            label="Support Bot",
            account_key="bot:123",
            config={},
            is_active=True,
            state="verified",
        )
    )
    # 2. Personal Telegram connection (hard-off by default)
    session.add(
        WorkspaceConnection(
            id=21,
            tenant_id=1,
            owner_user_id=5,
            platform="telegram",
            provider_id="telegram_personal",
            auth_method="personal_telegram",
            schema_version=1,
            label="Personal TG",
            account_key="tg:user99",
            config={"connector_mode": "personal"},
            is_active=True,
            state="verified",
        )
    )
    # 3. MAX Bot connection
    session.add(
        WorkspaceConnection(
            id=22,
            tenant_id=1,
            owner_user_id=5,
            platform="max",
            provider_id="max_bot",
            auth_method="bot_token",
            schema_version=1,
            label="MAX Bot",
            account_key="max:1",
            config={},
            is_active=True,
            state="verified",
        )
    )

    tg_update = {
        "update_id": 9001,
        "message": {
            "message_id": 77,
            "date": 1730714400,
            "chat": {"id": -100123456, "title": "VIP Support"},
            "from": {"id": 555001, "username": "vip_user", "first_name": "Ivan", "last_name": "Petrov"},
            "text": "Hello from Telegram",
        },
    }
    max_update = {
        "update_type": "message_created",
        "message": {
            "timestamp": 1730714500000,
            "sender": {"user_id": "88001", "name": "Maria S"},
            "recipient": {"chat_id": "max_c_10", "title": "MAX Sales"},
            "body": {"mid": "mid_999", "text": "Hello from MAX"},
        },
    }

    with tenant_scope(1):
        tg_res = await ingest_telegram_update(session, connection_id=20, update=tg_update)
        assert tg_res is not None
        assert tg_res.created is True
        assert tg_res.message.channel == "telegram"
        assert tg_res.message.original_url == "https://t.me/c/123456/77"

        with pytest.raises(PersonalConnectorDisabledError):
            await ingest_telegram_update(session, connection_id=21, update=tg_update)

        max_res = await ingest_max_update(session, connection_id=22, update=max_update)
        assert max_res is not None
        assert max_res.created is True
        assert max_res.message.channel == "max"
        assert max_res.thread.thread_key == "max:chat:max_c_10"


@pytest.mark.asyncio
async def test_merged_contact_resolution_and_unavailable_and_retention():
    session = _InMemoryCommsSession()
    session.add(
        WorkspaceConnection(
            id=30,
            tenant_id=1,
            owner_user_id=5,
            platform="email",
            provider_id="yandex_mail",
            auth_method="app_password",
            schema_version=1,
            label="Mail",
            account_key="info@yandex.ru",
            config={},
            is_active=True,
            state="verified",
        )
    )
    # Contact 1 merged into Contact 2
    c1 = Contact(id=1, tenant_id=1, display_name_encrypted="Old Record", merged_into_id=2)
    c2 = Contact(id=2, tenant_id=1, display_name_encrypted="Survivor Record", merged_into_id=None)
    session.add(c1)
    session.add(c2)
    from app.services.contacts.identity import identity_index

    b_idx = identity_index("email", "merged@example.com")
    session.add(
        ContactIdentity(
            id=50,
            tenant_id=1,
            contact_id=1,
            kind="email",
            connection_id=30,
            value_encrypted="merged@example.com",
            blind_index=b_idx,
            verified=True,
        )
    )

    old_time = datetime.now(timezone.utc) - timedelta(days=120)
    with tenant_scope(1):
        res = await ingest(
            session,
            IngestMessageInput(
                connection_id=30,
                channel="email",
                original_id="INBOX:999",
                direction="inbound",
                sent_at=old_time,
                received_at=old_time,
                subject="Subject Only",
                sender="merged@example.com",
                recipient="info@yandex.ru",
            ),
        )
        assert res.contact_id == 2

        marked = await mark_message_unavailable(
            session,
            connection_id=30,
            channel="email",
            original_id="INBOX:999",
        )
        assert marked is True
        assert res.message.is_unavailable is True

        pruned = await prune_expired_messages(session, retention_days=90)
        assert pruned == 1
        assert len(session.messages) == 0


def test_validation_and_thread_key_fallbacks():
    now = datetime.now(timezone.utc)
    subj_only = IngestMessageInput(
        connection_id=1,
        channel="email",
        original_id="1",
        direction="inbound",
        sent_at=now,
        subject="Re: Budget Review",
    )
    assert derive_thread_key(subj_only).startswith("email:subj:")

    orig_only = IngestMessageInput(
        connection_id=1,
        channel="email",
        original_id="uid-77",
        direction="inbound",
        sent_at=now,
    )
    assert derive_thread_key(orig_only) == "email:orig:uid-77"

    with pytest.raises(CommunicationIngestError):
        from app.services.communications.ingest import _validate_input

        _validate_input(
            IngestMessageInput(
                connection_id=1,
                channel="email",
                original_id="1",
                direction="inbound",
                sent_at=now,
                content_hash="md5:invalid",
            )
        )


def test_communications_outbox_and_followups_contracts():
    from app.services.communications.followups import (
        adjust_for_quiet_hours,
        confirm_followup_date,
        create_followup_item,
        detect_imprecise_date,
        dismiss_followup,
        is_in_quiet_hours,
        snooze_followup,
    )
    from app.services.communications.outbox import (
        ApprovalExpiredError,
        InvalidApprovalStateError,
        RouteNotVerifiedError,
        approve_outbox_item,
        can_retry_blindly,
        cancel_outbox_item,
        claim_outbox_item,
        confirm_delivery,
        create_outbox_item,
        timeout_delivery,
        update_message_body,
        validate_route,
    )

    assert detect_imprecise_date("Созвонимся в начале следующей недели") is True
    assert detect_imprecise_date("2026-11-05 14:00") is False
    late = datetime(2026, 11, 4, 22, 30, tzinfo=timezone.utc)
    early = datetime(2026, 11, 4, 5, 15, tzinfo=timezone.utc)
    day = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    assert is_in_quiet_hours(late) is True
    assert is_in_quiet_hours(early) is True
    assert is_in_quiet_hours(day) is False
    assert adjust_for_quiet_hours(late).hour == 8
    assert adjust_for_quiet_hours(early).hour == 8
    assert adjust_for_quiet_hours(day) == day

    f_item = create_followup_item("f1", "Acme", "в начале следующей недели", "msg:1", "soon")
    assert f_item["requires_date_confirmation"] is True
    f_item = confirm_followup_date(f_item, "2026-11-10T10:00:00Z", "u1")
    f_item = snooze_followup(f_item, "2026-11-11T10:00:00Z", "u1")
    f_item = dismiss_followup(f_item, "u1")
    assert f_item["status"] == "dismissed"

    item = create_outbox_item("o1", "email", "Bob", "bob@example.com", "Subj", "Body", route_verified=False)
    with pytest.raises(RouteNotVerifiedError):
        approve_outbox_item(item, "u1", "2026-11-05T12:00:00Z")

    item = validate_route(item, True)
    approved = approve_outbox_item(item, "u1", "2026-11-05T12:00:00Z")
    edited = update_message_body(approved, "New Body")
    assert edited["bound_actor"] is None

    approved2 = approve_outbox_item(edited, "u1", "2026-11-05T12:00:00Z")
    with pytest.raises(ApprovalExpiredError):
        claim_outbox_item(approved2, current_time_iso="2026-11-06T12:00:00Z")

    claimed = claim_outbox_item(approved2, current_time_iso="2026-11-05T10:00:00Z")
    timed_out = timeout_delivery(claimed, "read timeout")
    assert can_retry_blindly(timed_out) is False

    claimed2 = claim_outbox_item(approve_outbox_item(edited, "u1", "2026-11-05T12:00:00Z"))
    sent = confirm_delivery(claimed2, provider_ack="ack-1")
    with pytest.raises(InvalidApprovalStateError):
        cancel_outbox_item(sent, "u1")
    cancelled = cancel_outbox_item(edited, "u1")
    assert cancelled["state"] == "cancelled"


@pytest.mark.asyncio
async def test_b8_no_cross_connection_or_null_connection_contact_auto_link():
    """B-8: Ingest must never auto-link a ContactIdentity from connection_id=None or another connection."""
    session = _InMemoryCommsSession()
    session.add(
        WorkspaceConnection(
            id=41,
            tenant_id=1,
            owner_user_id=7,
            platform="email",
            provider_id="yandex_mail",
            auth_method="app_password",
            schema_version=1,
            label="Mailbox 1",
            account_key="m1@yandex.ru",
            config={},
            is_active=True,
            state="verified",
        )
    )
    session.add(
        WorkspaceConnection(
            id=42,
            tenant_id=1,
            owner_user_id=7,
            platform="email",
            provider_id="yandex_mail",
            auth_method="app_password",
            schema_version=1,
            label="Mailbox 2",
            account_key="m2@yandex.ru",
            config={},
            is_active=True,
            state="verified",
        )
    )

    from app.services.contacts.identity import identity_index

    b_idx = identity_index("email", "shared@client.ru")
    # Pre-existing manual contact with connection_id=None and another contact on connection_id=41
    c_null = Contact(id=10, tenant_id=1, display_name_encrypted="Manual Contact")
    c_conn41 = Contact(id=11, tenant_id=1, display_name_encrypted="Conn 41 Contact")
    session.add(c_null)
    session.add(c_conn41)
    session.add(
        ContactIdentity(
            id=210,
            tenant_id=1,
            contact_id=10,
            kind="email",
            connection_id=None,
            value_encrypted="shared@client.ru",
            blind_index=b_idx,
            verified=False,
        )
    )
    session.add(
        ContactIdentity(
            id=211,
            tenant_id=1,
            contact_id=11,
            kind="email",
            connection_id=41,
            value_encrypted="shared@client.ru",
            blind_index=b_idx,
            verified=False,
        )
    )

    now = datetime.now(timezone.utc)
    with tenant_scope(1):
        # Ingesting on connection 42 must NOT auto-link to c_null (10) or c_conn41 (11)
        res = await ingest(
            session,
            IngestMessageInput(
                connection_id=42,
                channel="email",
                original_id="INBOX:1:1",
                direction="inbound",
                sent_at=now,
                subject="Hello",
                sender="shared@client.ru",
                recipient="m2@yandex.ru",
            ),
        )
        assert res.contact_id not in (10, 11)
        new_ident = next(i for i in session.identities if i.contact_id == res.contact_id)
        assert new_ident.connection_id == 42


@pytest.mark.asyncio
async def test_b2_b3_n2_b6_duplicate_message_id_null_message_id_read_sync_and_deletions():
    """B-2, B-3, B-6, N-2: Multi-row Message-ID rebind, NULL Message-ID header hash fallback, read sync, and TG/MAX delete."""
    session = _InMemoryCommsSession()
    session.add(
        WorkspaceConnection(
            id=50,
            tenant_id=1,
            owner_user_id=7,
            platform="email",
            provider_id="yandex_mail",
            auth_method="app_password",
            schema_version=1,
            label="Yandex",
            account_key="team@yandex.ru",
            config={},
            is_active=True,
            state="verified",
        )
    )
    session.add(
        WorkspaceConnection(
            id=51,
            tenant_id=1,
            owner_user_id=7,
            platform="telegram",
            provider_id="telegram_bot",
            auth_method="bot_token",
            schema_version=1,
            label="TG Bot",
            account_key="bot51",
            config={},
            is_active=True,
            state="verified",
        )
    )
    session.add(
        WorkspaceConnection(
            id=52,
            tenant_id=1,
            owner_user_id=7,
            platform="max",
            provider_id="max_bot",
            auth_method="bot_token",
            schema_version=1,
            label="MAX Bot",
            account_key="max52",
            config={},
            is_active=True,
            state="verified",
        )
    )

    now = datetime.now(timezone.utc)
    header_hash_no_msgid = compute_content_hash(b"From: a@b.ru\r\nSubject: No MsgID\r\n\r\n")

    with tenant_scope(1):
        # 1. Two messages sharing the same Message-ID in INBOX (normal sync: dedupe=False)
        r1 = await ingest(
            session,
            IngestMessageInput(
                connection_id=50,
                channel="email",
                original_id="INBOX:10:1",
                direction="inbound",
                sent_at=now,
                subject="Shared ID 1",
                sender="a@b.ru",
                recipient="team@yandex.ru",
                external_message_id="<dup-id@b.ru>",
                is_read=False,
                dedupe_by_external_message_id_hash=False,
            ),
        )
        r2 = await ingest(
            session,
            IngestMessageInput(
                connection_id=50,
                channel="email",
                original_id="INBOX:10:2",
                direction="inbound",
                sent_at=now,
                subject="Shared ID 2",
                sender="a@b.ru",
                recipient="team@yandex.ru",
                external_message_id="<dup-id@b.ru>",
                is_read=False,
                dedupe_by_external_message_id_hash=False,
            ),
        )
        assert r1.created is True and r2.created is True
        assert r1.thread.unread_count == 2

        # N-2: Re-ingesting r1 with is_read=True updates is_read and decrements unread_count
        r1_seen = await ingest(
            session,
            IngestMessageInput(
                connection_id=50,
                channel="email",
                original_id="INBOX:10:1",
                direction="inbound",
                sent_at=now,
                subject="Shared ID 1",
                sender="a@b.ru",
                recipient="team@yandex.ru",
                external_message_id="<dup-id@b.ru>",
                is_read=True,
                dedupe_by_external_message_id_hash=False,
            ),
        )
        assert r1_seen.created is False
        assert r1.message.is_read is True
        assert r1.thread.unread_count == 1

        # B-2: UIDVALIDITY reset (10 -> 20) re-binds both rows sharing <dup-id@b.ru> without MultipleResultsFound
        reb1 = await ingest(
            session,
            IngestMessageInput(
                connection_id=50,
                channel="email",
                original_id="INBOX:20:101",
                direction="inbound",
                sent_at=now,
                subject="Shared ID 1",
                sender="a@b.ru",
                recipient="team@yandex.ru",
                external_message_id="<dup-id@b.ru>",
                is_read=True,
                dedupe_by_external_message_id_hash=True,
            ),
        )
        reb2 = await ingest(
            session,
            IngestMessageInput(
                connection_id=50,
                channel="email",
                original_id="INBOX:20:102",
                direction="inbound",
                sent_at=now,
                subject="Shared ID 2",
                sender="a@b.ru",
                recipient="team@yandex.ru",
                external_message_id="<dup-id@b.ru>",
                is_read=True,
                dedupe_by_external_message_id_hash=True,
            ),
        )
        assert reb1.uid_rebound is True and reb2.uid_rebound is True
        assert {r1.message.original_id, r2.message.original_id} == {"INBOX:20:101", "INBOX:20:102"}

        # B-3: Message with NULL Message-ID re-binds via content_hash scoped to same folder on reset
        no_id_init = await ingest(
            session,
            IngestMessageInput(
                connection_id=50,
                channel="email",
                original_id="INBOX:20:105",
                direction="inbound",
                sent_at=now,
                subject="No MsgID",
                sender="a@b.ru",
                recipient="team@yandex.ru",
                external_message_id=None,
                content_hash=header_hash_no_msgid,
                dedupe_by_external_message_id_hash=False,
            ),
        )
        assert no_id_init.created is True
        no_id_reset = await ingest(
            session,
            IngestMessageInput(
                connection_id=50,
                channel="email",
                original_id="INBOX:30:205",
                direction="inbound",
                sent_at=now,
                subject="No MsgID",
                sender="a@b.ru",
                recipient="team@yandex.ru",
                external_message_id=None,
                content_hash=header_hash_no_msgid,
                dedupe_by_external_message_id_hash=True,
            ),
        )
        assert no_id_reset.created is False
        assert no_id_reset.uid_rebound is True
        assert no_id_init.message.original_id == "INBOX:30:205"

        # B-6: Telegram and MAX deletion events mark unavailable_at
        tg_res = await ingest_telegram_update(
            session,
            connection_id=51,
            update={"message": {"message_id": 77, "date": 1762250000, "chat": {"id": 900}, "text": "hi"}},
        )
        assert tg_res is not None and tg_res.message.is_unavailable is False
        await ingest_telegram_update(
            session,
            connection_id=51,
            update={"deleted_message": {"message_id": 77, "chat": {"id": 900}}},
        )
        assert tg_res.message.is_unavailable is True

        max_res = await ingest_max_update(
            session,
            connection_id=52,
            update={"message": {"body": {"mid": "m-99", "text": "yo"}, "recipient": {"chat_id": 800}}},
        )
        assert max_res is not None and max_res.message.is_unavailable is False
        await ingest_max_update(
            session,
            connection_id=52,
            update={"update_type": "message_removed", "message_id": "m-99", "chat_id": 800},
        )
        assert max_res.message.is_unavailable is True


