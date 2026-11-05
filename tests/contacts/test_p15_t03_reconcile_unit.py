"""DB-exempt unit and contract tests for P15-T03 contact identity reconciliation, merge, and unmerge."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.tenant_context import tenant_scope
from app.models.communications import ContactChannelStat, MessageThread, UnifiedMessage
from app.models.contacts import Contact, ContactConsent, ContactIdentity, ContactMergeJournal
from app.models.crm import CrmEntity
from app.models.managers.contact_manager import ContactUnavailable
from app.services.contacts.identity import identity_index
from app.services.contacts.reconcile import (
    AUDIT_ACTION_MERGE,
    AUDIT_ACTION_UNMERGE,
    ContactMergeConflictError,
    ContactMergeError,
    decode_merge_journal_payload,
    merge_contacts,
    suggest_contact_merges,
    unmerge_contacts,
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



class _FakeResult:
    def __init__(self, rows: list[Any]):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)


class _InMemoryReconcileSession:
    def __init__(self):
        self.contacts: list[Contact] = []
        self.identities: list[ContactIdentity] = []
        self.consents: list[ContactConsent] = []
        self.messages: list[UnifiedMessage] = []
        self.threads: list[MessageThread] = []
        self.crm_entities: list[CrmEntity] = []
        self.stats: list[ContactChannelStat] = []
        self.journals: list[ContactMergeJournal] = []
        self.lock_order: list[int] = []
        self._next_id = 500

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            self._next_id += 1
            obj.id = self._next_id
        if isinstance(obj, Contact) and obj not in self.contacts:
            self.contacts.append(obj)
        elif isinstance(obj, ContactIdentity) and obj not in self.identities:
            self.identities.append(obj)
        elif isinstance(obj, ContactConsent) and obj not in self.consents:
            self.consents.append(obj)
        elif isinstance(obj, UnifiedMessage) and obj not in self.messages:
            self.messages.append(obj)
        elif isinstance(obj, MessageThread) and obj not in self.threads:
            self.threads.append(obj)
        elif isinstance(obj, CrmEntity) and obj not in self.crm_entities:
            self.crm_entities.append(obj)
        elif isinstance(obj, ContactChannelStat) and obj not in self.stats:
            self.stats.append(obj)
        elif isinstance(obj, ContactMergeJournal) and obj not in self.journals:
            self.journals.append(obj)

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = str(stmt)
        params = stmt.compile().params if hasattr(stmt, "compile") else {}

        if "DELETE FROM" in sql and "contact_channel_stats" in sql:
            stat_id = params.get("id_1")
            if stat_id is not None:
                self.stats = [s for s in self.stats if s.id != stat_id]
                return _FakeResult([])
            c_ids = [v for k, v in params.items() if "contact_id" in k]
            flat_ids: set[int] = set()
            for item in c_ids:
                if isinstance(item, (list, tuple, set)):
                    flat_ids.update(int(x) for x in item)
                elif isinstance(item, int):
                    flat_ids.add(item)
            self.stats = [s for s in self.stats if s.contact_id not in flat_ids]
            return _FakeResult([])

        if "DELETE FROM" in sql and "message_threads" in sql:
            in_ids = next((set(v) for k, v in params.items() if "id" in k and isinstance(v, (list, tuple, set))), set())
            self.threads = [t for t in self.threads if t.id not in in_ids]
            return _FakeResult([])

        if "FROM" in sql and "contact_merge_journal" in sql:
            t_id = params.get("tenant_id_1")
            j_id = params.get("id_1")
            return _FakeResult([j for j in self.journals if j.tenant_id == t_id and j.id == j_id])

        if "FROM" in sql and "contacts" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("id_1")
            if c_id is not None:
                if "FOR UPDATE" in sql:
                    self.lock_order.append(int(c_id))
                return _FakeResult([c for c in self.contacts if c.tenant_id == t_id and c.id == c_id])
            return _FakeResult([c for c in self.contacts if c.tenant_id == t_id and c.merged_into_id is None])

        if "FROM" in sql and "contact_identities" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            if c_id is not None:
                return _FakeResult([i for i in self.identities if i.tenant_id == t_id and i.contact_id == c_id])
            in_ids = next((set(v) for k, v in params.items() if "id" in k and isinstance(v, (list, tuple, set))), None)
            if in_ids is not None:
                return _FakeResult([i for i in self.identities if i.tenant_id == t_id and i.id in in_ids])
            return _FakeResult([i for i in self.identities if i.tenant_id == t_id])

        if "FROM" in sql and "contact_consents" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            if c_id is not None:
                return _FakeResult([c for c in self.consents if c.tenant_id == t_id and c.contact_id == c_id])
            in_ids = next((set(v) for k, v in params.items() if "id" in k and isinstance(v, (list, tuple, set))), None)
            if in_ids is not None:
                return _FakeResult([c for c in self.consents if c.tenant_id == t_id and c.id in in_ids])

        if "FROM" in sql and "unified_messages" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            if c_id is not None:
                return _FakeResult([m for m in self.messages if m.tenant_id == t_id and m.contact_id == c_id])
            in_ids = next((set(v) for k, v in params.items() if "id" in k and isinstance(v, (list, tuple, set))), None)
            if in_ids is not None:
                return _FakeResult([m for m in self.messages if m.tenant_id == t_id and m.id in in_ids])
            return _FakeResult(
                [
                    m
                    for m in self.messages
                    if m.tenant_id == t_id and m.contact_id is not None and m.thread_id is not None
                ]
            )

        if "FROM" in sql and "message_threads" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            if c_id is not None:
                return _FakeResult([t for t in self.threads if t.tenant_id == t_id and t.contact_id == c_id])
            in_ids = next((set(v) for k, v in params.items() if "id" in k and isinstance(v, (list, tuple, set))), None)
            if in_ids is not None:
                return _FakeResult([t for t in self.threads if t.tenant_id == t_id and t.id in in_ids])

        if "FROM" in sql and "crm_entities" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            if c_id is not None:
                return _FakeResult([e for e in self.crm_entities if e.tenant_id == t_id and e.contact_id == c_id])
            in_ids = next((set(v) for k, v in params.items() if "id" in k and isinstance(v, (list, tuple, set))), None)
            if in_ids is not None:
                return _FakeResult([e for e in self.crm_entities if e.tenant_id == t_id and e.id in in_ids])

        if "FROM" in sql and "contact_channel_stats" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            return _FakeResult([s for s in self.stats if s.tenant_id == t_id and s.contact_id == c_id])

        return _FakeResult([])


@pytest.mark.asyncio
async def test_strong_and_weak_merge_suggestions():
    session = _InMemoryReconcileSession()
    # Contact 1 (Email) and Contact 2 (Telegram + same email blind index) -> strong match
    c1 = Contact(id=1, tenant_id=1, display_name_encrypted="Alice Smith", birthday_encrypted="1990-05-10", birthday_has_year=True)
    c2 = Contact(id=2, tenant_id=1, display_name_encrypted="A. Smith", birthday_encrypted="1991-05-10", birthday_has_year=True)
    # Contact 3 and Contact 4 share display name and thread 99 -> weak match
    c3 = Contact(id=3, tenant_id=1, display_name_encrypted="Boris Volkov")
    c4 = Contact(id=4, tenant_id=1, display_name_encrypted="  boris   volkov ")
    session.add(c1)
    session.add(c2)
    session.add(c3)
    session.add(c4)

    shared_email_idx = identity_index("email", "alice@company.com")
    session.add(
        ContactIdentity(
            id=11,
            tenant_id=1,
            contact_id=1,
            kind="email",
            connection_id=10,
            value_encrypted="alice@company.com",
            blind_index=shared_email_idx,
            verified=True,
        )
    )
    session.add(
        ContactIdentity(
            id=12,
            tenant_id=1,
            contact_id=2,
            kind="email",
            connection_id=20,
            value_encrypted="alice@company.com",
            blind_index=shared_email_idx,
            verified=False,
        )
    )

    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    session.add(
        UnifiedMessage(
            id=201,
            tenant_id=1,
            connection_id=10,
            contact_id=3,
            thread_id=99,
            channel="telegram",
            original_id="tg:1",
            direction="inbound",
            sent_at=now,
            received_at=now,
        )
    )
    session.add(
        UnifiedMessage(
            id=202,
            tenant_id=1,
            connection_id=10,
            contact_id=4,
            thread_id=99,
            channel="max",
            original_id="max:2",
            direction="inbound",
            sent_at=now,
            received_at=now,
        )
    )

    with tenant_scope(1):
        suggestions = await suggest_contact_merges(session, limit=20)

    assert len(suggestions) == 2
    strong = suggestions[0]
    assert strong["confidence"] == "strong"
    assert strong["score"] == 0.95
    assert strong["source_contact_id"] == 2
    assert strong["target_contact_id"] == 1
    assert "exact_email_blind_index" in strong["reasons"]
    assert "display_name" in strong["conflicting_fields"]
    assert "birthday" in strong["conflicting_fields"]
    assert strong["auto_merge"] is False

    weak = suggestions[1]
    assert weak["confidence"] == "weak"
    assert weak["score"] == 0.60
    assert weak["source_contact_id"] == 4
    assert weak["target_contact_id"] == 3
    assert "same_display_name_shared_thread" in weak["reasons"]


@pytest.mark.asyncio
async def test_merge_then_unmerge_restores_exact_previous_state_and_locks_in_order():
    session = _InMemoryReconcileSession()
    t1 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 4, 11, 0, tzinfo=timezone.utc)

    target = Contact(
        id=10,
        tenant_id=1,
        display_name_encrypted="Elena Target",
        notes_encrypted="Target notes",
        birthday_encrypted="1988-03-15",
        birthday_has_year=True,
        preferred_channel="email",
        last_active_channel="email",
        last_active_at=t1,
    )
    source = Contact(
        id=25,
        tenant_id=1,
        display_name_encrypted="Elena Source (TG)",
        notes_encrypted="Source TG notes",
        birthday_encrypted="1989-07-20",
        birthday_has_year=True,
        anniversary_encrypted="2015-06-01",
        anniversary_has_year=True,
        preferred_channel="telegram",
        last_active_channel="telegram",
        last_active_at=t2,
    )
    session.add(target)
    session.add(source)

    id_email = ContactIdentity(
        id=31,
        tenant_id=1,
        contact_id=10,
        kind="email",
        connection_id=1,
        value_encrypted="elena@example.com",
        blind_index=identity_index("email", "elena@example.com"),
        verified=True,
    )
    id_tg = ContactIdentity(
        id=32,
        tenant_id=1,
        contact_id=25,
        kind="telegram_id",
        connection_id=2,
        value_encrypted="777111",
        blind_index=identity_index("telegram_id", "777111"),
        verified=True,
    )
    session.add(id_email)
    session.add(id_tg)

    consent_src = ContactConsent(
        id=41,
        tenant_id=1,
        contact_id=25,
        channel="telegram",
        purpose="greeting",
        basis_encrypted="opt-in",
        source_evidence_encrypted="bot start",
        granted_at=t2,
    )
    session.add(consent_src)

    thread_src = MessageThread(
        id=51,
        tenant_id=1,
        connection_id=2,
        contact_id=25,
        channel="telegram",
        thread_key="telegram:chat:777111",
        message_count=2,
        unread_count=1,
    )
    session.add(thread_src)

    msg_src = UnifiedMessage(
        id=61,
        tenant_id=1,
        connection_id=2,
        contact_id=25,
        thread_id=51,
        channel="telegram",
        original_id="777111:1",
        direction="inbound",
        sent_at=t2,
        received_at=t2,
    )
    session.add(msg_src)

    stat_target = ContactChannelStat(
        id=71,
        tenant_id=1,
        contact_id=10,
        channel="email",
        inbound_count=3,
        outbound_count=1,
        last_inbound_at=t1,
        last_message_at=t1,
    )
    stat_source_email = ContactChannelStat(
        id=72,
        tenant_id=1,
        contact_id=25,
        channel="email",
        inbound_count=2,
        outbound_count=4,
        last_inbound_at=t2,
        last_message_at=t2,
    )
    stat_source_tg = ContactChannelStat(
        id=73,
        tenant_id=1,
        contact_id=25,
        channel="telegram",
        inbound_count=5,
        outbound_count=0,
        last_inbound_at=t2,
        last_message_at=t2,
    )
    session.add(stat_target)
    session.add(stat_source_email)
    session.add(stat_source_tg)

    with tenant_scope(1):
        # Pass source=25, target=10 -> lock order must be ascending [10, 25]
        merge_res = await merge_contacts(
            session,
            source_contact_id=25,
            target_contact_id=10,
            reason="Verified same person across Email and Telegram",
            field_choices={"display_name": "source", "birthday": "target"},
        )
        assert session.lock_order == [10, 25]
        assert merge_res["audit_action"] == AUDIT_ACTION_MERGE
        assert source.merged_into_id == 10
        assert target.display_name_encrypted == "Elena Source (TG)"
        assert target.birthday_encrypted == "1988-03-15"
        assert target.anniversary_encrypted == "2015-06-01"
        assert id_tg.contact_id == 10
        assert consent_src.contact_id == 10
        assert msg_src.contact_id == 10
        assert thread_src.contact_id == 10

        # Stats merged onto target (10): email inbound = 3 + 2 = 5, tg inbound = 5
        target_stats_after = {s.channel: s for s in session.stats if s.contact_id == 10}
        assert target_stats_after["email"].inbound_count == 5
        assert target_stats_after["email"].outbound_count == 5
        assert target_stats_after["telegram"].inbound_count == 5

        # Concurrent/second merge of already-merged contact 25 must fail
        with pytest.raises(ContactMergeConflictError):
            await merge_contacts(session, source_contact_id=25, target_contact_id=10, reason="duplicate")

        # Now unmerge using journal_id and verify exact restoration
        unmerge_res = await unmerge_contacts(session, journal_id=merge_res["journal_id"])
        assert unmerge_res["audit_action"] == AUDIT_ACTION_UNMERGE
        assert source.merged_into_id is None
        assert target.display_name_encrypted == "Elena Target"
        assert target.birthday_encrypted == "1988-03-15"
        assert target.anniversary_encrypted is None
        assert target.last_active_at == t1
        assert source.display_name_encrypted == "Elena Source (TG)"
        assert source.birthday_encrypted == "1989-07-20"
        assert source.anniversary_encrypted == "2015-06-01"
        assert id_tg.contact_id == 25
        assert consent_src.contact_id == 25
        assert msg_src.contact_id == 25
        assert thread_src.contact_id == 25

        restored_target_stats = {s.channel: s for s in session.stats if s.contact_id == 10}
        restored_source_stats = {s.channel: s for s in session.stats if s.contact_id == 25}
        assert set(restored_target_stats) == {"email"}
        assert restored_target_stats["email"].inbound_count == 3
        assert set(restored_source_stats) == {"email", "telegram"}
        assert restored_source_stats["email"].inbound_count == 2
        assert restored_source_stats["telegram"].inbound_count == 5

        # Double unmerge is rejected
        with pytest.raises(ContactMergeConflictError):
            await unmerge_contacts(session, journal_id=merge_res["journal_id"])


@pytest.mark.asyncio
async def test_reconcile_validation_errors():
    session = _InMemoryReconcileSession()
    with tenant_scope(1):
        with pytest.raises(ContactMergeError):
            await merge_contacts(session, source_contact_id=5, target_contact_id=5)
        with pytest.raises(ContactMergeError):
            await merge_contacts(session, source_contact_id=1, target_contact_id=2, reason="   ")
        with pytest.raises(ContactUnavailable):
            await unmerge_contacts(session, journal_id=9999)
    assert decode_merge_journal_payload(None)["snapshot"] == {}
    assert decode_merge_journal_payload("plain text reason")["reason"] == "plain text reason"


@pytest.mark.asyncio
async def test_contacts_identity_and_workspace_projection_contracts(monkeypatch):
    from app.core.pii import PIIError
    from app.models import Contact, ContactConsent, ContactIdentity, ContactMergeJournal
    from app.services.contacts.identity import identity_index, identity_values, normalized_identity, pii_kind
    from app.services.contacts.workspace import (
        ContactPrivacy,
         consent_dict,
        contact_detail,
        contact_dict,
        identity_dict,
        list_contacts,
    )

    assert pii_kind("email") == "email"
    assert pii_kind("telegram_id") == "opaque_id"
    with pytest.raises(ValueError):
        pii_kind("unknown_kind")
    with pytest.raises(PIIError):
        normalized_identity("email", "")
    with pytest.raises(PIIError):
        normalized_identity("telegram_id", "not-a-number")

    vals = identity_values("email", "User@Example.com")
    assert vals["blind_index"] == identity_index("email", "user@example.com")

    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    c_row = Contact(
        id=1,
        tenant_id=1,
        display_name_encrypted="Test User",
        notes_encrypted="Note",
        birthday_encrypted="1990-01-01",
        birthday_has_year=True,
        anniversary_encrypted=None,
        anniversary_has_year=False,
        preferred_channel="email",
        last_active_channel="email",
        last_active_at=now,
        created_at=now,
        updated_at=now,
    )
    i_row = ContactIdentity(
        id=2,
        tenant_id=1,
        contact_id=1,
        kind="email",
        connection_id=None,
        value_encrypted="user@example.com",
        blind_index=vals["blind_index"],
        verified=True,
        source_native_id_encrypted="src-1",
    )
    con_row = ContactConsent(
        id=3,
        tenant_id=1,
        contact_id=1,
        channel="email",
        purpose="transactional",
        basis_encrypted="contract",
        source_evidence_encrypted="signed",
        granted_at=now,
        withdrawn_at=None,
    )
    j_row = ContactMergeJournal(
        id=4,
        tenant_id=1,
        source_contact_id=9,
        target_contact_id=1,
        reason_encrypted="merged",
        created_at=now,
    )

    assert contact_dict(c_row)["display_name"] == "Test User"
    assert identity_dict(i_row)["value"] == "user@example.com"
    assert consent_dict(con_row)["purpose"] == "transactional"

    class _QueryStub:
        def __init__(self, rows):
            self._rows = rows

        def order_by(self, *a):
            return self

        async def all(self):
            return self._rows

    async def _fake_list_contacts(*, limit=50, before_id=None):
        return [c_row]

    async def _fake_get(**kw):
        return c_row

    async def _fake_locked(cid, session):
        return c_row

    monkeypatch.setattr(Contact.objects, "list_contacts", _fake_list_contacts)
    monkeypatch.setattr(Contact.objects, "get", _fake_get)
    monkeypatch.setattr(Contact.objects, "_locked", _fake_locked)
    monkeypatch.setattr(ContactIdentity.objects, "filter", lambda *a, **kw: _QueryStub([i_row]))
    monkeypatch.setattr(ContactConsent.objects, "filter", lambda *a, **kw: _QueryStub([con_row]))
    monkeypatch.setattr(ContactMergeJournal.objects, "filter", lambda *a, **kw: _QueryStub([j_row]))

    with tenant_scope(1):
        listed = await list_contacts(limit=1)
        assert listed["items"][0]["id"] == 1
        assert listed["next_before_id"] == 1
        detail = await contact_detail(1, session=SimpleNamespace())
        assert len(detail["identities"]) == 1
        assert len(detail["consents"]) == 1
        exported = await ContactPrivacy.export_contact.__wrapped__(ContactPrivacy, 1, SimpleNamespace())
        assert len(exported["merge_journal"]) == 1


@pytest.mark.asyncio
async def test_b9_cross_connection_merge_and_unmerge_preserves_thread_connection_invariants():
    """B-9: Merging contacts across connections keeps threads scoped per connection_id and repairs/restores thread state."""
    session = _InMemoryReconcileSession()
    t1 = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 4, 11, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

    # Target contact (id=10) on connection 101, Source contact (id=20) on connection 202
    c_target = Contact(id=10, tenant_id=1, display_name_encrypted="Target Person", last_active_at=t1, last_active_channel="email")
    c_source = Contact(id=20, tenant_id=1, display_name_encrypted="Source Person", last_active_at=t3, last_active_channel="telegram")
    session.add(c_target)
    session.add(c_source)

    # Thread on connection 101 owned by target (same thread_key as source thread, but different connection_id)
    th_conn101 = MessageThread(
        id=301,
        tenant_id=1,
        connection_id=101,
        contact_id=10,
        channel="email",
        thread_key="email:subj:shared",
        subject_encrypted="Shared Subject",
        last_message_at=t1,
        message_count=1,
        unread_count=1,
    )
    # Thread on connection 202 owned by source
    th_conn202 = MessageThread(
        id=302,
        tenant_id=1,
        connection_id=202,
        contact_id=20,
        channel="email",
        thread_key="email:subj:shared",
        subject_encrypted="Shared Subject",
        last_message_at=t2,
        message_count=1,
        unread_count=0,
    )
    # Thread on connection 303 where contact_id was initially None, but source sent a message on it
    th_unassigned = MessageThread(
        id=303,
        tenant_id=1,
        connection_id=303,
        contact_id=None,
        channel="telegram",
        thread_key="telegram:chat:777",
        subject_encrypted="TG Group",
        last_message_at=t3,
        message_count=2,
        unread_count=2,
    )
    session.add(th_conn101)
    session.add(th_conn202)
    session.add(th_unassigned)

    m1 = UnifiedMessage(
        id=401,
        tenant_id=1,
        connection_id=101,
        contact_id=10,
        thread_id=301,
        channel="email",
        original_id="INBOX:1:1",
        direction="inbound",
        sent_at=t1,
        received_at=t1,
        content_hash="sha256:" + "1" * 64,
        is_read=False,
    )
    m2 = UnifiedMessage(
        id=402,
        tenant_id=1,
        connection_id=202,
        contact_id=20,
        thread_id=302,
        channel="email",
        original_id="INBOX:1:2",
        direction="outbound",
        sent_at=t2,
        received_at=t2,
        content_hash="sha256:" + "2" * 64,
        is_read=True,
    )
    m3_unassigned_th = UnifiedMessage(
        id=403,
        tenant_id=1,
        connection_id=303,
        contact_id=20,
        thread_id=303,
        channel="telegram",
        original_id="777:1",
        direction="inbound",
        sent_at=t2,
        received_at=t2,
        content_hash="sha256:" + "3" * 64,
        is_read=False,
    )
    # Message on connection 202 that was anomalously attached to thread 303 (connection 303)
    m4_cross_conn = UnifiedMessage(
        id=404,
        tenant_id=1,
        connection_id=202,
        contact_id=20,
        thread_id=303,
        channel="email",
        original_id="INBOX:1:9",
        direction="inbound",
        sent_at=t3,
        received_at=t3,
        content_hash="sha256:" + "4" * 64,
        is_read=False,
    )
    session.add(m1)
    session.add(m2)
    session.add(m3_unassigned_th)
    session.add(m4_cross_conn)

    with tenant_scope(1):
        merge_res = await merge_contacts(
            session,
            source_contact_id=20,
            target_contact_id=10,
            reason="cross_connection_reconcile",
            field_choices={"display_name": "target"},
        )
        # Threads on different connections stay separate on their respective connection_ids
        assert th_conn101.connection_id == 101 and th_conn101.contact_id == 10
        assert th_conn202.connection_id == 202 and th_conn202.contact_id == 10
        # Unassigned thread 303 was claimed by target_contact_id=10 and its counts recomputed after m4 moved
        assert th_unassigned.contact_id == 10
        assert th_unassigned.message_count == 1
        assert th_unassigned.unread_count == 1
        # Cross-connection message m4 was re-bound to a thread on its own connection 202
        rebound_th = next(t for t in session.threads if t.id == m4_cross_conn.thread_id)
        assert rebound_th.connection_id == 202
        assert rebound_th.contact_id == 10
        assert rebound_th.message_count == 1
        assert rebound_th.unread_count == 1

        # Unmerge restores exact pre-merge state for all threads and messages
        unmerge_res = await unmerge_contacts(session, journal_id=merge_res["journal_id"])
        assert unmerge_res["restored_messages"] == 3
        assert th_conn101.contact_id == 10
        assert th_conn202.contact_id == 20
        assert th_unassigned.contact_id is None
        assert th_unassigned.message_count == 2
        assert th_unassigned.unread_count == 2
        assert m2.contact_id == 20 and m2.thread_id == 302
        assert m3_unassigned_th.contact_id == 20 and m3_unassigned_th.thread_id == 303
        assert m4_cross_conn.contact_id == 20 and m4_cross_conn.thread_id == 303


