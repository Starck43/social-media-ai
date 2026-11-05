"""Unit and contract tests for P15-T04 unified inbox service, API endpoints, and web routes (`__source_only__ = True`)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.conventions import validate_business_routes
from app.api.v1.endpoints import communication as communication_api
from app.core.access_catalog import workspace_grants
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models.communications.message import (
    ContactChannelStat,
    MessageThread,
    UnifiedMessage,
)
from app.models.contacts.core import Contact
from app.models.managers.contact_manager import ContactUnavailable
from app.services.communications.inbox import (
    InboxItemUnavailable,
    archive_thread,
    get_thread_detail,
    hide_message_locally,
    list_contact_messages,
    list_inbox_threads,
    mark_thread_read,
)
from cryptography.fernet import Fernet
import base64
from app.core.config import settings

__source_only__ = True


@pytest.fixture(autouse=True)
def _pii_keys(monkeypatch):
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode(), raising=False)
    monkeypatch.setattr(settings, "PII_INDEX_KEY", base64.urlsafe_b64encode(b"u" * 32).decode(), raising=False)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode(), raising=False)



class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return _FakeScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeInboxSession:
    """In-memory session backing `MessageThread`, `UnifiedMessage`, `Contact`, and `ContactChannelStat`."""

    def __init__(self):
        self.contacts: list[Contact] = []
        self.threads: list[MessageThread] = []
        self.messages: list[UnifiedMessage] = []
        self.stats: list[ContactChannelStat] = []

    def _extract_eq_filters(self, stmt) -> dict[str, object]:
        filters: dict[str, object] = {}
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            return filters

        def _walk(node):
            sub = getattr(node, "clauses", None)
            if sub is not None:
                for child in sub:
                    _walk(child)
                return
            left = getattr(node, "left", None)
            right = getattr(node, "right", None)
            col_name = getattr(left, "key", None) or getattr(left, "name", None)
            if not col_name:
                return
            val = getattr(right, "value", None)
            if val is None and right is not None:
                r_str = str(right).strip().lower()
                if r_str == "true":
                    val = True
                elif r_str == "false":
                    val = False
            filters[col_name] = val

        _walk(whereclause)
        return filters

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        eq = self._extract_eq_filters(stmt)
        tenant_id = eq.get("tenant_id")

        if entity is MessageThread:
            rows = [t for t in self.threads if t.tenant_id == tenant_id]
            if "id" in eq and eq["id"] is not None and len(rows) > 0:
                sql_str = str(stmt)
                if "message_threads.id <" in sql_str:
                    rows = [t for t in rows if t.id < int(eq["id"])]
                else:
                    rows = [t for t in rows if t.id == int(eq["id"])]
            if "channel" in eq and eq["channel"] is not None:
                rows = [t for t in rows if t.channel == eq["channel"]]
            if "contact_id" in eq and eq["contact_id"] is not None:
                rows = [t for t in rows if t.contact_id == int(eq["contact_id"])]
            rows.sort(key=lambda t: t.id, reverse=True)
            return _FakeExecuteResult(rows)

        if entity is Contact:
            rows = [c for c in self.contacts if c.tenant_id == tenant_id]
            if "id" in eq and eq["id"] is not None:
                rows = [c for c in rows if c.id == int(eq["id"])]
            return _FakeExecuteResult(rows)

        if entity is UnifiedMessage:
            rows = [m for m in self.messages if m.tenant_id == tenant_id]
            if "id" in eq and eq["id"] is not None:
                rows = [m for m in rows if m.id == int(eq["id"])]
            if "thread_id" in eq and eq["thread_id"] is not None:
                rows = [m for m in rows if m.thread_id == int(eq["thread_id"])]
            if "contact_id" in eq and eq["contact_id"] is not None:
                rows = [m for m in rows if m.contact_id == int(eq["contact_id"])]
            if "is_hidden" in eq and eq["is_hidden"] is not None:
                rows = [m for m in rows if bool(m.is_hidden) is bool(eq["is_hidden"])]
            rows.sort(key=lambda m: (m.sent_at, m.id))
            return _FakeExecuteResult(rows)

        if entity is ContactChannelStat:
            rows = [s for s in self.stats if s.tenant_id == tenant_id]
            if "contact_id" in eq and eq["contact_id"] is not None:
                rows = [s for s in rows if s.contact_id == int(eq["contact_id"])]
            rows.sort(key=lambda s: s.channel)
            return _FakeExecuteResult(rows)

        return _FakeExecuteResult([])

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _seed_sample_inbox(session: _FakeInboxSession):
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    c1 = Contact(
        id=10,
        tenant_id=1,
        display_name_encrypted="Alice Manager",
    )
    c2 = Contact(
        id=20,
        tenant_id=1,
        display_name_encrypted="Bob Partner",
    )
    c_other = Contact(
        id=30,
        tenant_id=2,
        display_name_encrypted="Other Tenant Secret",
    )
    session.contacts.extend([c1, c2, c_other])

    th1 = MessageThread(
        id=101,
        tenant_id=1,
        connection_id=5,
        contact_id=10,
        channel="email",
        thread_key="email:q4-contract",
        subject_encrypted="Q4 Contract Renewal",
        participants_encrypted='["alice@example.com", "sales@company.test"]',
        last_message_at=now - timedelta(hours=1),
        message_count=2,
        unread_count=1,
        is_archived=False,
    )
    th2 = MessageThread(
        id=102,
        tenant_id=1,
        connection_id=6,
        contact_id=20,
        channel="telegram",
        thread_key="tg:chat:777",
        subject_encrypted="Telegram Escalation",
        participants_encrypted='["tg:777", "support"]',
        last_message_at=now - timedelta(minutes=15),
        message_count=1,
        unread_count=0,
        is_archived=False,
    )
    th_other = MessageThread(
        id=201,
        tenant_id=2,
        connection_id=9,
        contact_id=30,
        channel="email",
        thread_key="email:secret",
        subject_encrypted="Confidential Acquisition",
        participants_encrypted='["ceo@other.test"]',
        last_message_at=now,
        message_count=1,
        unread_count=1,
        is_archived=False,
    )
    session.threads.extend([th1, th2, th_other])

    m1 = UnifiedMessage(
        id=1001,
        tenant_id=1,
        thread_id=101,
        contact_id=10,
        connection_id=5,
        channel="email",
        original_id="imap:5:1",
        direction="inbound",
        sent_at=now - timedelta(hours=2),
        received_at=now - timedelta(hours=2),
        subject_encrypted="Q4 Contract Renewal",
        sender_encrypted="alice@example.com",
        recipient_encrypted="sales@company.test",
        original_url="imap://mail.example.com/INBOX;UID=1",
        content_hash="a" * 64,
        is_read=True,
        is_archived=False,
        is_hidden=False,
        unavailable_at=None,
    )
    m2 = UnifiedMessage(
        id=1002,
        tenant_id=1,
        thread_id=101,
        contact_id=10,
        connection_id=5,
        channel="email",
        original_id="imap:5:2",
        direction="outbound",
        sent_at=now - timedelta(hours=1),
        received_at=now - timedelta(hours=1),
        subject_encrypted="Re: Q4 Contract Renewal",
        sender_encrypted="sales@company.test",
        recipient_encrypted="alice@example.com",
        original_url="imap://mail.example.com/INBOX;UID=2",
        content_hash="b" * 64,
        is_read=False,
        is_archived=False,
        is_hidden=False,
        unavailable_at=now - timedelta(minutes=30),
    )
    m3 = UnifiedMessage(
        id=1003,
        tenant_id=1,
        thread_id=102,
        contact_id=20,
        connection_id=6,
        channel="telegram",
        original_id="tg:777:55",
        direction="inbound",
        sent_at=now - timedelta(minutes=15),
        received_at=now - timedelta(minutes=15),
        subject_encrypted="Telegram Escalation",
        sender_encrypted="tg:777",
        recipient_encrypted="support",
        original_url="https://t.me/c/777/55",
        content_hash="c" * 64,
        is_read=True,
        is_archived=False,
        is_hidden=False,
        unavailable_at=None,
    )
    m_other = UnifiedMessage(
        id=2001,
        tenant_id=2,
        thread_id=201,
        contact_id=30,
        connection_id=9,
        channel="email",
        original_id="imap:9:1",
        direction="inbound",
        sent_at=now,
        received_at=now,
        subject_encrypted="Confidential Acquisition",
        sender_encrypted="ceo@other.test",
        recipient_encrypted="board@other.test",
        original_url="imap://other.test/INBOX;UID=1",
        content_hash="d" * 64,
        is_read=False,
        is_archived=False,
        is_hidden=False,
        unavailable_at=None,
    )
    session.messages.extend([m1, m2, m3, m_other])

    session.stats.append(
        ContactChannelStat(
            id=1,
            tenant_id=1,
            contact_id=10,
            channel="email",
            inbound_count=1,
            outbound_count=1,
            last_inbound_at=now - timedelta(hours=2),
            last_outbound_at=now - timedelta(hours=1),
            last_message_at=now - timedelta(hours=1),
        )
    )
    return now


@pytest.mark.asyncio
async def test_inbox_list_filters_metadata_search_and_tenant_isolation():
    session = _FakeInboxSession()
    now = _seed_sample_inbox(session)
    actor = SimpleNamespace(id=7, is_active=True)

    with tenant_scope(1), permission_scope(actor, member_grants=workspace_grants("owner")):
        all_threads = await list_inbox_threads(session)
        assert [t["id"] for t in all_threads["items"]] == [102, 101]

        # Filter by channel
        email_only = await list_inbox_threads(session, channel="email")
        assert [t["id"] for t in email_only["items"]] == [101]
        assert email_only["items"][0]["channel_label"] == "Email"

        # Filter by unread
        unread_only = await list_inbox_threads(session, is_read=False)
        assert [t["id"] for t in unread_only["items"]] == [101]

        # Filter by direction (outbound exists only in thread 101)
        outbound_only = await list_inbox_threads(session, direction="outbound")
        assert [t["id"] for t in outbound_only["items"]] == [101]

        # Metadata-only search by participant/subject/contact display name
        by_subject = await list_inbox_threads(session, q="Renewal alice")
        assert [t["id"] for t in by_subject["items"]] == [101]

        by_contact_name = await list_inbox_threads(session, q="Bob Partner")
        assert [t["id"] for t in by_contact_name["items"]] == [102]

        # Cross-tenant secret never leaks in search
        leak_probe = await list_inbox_threads(session, q="Confidential Acquisition")
        assert leak_probe["items"] == []

        # Bounded pagination cursor
        page1 = await list_inbox_threads(session, limit=1)
        assert [t["id"] for t in page1["items"]] == [102]
        assert page1["next_before_id"] == 102
        page2 = await list_inbox_threads(session, limit=1, before_id=page1["next_before_id"])
        assert [t["id"] for t in page2["items"]] == [101]

        # Validation checks
        with pytest.raises(ValueError, match="limit"):
            await list_inbox_threads(session, limit=0)
        with pytest.raises(ValueError, match="channel"):
            await list_inbox_threads(session, channel="whatsapp")
        with pytest.raises(ValueError, match="direction"):
            await list_inbox_threads(session, direction="sideways")
        with pytest.raises(ValueError, match="since must not be after until"):
            await list_inbox_threads(session, since=now, until=now - timedelta(hours=1))


@pytest.mark.asyncio
async def test_thread_detail_chronological_order_and_unavailable_originals():
    session = _FakeInboxSession()
    _seed_sample_inbox(session)
    actor = SimpleNamespace(id=7, is_active=True)

    with tenant_scope(1), permission_scope(actor, member_grants=workspace_grants("owner")):
        detail = await get_thread_detail(session, 101)
        assert detail["thread"]["id"] == 101
        assert detail["thread"]["contact_display_name"] == "Alice Manager"
        assert len(detail["contact_channel_stats"]) == 1

        msgs = detail["messages"]
        assert [m["id"] for m in msgs] == [1001, 1002]
        # m1 is available -> original_url is returned
        assert msgs[0]["is_unavailable"] is False
        assert msgs[0]["availability_status"] == "available"
        assert msgs[0]["original_url"] == "imap://mail.example.com/INBOX;UID=1"

        # m2 has unavailable_at set -> original_url is suppressed and status is unavailable (TZ 2.5)
        assert msgs[1]["is_unavailable"] is True
        assert msgs[1]["availability_status"] == "unavailable"
        assert msgs[1]["original_url"] is None

        # Cross-tenant thread is opaque 404
        with pytest.raises(InboxItemUnavailable):
            await get_thread_detail(session, 201)

        # Chronological messages per contact
        contact_msgs = await list_contact_messages(session, 10)
        assert [m["id"] for m in contact_msgs["items"]] == [1001, 1002]
        with pytest.raises(ContactUnavailable):
            await list_contact_messages(session, 30)


@pytest.mark.asyncio
async def test_mark_read_archive_and_local_hide_never_deletes_on_provider():
    session = _FakeInboxSession()
    _seed_sample_inbox(session)
    actor = SimpleNamespace(id=7, is_active=True)

    with tenant_scope(1), permission_scope(actor, member_grants=workspace_grants("owner")):
        # Mark thread 101 read
        read_res = await mark_thread_read(session, thread_id=101, is_read=True)
        assert read_res == {
            "thread_id": 101,
            "is_read": True,
            "unread_count": 0,
            "updated_messages": 1,
        }

        # Mark thread 101 unread
        unread_res = await mark_thread_read(session, thread_id=101, is_read=False)
        assert unread_res["is_read"] is False
        assert unread_res["unread_count"] == 2

        # Archive thread 101
        arch_res = await archive_thread(session, thread_id=101, is_archived=True)
        assert arch_res == {"thread_id": 101, "is_archived": True}

        # Local hide on message 1002 (delete = local hide, not provider delete)
        hide_res = await hide_message_locally(session, message_id=1002, is_hidden=True)
        assert hide_res == {
            "message_id": 1002,
            "thread_id": 101,
            "is_hidden": True,
            "provider_deleted": False,
        }
        detail_after_hide = await get_thread_detail(session, 101)
        assert [m["id"] for m in detail_after_hide["messages"]] == [1001]
        assert detail_after_hide["thread"]["message_count"] == 1

        # Unhide restores message_count
        unhide_res = await hide_message_locally(session, message_id=1002, is_hidden=False)
        assert unhide_res["is_hidden"] is False
        assert unhide_res["provider_deleted"] is False
        detail_after_unhide = await get_thread_detail(session, 101)
        assert len(detail_after_unhide["messages"]) == 2


@pytest.mark.asyncio
async def test_communication_api_and_web_routes_contract(monkeypatch):
    # Validate BusinessRoute contract on /api/v1/communication router
    validate_business_routes(communication_api.router)

    session = _FakeInboxSession()
    _seed_sample_inbox(session)
    actor = SimpleNamespace(id=7, is_active=True)

    @asynccontextmanager
    async def _fake_cm():
        class _BeginCtx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        session.begin = _BeginCtx
        yield session

    monkeypatch.setattr(communication_api, "async_session_maker", _fake_cm)

    with tenant_scope(1), permission_scope(actor, member_grants=workspace_grants("owner")):
        inbox_out = await communication_api.get_communication_inbox(
            channel="email",
            direction=None,
            contact_id=None,
            is_read=None,
            is_archived=False,
            since=None,
            until=None,
            q=None,
            limit=50,
            before_id=None,
        )
        assert len(inbox_out.items) == 1
        assert inbox_out.items[0].id == 101

        detail_out = await communication_api.get_communication_thread_detail(thread_id=101)
        assert detail_out.thread.id == 101
        assert len(detail_out.messages) == 2

        contact_msgs_out = await communication_api.get_communication_contact_messages(
            contact_id=10,
            channel=None,
            limit=50,
            before_id=None,
        )
        assert len(contact_msgs_out.items) == 2

        mark_out = await communication_api.post_communication_mark_read(
            communication_api.MarkReadRequest(thread_id=101, is_read=True)
        )
        assert mark_out.is_read is True

        arch_out = await communication_api.post_communication_archive(
            communication_api.ArchiveThreadRequest(thread_id=101, is_archived=True)
        )
        assert arch_out.is_archived is True

        hide_out = await communication_api.post_communication_hide_message(
            communication_api.HideMessageRequest(message_id=1001, is_hidden=True)
        )
        assert hide_out.is_hidden is True
        assert hide_out.provider_deleted is False

        with pytest.raises(HTTPException) as exc_info:
            await communication_api.get_communication_thread_detail(thread_id=9999)
        assert exc_info.value.status_code == 404

        # Check web handler renders inbox and outbox context cleanly
        from app.web import communications as web_comms

        monkeypatch.setattr(web_comms, "async_session_maker", _fake_cm)
        monkeypatch.setattr(web_comms, "snippet_rows", AsyncMock(return_value=[]))
        captured: dict[str, object] = {}

        def _fake_render(request, template_name, **ctx):
            captured["template"] = template_name
            captured.update(ctx)
            return captured

        monkeypatch.setattr(web_comms, "render", _fake_render)
        await web_comms.communications_page(
            SimpleNamespace(state=SimpleNamespace()),
            boundary="cloud",
            tab="inbox",
            channel=None,
            contact_id=None,
            thread_id=102,
            is_read=None,
            is_archived=False,
            q="",
            before_id=None,
        )
        assert captured["template"] == "web/communications.html"
        assert captured["active_tab"] == "inbox"
        assert captured["selected_thread_detail"]["thread"]["id"] == 102


@pytest.mark.asyncio
async def test_b7_route_and_service_permissions_check_comms_message_not_contact():
    """B-7: Inbox routes and contact-messages service gate on comms.message, not contact alone."""
    from app.types import ActionType

    # Verify router-level dependency requires comms.message VIEW
    router_dep_sets = [
        frozenset(
            cell.cell_contents
            for cell in (getattr(dep.dependency, "__closure__", None) or ())
            if isinstance(cell.cell_contents, (str, ActionType))
        )
        for dep in communication_api.router.dependencies
    ]
    assert frozenset({"comms.message", ActionType.VIEW}) in router_dep_sets

    # Verify route-level dependencies on /api/v1/communication/*
    route_by_path: dict[tuple[str, str], object] = {}
    for route in communication_api.router.routes:
        methods = getattr(route, "methods", set()) or set()
        for m in methods:
            route_by_path[(m, route.path)] = route

    def _dep_sets(route_obj):
        sets = []
        for dep in route_obj.dependant.dependencies:
            vals = frozenset(
                cell.cell_contents
                for cell in (getattr(dep.call, "__closure__", None) or ())
                if isinstance(cell.cell_contents, (str, ActionType))
            )
            if vals:
                sets.append(vals)
        return sets

    assert frozenset({"comms.message", ActionType.VIEW}) in _dep_sets(route_by_path[("GET", "/inbox")])
    assert frozenset({"comms.message", ActionType.VIEW}) in _dep_sets(
        route_by_path[("GET", "/inbox/threads/{thread_id}")]
    )
    assert frozenset({"comms.message", ActionType.VIEW}) in _dep_sets(
        route_by_path[("GET", "/inbox/contacts/{contact_id}/messages")]
    )
    assert frozenset({"comms.message", ActionType.UPDATE}) in _dep_sets(route_by_path[("POST", "/inbox/mark-read")])
    assert frozenset({"comms.message", ActionType.UPDATE}) in _dep_sets(route_by_path[("POST", "/inbox/archive")])
    assert frozenset({"comms.message", ActionType.UPDATE}) in _dep_sets(route_by_path[("POST", "/inbox/hide")])

    # Service-level check: an actor granted only contact:view cannot list contact messages or inbox
    session = _FakeInboxSession()
    _seed_sample_inbox(session)
    actor = SimpleNamespace(id=19, is_active=True)
    contact_only_grants = {("contact", ActionType.VIEW)}

    with tenant_scope(1), permission_scope(actor, member_grants=contact_only_grants):
        with pytest.raises(PermissionError, match="Unified message access denied"):
            await list_inbox_threads(session)
        with pytest.raises(PermissionError, match="Unified message access denied"):
            await list_contact_messages(session, 10)


@pytest.mark.asyncio
async def test_b10_resolve_context_communication_handoff_to_chat(monkeypatch):
    """B-10: 'Open in chat' (`context=communication:<id>`) resolves UnifiedMessage with tenant, permission, hidden, and boundary checks."""
    from app.models.managers import base_manager as bm_mod
    from app.services.knowledge import workspace as ws_mod
    from app.types import ActionType

    session = _FakeInboxSession()
    _seed_sample_inbox(session)
    actor = SimpleNamespace(id=7, is_active=True)

    @asynccontextmanager
    async def _fake_cm():
        yield session

    monkeypatch.setattr(bm_mod, "async_session_maker", _fake_cm)

    with tenant_scope(1), permission_scope(actor, member_grants=workspace_grants("viewer")):
        ctx = await ws_mod.resolve_context("communication", 1001, "cloud")
        assert ctx is not None
        assert ctx["type"] == "communication"
        assert ctx["id"] == 1001
        assert ctx["thread_id"] == 101
        assert ctx["title"] == "Q4 Contract Renewal"
        assert ctx["source_kind"] == "email"
        assert ctx["href"] == "imap://mail.example.com/INBOX;UID=1"
        assert "alice@example.com" in ctx["snippet"]

        # Unavailable original suppresses href
        ctx_unavail = await ws_mod.resolve_context("communication", 1002, "cloud")
        assert ctx_unavail is not None
        assert ctx_unavail["href"] is None

        # Cross-tenant message raises 404
        with pytest.raises(HTTPException) as exc_cross:
            await ws_mod.resolve_context("communication", 2001, "cloud")
        assert exc_cross.value.status_code == 404

        # Locally hidden message raises 404
        session.messages[0].is_hidden = True
        with pytest.raises(HTTPException) as exc_hidden:
            await ws_mod.resolve_context("communication", 1001, "cloud")
        assert exc_hidden.value.status_code == 404
        session.messages[0].is_hidden = False

    # Actor without comms.message:view gets 404
    with tenant_scope(1), permission_scope(actor, member_grants={("contact", ActionType.VIEW)}):
        with pytest.raises(HTTPException) as exc_perm:
            await ws_mod.resolve_context("communication", 1001, "cloud")
        assert exc_perm.value.status_code == 404


@pytest.mark.asyncio
async def test_n3_and_n4_mark_read_all_hidden_and_web_403_propagation(monkeypatch):
    """N-3 & N-4: mark_thread_read sets unread_count=0 when all messages are hidden; web pages raise 403 on PermissionError."""
    from app.types import ActionType
    from app.web import communications as web_comms
    from app.web import contacts_merge as web_merge

    session = _FakeInboxSession()
    _seed_sample_inbox(session)
    actor = SimpleNamespace(id=7, is_active=True)

    with tenant_scope(1), permission_scope(actor, member_grants=workspace_grants("owner")):
        for m in session.messages:
            if m.thread_id == 101:
                m.is_hidden = True
        res = await mark_thread_read(session, thread_id=101, is_read=False)
        assert res["unread_count"] == 0
        assert res["updated_messages"] == 0

    @asynccontextmanager
    async def _fake_cm():
        class _BeginCtx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        session.begin = _BeginCtx
        yield session

    monkeypatch.setattr(web_comms, "async_session_maker", _fake_cm)
    monkeypatch.setattr(web_comms, "snippet_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(web_merge, "async_session_maker", _fake_cm)

    with tenant_scope(1), permission_scope(actor, member_grants=set()):
        with pytest.raises(HTTPException) as exc_comms:
            await web_comms.communications_page(
                SimpleNamespace(state=SimpleNamespace()),
                boundary="cloud",
                tab="inbox",
            )
        assert exc_comms.value.status_code == 403

        with pytest.raises(HTTPException) as exc_merge:
            await web_merge.contacts_merge_page(SimpleNamespace(state=SimpleNamespace()))
        assert exc_merge.value.status_code == 403
        assert isinstance(ActionType.VIEW, ActionType)

