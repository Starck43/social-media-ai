"""DB-exempt unit and contract tests for P15-T01 IMAP incremental read sync."""

from __future__ import annotations

import imaplib
import ssl
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.tenant_context import TenantContextError, tenant_scope
from app.integrations.email import (
    DEFAULT_IMAP_FOLDERS,
    ImapSyncError,
    ImapSyncService,
    configure_imap_folders,
    get_imap_folders,
    parse_raw_header_block,
)
from app.integrations.email.imap_sync import (
    _open_pinned_imap_client,
    _resolve_imap_target,
    validate_folder_list,
)
from app.jobs.job_handlers.email_sync import JOB_NAME, handle_email_sync
from app.models.communications import ContactChannelStat, MessageThread, UnifiedMessage
from app.models.contacts import Contact, ContactIdentity
from app.models.integrations import WorkspaceConnection, WorkspaceConnectionSecret
from app.utils.crypto import encrypt_secret
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
        return SimpleNamespace(
            all=lambda: list(self._rows),
            first=lambda: self._rows[0] if self._rows else None,
        )

    def all(self):
        return list(self._rows)


class _InMemorySavepoint:
    """Savepoint that discards the rows added between `begin_nested` and `rollback` (B-13)."""

    _LISTS = ("connections", "secrets", "contacts", "identities", "threads", "messages", "stats")

    def __init__(self, session):
        self.session = session
        self._marks = {name: len(getattr(session, name)) for name in self._LISTS}

    async def rollback(self) -> None:
        for name, mark in self._marks.items():
            rows = getattr(self.session, name)
            del rows[mark:]

    async def commit(self) -> None:
        return None


class _InMemoryImapDbSession:
    def __init__(self):
        self.connections: list[WorkspaceConnection] = []
        self.secrets: list[WorkspaceConnectionSecret] = []
        self.contacts: list[Contact] = []
        self.identities: list[ContactIdentity] = []
        self.threads: list[MessageThread] = []
        self.messages: list[UnifiedMessage] = []
        self.stats: list[ContactChannelStat] = []
        self._next_id = 100

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and not isinstance(obj, WorkspaceConnectionSecret):
            self._next_id += 1
            obj.id = self._next_id
        if isinstance(obj, WorkspaceConnection) and obj not in self.connections:
            self.connections.append(obj)
        elif isinstance(obj, WorkspaceConnectionSecret) and obj not in self.secrets:
            self.secrets.append(obj)
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

    def begin_nested(self):
        return _InMemorySavepoint(self)

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = str(stmt)
        params = stmt.compile().params if hasattr(stmt, "compile") else {}

        if "FROM" in sql and "workspace_connection_secrets" in sql:
            c_id = params.get("connection_id_1")
            return _FakeResult([s for s in self.secrets if s.connection_id == c_id])

        if "FROM" in sql and "workspace_connections" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("id_1")
            if c_id is not None:
                return _FakeResult([c for c in self.connections if c.tenant_id == t_id and c.id == c_id])
            return _FakeResult([c for c in self.connections if c.tenant_id == t_id and c.is_active])

        if "FROM" in sql and "contact_identities" in sql:
            t_id = params.get("tenant_id_1")
            kind = params.get("kind_1")
            b_idx = params.get("blind_index_1")
            conn_id = params.get("connection_id_1")
            return _FakeResult(
                [
                    i
                    for i in self.identities
                    if i.tenant_id == t_id
                    and i.kind == kind
                    and i.blind_index == b_idx
                    and i.connection_id == conn_id
                ]
            )

        if "FROM" in sql and "contacts" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("id_1")
            if c_id is not None:
                return _FakeResult([c for c in self.contacts if c.tenant_id == t_id and c.id == c_id])
            return _FakeResult([(c.id, c.merged_into_id) for c in self.contacts if c.tenant_id == t_id])

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
            return _FakeResult(
                [
                    m
                    for m in self.messages
                    if m.tenant_id == t_id and m.connection_id == conn_id and m.channel == channel
                ]
            )

        if "FROM" in sql and "contact_channel_stats" in sql:
            t_id = params.get("tenant_id_1")
            c_id = params.get("contact_id_1")
            channel = params.get("channel_1")
            return _FakeResult(
                [s for s in self.stats if s.tenant_id == t_id and s.contact_id == c_id and s.channel == channel]
            )

        return _FakeResult([])


class _FakeImapServerClient:
    """Fake IMAP4_SSL client asserting read-only selection and BODY.PEEK-only fetches."""

    def __init__(self, folders_data: dict[str, dict[str, Any]]):
        self.folders_data = folders_data
        self.current_folder: str | None = None
        self.select_calls: list[tuple[str, bool]] = []
        self.uid_calls: list[tuple[str, Any, Any]] = []
        self.untagged_responses: dict[str, list[bytes]] = {}
        self.closed = False

    def select(self, mailbox: str = "INBOX", readonly: bool = False):
        assert readonly is True, "IMAP sync must always select folders with readonly=True"
        self.select_calls.append((mailbox, readonly))
        if mailbox not in self.folders_data:
            return "NO", [b"Folder not found"]
        self.current_folder = mailbox
        uv = self.folders_data[mailbox]["uidvalidity"]
        self.untagged_responses = {"UIDVALIDITY": [str(uv).encode("ascii")]}
        return "OK", [f"[UIDVALIDITY {uv}]".encode("ascii")]

    def uid(self, command: str, *args: Any):
        cmd_upper = command.upper()
        self.uid_calls.append((cmd_upper, args[0] if args else None, args[1] if len(args) > 1 else None))
        assert cmd_upper != "STORE", "IMAP sync must never modify flags with UID STORE"
        folder_info = self.folders_data[self.current_folder or "INBOX"]
        messages: dict[int, tuple[bytes, bytes]] = folder_info["messages"]

        if cmd_upper == "SEARCH":
            if folder_info.get("empty_search_reply"):
                # An OK answer with no data: not evidence that the folder is empty (B-14).
                return "OK", [None]
            criterion = str(args[1] or "")
            min_uid = 1
            if "UID " in criterion and ":*" in criterion:
                min_uid = int(criterion.split("UID ", 1)[1].split(":*", 1)[0])
            matching = [str(uid).encode("ascii") for uid in sorted(messages) if uid >= min_uid]
            return "OK", [b" ".join(matching)]

        if cmd_upper == "FETCH":
            uid_set_str = str(args[0])
            spec = str(args[1])
            assert "BODY.PEEK[" in spec, "IMAP sync must use BODY.PEEK so flags are never changed"
            requested_uids = [int(x) for x in uid_set_str.split(",") if x.isdigit()]
            custom_fetch = folder_info.get("custom_fetch")
            if callable(custom_fetch):
                return custom_fetch(requested_uids, messages)
            response_items: list[Any] = []
            for u in requested_uids:
                if u in messages:
                    flags_b, headers_b = messages[u]
                    meta = f"{u} (UID {u} FLAGS ({flags_b.decode('ascii')}) BODY[HEADER.FIELDS] {{{len(headers_b)}}})".encode(
                        "ascii"
                    )
                    response_items.append((meta, headers_b))
                    response_items.append(b")")
            return "OK", response_items

        return "BAD", []

    def shutdown(self):
        self.closed = True


@pytest.mark.asyncio
async def test_incremental_imap_sync_resumes_from_uid_and_handles_uidvalidity_reset():
    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=1,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="yandex_mail",
        auth_method="app_password",
        schema_version=1,
        label="Yandex Work",
        account_key="sales@yandex.ru",
        config={"account_login": "sales@yandex.ru", "imap_host": "imap.yandex.ru"},
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=1, secret_encrypted=encrypt_secret("app-secret-123")))

    msg1_headers = (
        b"From: =?utf-8?B?0JjQstCw0L0g0JrQu9C40LXQvdGC?= <ivan@client.ru>\r\n"
        b"To: sales@yandex.ru\r\n"
        b"Subject: =?utf-8?B?0JfQsNC60LDQtyA1MDA=?=\r\n"
        b"Date: Wed, 04 Nov 2026 10:00:00 +0000\r\n"
        b"Message-ID: <msg-101@client.ru>\r\n\r\n"
    )
    msg2_headers = (
        b"From: sales@yandex.ru\r\n"
        b"To: Ivan Client <ivan@client.ru>\r\n"
        b"Subject: Re: Order 500\r\n"
        b"Date: Wed, 04 Nov 2026 10:05:00 +0000\r\n"
        b"Message-ID: <msg-102@yandex.ru>\r\n"
        b"In-Reply-To: <msg-101@client.ru>\r\n"
        b"References: <msg-101@client.ru>\r\n\r\n"
    )
    msg3_headers = (
        b"From: Ivan Client <ivan@client.ru>\r\n"
        b"To: sales@yandex.ru\r\n"
        b"Subject: Re: Order 500\r\n"
        b"Date: Wed, 04 Nov 2026 10:10:00 +0000\r\n"
        b"Message-ID: <msg-103@client.ru>\r\n"
        b"In-Reply-To: <msg-102@yandex.ru>\r\n"
        b"References: <msg-101@client.ru> <msg-102@yandex.ru>\r\n\r\n"
    )

    folders_state = {
        "INBOX": {
            "uidvalidity": 100,
            "messages": {
                10: (b"", msg1_headers),
            },
        },
        "Sent": {
            "uidvalidity": 200,
            "messages": {
                5: (b"\\Seen", msg2_headers),
            },
        },
    }

    clients_created: list[_FakeImapServerClient] = []

    def _factory(provider_id: str, login: str, password: str) -> _FakeImapServerClient:
        assert provider_id == "yandex_mail"
        assert login == "sales@yandex.ru"
        assert password == "app-secret-123"
        c = _FakeImapServerClient(folders_state)
        clients_created.append(c)
        return c

    service = ImapSyncService(client_factory=_factory, batch_size=25)

    with tenant_scope(1):
        # Run 1: initial sync of INBOX (UID 10) and Sent (UID 5)
        res1 = await service.sync_connection(session, connection_id=1)
        assert res1["total_created"] == 2
        assert conn.config["imap_sync_state"]["INBOX"]["last_uid"] == 10
        assert conn.config["imap_sync_state"]["Sent"]["last_uid"] == 5
        assert len(session.messages) == 2
        assert len(session.threads) == 1
        assert len(session.contacts) == 1
        assert session.stats[0].inbound_count == 1
        assert session.stats[0].outbound_count == 1
        assert clients_created[-1].closed is True

        # Add UID 11 to INBOX and run incremental sync: only UID 11 is fetched
        folders_state["INBOX"]["messages"][11] = (b"", msg3_headers)
        res2 = await service.sync_connection(session, connection_id=1)
        assert res2["total_created"] == 1
        assert conn.config["imap_sync_state"]["INBOX"]["last_uid"] == 11
        assert len(session.messages) == 3
        assert session.stats[0].inbound_count == 2

        # Simulate UIDVALIDITY reset on INBOX from 100 -> 300 where UIDs 10, 11 become 70, 71
        folders_state["INBOX"] = {
            "uidvalidity": 300,
            "messages": {
                70: (b"", msg1_headers),
                71: (b"", msg3_headers),
            },
        }
        res3 = await service.sync_connection(session, connection_id=1)
        assert res3["total_created"] == 0
        assert res3["total_uid_rebound"] == 2
        assert conn.config["imap_sync_state"]["INBOX"]["uidvalidity"] == 300
        assert conn.config["imap_sync_state"]["INBOX"]["last_uid"] == 71
        assert len(session.messages) == 3
        assert session.stats[0].inbound_count == 2


@pytest.mark.asyncio
async def test_folder_configuration_and_email_sync_job_handler():
    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=2,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="mailru_mail",
        auth_method="app_password",
        schema_version=1,
        label="Mail.ru",
        account_key="team@mail.ru",
        config={"account_login": "team@mail.ru", "imap_host": "imap.mail.ru"},
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=2, secret_encrypted=encrypt_secret("pw-456")))

    assert get_imap_folders(conn) == DEFAULT_IMAP_FOLDERS
    with pytest.raises(ValueError):
        validate_folder_list(["INBOX; DROP TABLE"])

    fake_service = ImapSyncService(
        client_factory=lambda *a: _FakeImapServerClient(
            {
                "INBOX": {"uidvalidity": 1, "messages": {}},
                "Archive": {"uidvalidity": 2, "messages": {}},
            }
        )
    )

    with pytest.raises(TenantContextError):
        await handle_email_sync({}, sync_service=fake_service, session=session)

    with tenant_scope(1):
        configured = await configure_imap_folders(session, connection_id=2, folders=["INBOX", "Archive"])
        assert configured == ("INBOX", "Archive")
        assert get_imap_folders(conn) == ("INBOX", "Archive")

        job_res = await handle_email_sync(
            {"connection_id": 2},
            sync_service=fake_service,
            session=session,
        )
        assert job_res["job"] == JOB_NAME
        assert job_res["connections_synced"] == 1


def test_network_policy_and_d13_outbound_seam_isolation(monkeypatch):
    with pytest.raises(ImapSyncError) as exc_info:
        _resolve_imap_target("unknown_provider")
    assert exc_info.value.code == "unsupported_provider"

    # Reject loopback/private DNS answers through P14-T09 app.core.outbound seam
    monkeypatch.setattr(
        "app.core.outbound.socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("127.0.0.1", 993))],
    )
    with pytest.raises(ImapSyncError) as exc_priv:
        _resolve_imap_target("yandex_mail")
    assert exc_priv.value.code == "unsafe_address"

    # DNS failure maps to connection_failed
    def _raise_oserror(*a, **kw):
        raise OSError("dns failure")

    monkeypatch.setattr("app.core.outbound.socket.getaddrinfo", _raise_oserror)
    with pytest.raises(ImapSyncError) as exc_dns:
        _resolve_imap_target("yandex_mail")
    assert exc_dns.value.code == "connection_failed"

    # Accept global IP resolved by app.core.outbound.acquire_outbound_target
    monkeypatch.setattr(
        "app.core.outbound.socket.getaddrinfo",
        lambda *a, **kw: [(None, None, None, None, ("77.88.21.125", 993))],
    )
    host, addr = _resolve_imap_target("yandex_mail")
    assert host == "imap.yandex.ru"
    assert addr == "77.88.21.125"

    # Verify D13: _resolve_imap_target delegates directly to acquire_outbound_target
    seam_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.integrations.email.imap_sync.acquire_outbound_target",
        lambda host, port, *, policy=None, **kw: (
            seam_calls.append((host, port))
            or SimpleNamespace(host=host, addresses=("93.158.134.1",))
        ),
    )
    host2, addr2 = _resolve_imap_target("mailru_mail")
    assert host2 == "imap.mail.ru"
    assert addr2 == "93.158.134.1"
    assert seam_calls == [("imap.mail.ru", 993)]


def test_open_pinned_imap_client_error_mapping(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.email.imap_sync._resolve_imap_target",
        lambda provider_id: ("imap.yandex.ru", "77.88.21.125"),
    )

    class _DummyClient:
        def __init__(self, host: str, address: str):
            self.host = host
            self.address = address
            self.logged_in_as: str | None = None

        def login(self, user: str, password: str):
            if password == "bad":
                raise imaplib.IMAP4.error("auth failed")
            if password == "tls":
                raise ssl.SSLError("tls boom")
            if password == "abort":
                raise imaplib.IMAP4.abort("server abort")
            self.logged_in_as = user

        def shutdown(self):
            pass

    monkeypatch.setattr("app.integrations.email.imap_sync._PinnedIMAP", _DummyClient)

    client = _open_pinned_imap_client("yandex_mail", "user@yandex.ru", "good")
    assert client.logged_in_as == "user"

    with pytest.raises(ImapSyncError) as e_auth:
        _open_pinned_imap_client("yandex_mail", "user@yandex.ru", "bad")
    assert e_auth.value.code == "authentication_failed"

    with pytest.raises(ImapSyncError) as e_tls:
        _open_pinned_imap_client("yandex_mail", "user@yandex.ru", "tls")
    assert e_tls.value.code == "tls_failed"

    with pytest.raises(ImapSyncError) as e_abort:
        _open_pinned_imap_client("yandex_mail", "user@yandex.ru", "abort")
    assert e_abort.value.code == "service_unavailable"

    env = parse_raw_header_block(
        uid=1,
        folder="INBOX",
        header_bytes=b"From: a@b.com\r\nTo: c@d.com\r\nSubject: Hi\r\nDate: invalid-date\r\n\r\n",
        flags_bytes=b"\\Seen",
    )
    assert env.is_seen is True
    assert env.subject == "Hi"


@pytest.mark.asyncio
async def test_b1_same_message_in_two_folders_keeps_two_rows():
    """B-1 (Test 1): Same email in INBOX and Archive creates and preserves two separate rows."""
    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=10,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="yandex_mail",
        auth_method="app_password",
        schema_version=1,
        label="Yandex",
        account_key="sales@yandex.ru",
        config={
            "account_login": "sales@yandex.ru",
            "imap_host": "imap.yandex.ru",
            "imap_folders": ["INBOX", "Archive"],
        },
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=10, secret_encrypted=encrypt_secret("secret")))

    same_headers = (
        b"From: Client <client@example.com>\r\n"
        b"To: sales@yandex.ru\r\n"
        b"Subject: Contract\r\n"
        b"Date: Wed, 04 Nov 2026 10:00:00 +0000\r\n"
        b"Message-ID: <shared-across-folders@example.com>\r\n\r\n"
    )
    folders_state = {
        "INBOX": {"uidvalidity": 10, "messages": {1: (b"", same_headers)}},
        "Archive": {"uidvalidity": 20, "messages": {5: (b"\\Seen", same_headers)}},
    }
    service = ImapSyncService(client_factory=lambda *a: _FakeImapServerClient(folders_state))

    with tenant_scope(1):
        res = await service.sync_connection(session, connection_id=10)
        assert res["total_created"] == 2
        assert res["total_uid_rebound"] == 0
        assert len(session.messages) == 2
        assert {m.original_id for m in session.messages} == {"INBOX:10:1", "Archive:20:5"}


@pytest.mark.asyncio
async def test_b2_two_messages_sharing_message_id_in_same_folder_and_reset():
    """B-2 (Test 2): Two messages sharing a Message-ID do not crash sync and both rebind across UIDVALIDITY reset."""
    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=11,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="yandex_mail",
        auth_method="app_password",
        schema_version=1,
        label="Yandex",
        account_key="sales@yandex.ru",
        config={"account_login": "sales@yandex.ru", "imap_host": "imap.yandex.ru", "imap_folders": ["INBOX"]},
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=11, secret_encrypted=encrypt_secret("secret")))

    dup_headers_a = (
        b"From: Bot <bot@example.com>\r\n"
        b"To: sales@yandex.ru\r\n"
        b"Subject: Notification A\r\n"
        b"Date: Wed, 04 Nov 2026 10:00:00 +0000\r\n"
        b"Message-ID: <broken-mailer-fixed-id@example.com>\r\n\r\n"
    )
    dup_headers_b = (
        b"From: Bot <bot@example.com>\r\n"
        b"To: sales@yandex.ru\r\n"
        b"Subject: Notification B\r\n"
        b"Date: Wed, 04 Nov 2026 10:01:00 +0000\r\n"
        b"Message-ID: <broken-mailer-fixed-id@example.com>\r\n\r\n"
    )
    folders_state = {
        "INBOX": {
            "uidvalidity": 100,
            "messages": {
                1: (b"", dup_headers_a),
                2: (b"", dup_headers_b),
            },
        }
    }
    service = ImapSyncService(client_factory=lambda *a: _FakeImapServerClient(folders_state))

    with tenant_scope(1):
        res1 = await service.sync_connection(session, connection_id=11)
        assert res1["total_created"] == 2
        assert len(session.messages) == 2

        # Trigger UIDVALIDITY reset (100 -> 200) where UIDs 1, 2 become 51, 52
        folders_state["INBOX"] = {
            "uidvalidity": 200,
            "messages": {
                51: (b"", dup_headers_a),
                52: (b"", dup_headers_b),
            },
        }
        res2 = await service.sync_connection(session, connection_id=11)
        assert res2["total_created"] == 0
        assert res2["total_uid_rebound"] == 2
        assert len(session.messages) == 2
        assert {m.original_id for m in session.messages} == {"INBOX:200:51", "INBOX:200:52"}


@pytest.mark.asyncio
async def test_b3_message_without_message_id_rebinds_across_uidvalidity_reset():
    """B-3 (Test 3): Message without Message-ID re-binds via header content_hash across UIDVALIDITY reset."""
    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=12,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="yandex_mail",
        auth_method="app_password",
        schema_version=1,
        label="Yandex",
        account_key="sales@yandex.ru",
        config={"account_login": "sales@yandex.ru", "imap_host": "imap.yandex.ru", "imap_folders": ["INBOX"]},
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=12, secret_encrypted=encrypt_secret("secret")))

    no_msgid_headers = (
        b"From: Legacy Daemon <daemon@example.com>\r\n"
        b"To: sales@yandex.ru\r\n"
        b"Subject: Cron Report\r\n"
        b"Date: Wed, 04 Nov 2026 11:00:00 +0000\r\n\r\n"
    )
    folders_state = {
        "INBOX": {
            "uidvalidity": 10,
            "messages": {
                7: (b"", no_msgid_headers),
            },
        }
    }
    service = ImapSyncService(client_factory=lambda *a: _FakeImapServerClient(folders_state))

    with tenant_scope(1):
        res1 = await service.sync_connection(session, connection_id=12)
        assert res1["total_created"] == 1
        assert session.messages[0].external_message_id_hash is None
        assert session.messages[0].original_id == "INBOX:10:7"

        # UIDVALIDITY reset (10 -> 11) where UID 7 becomes UID 42
        folders_state["INBOX"] = {
            "uidvalidity": 11,
            "messages": {
                42: (b"\\Seen", no_msgid_headers),
            },
        }
        res2 = await service.sync_connection(session, connection_id=12)
        assert res2["total_created"] == 0
        assert res2["total_uid_rebound"] == 1
        assert len(session.messages) == 1
        assert session.messages[0].original_id == "INBOX:11:42"
        assert session.messages[0].is_read is True


@pytest.mark.asyncio
async def test_b4_unparseable_fetch_reply_does_not_advance_cursor_past_failed_uid():
    """B-4 (Test 4): Unparseable FETCH reply makes batch unsuccessful and keeps last_uid below failed UID."""
    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=13,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="yandex_mail",
        auth_method="app_password",
        schema_version=1,
        label="Yandex",
        account_key="sales@yandex.ru",
        config={"account_login": "sales@yandex.ru", "imap_host": "imap.yandex.ru", "imap_folders": ["INBOX"]},
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=13, secret_encrypted=encrypt_secret("secret")))

    h14 = b"From: a@b.ru\r\nTo: sales@yandex.ru\r\nSubject: M14\r\nMessage-ID: <m14@b.ru>\r\n\r\n"
    h15 = b"From: a@b.ru\r\nTo: sales@yandex.ru\r\nSubject: M15\r\nMessage-ID: <m15@b.ru>\r\n\r\n"
    h16 = b"From: a@b.ru\r\nTo: sales@yandex.ru\r\nSubject: M16\r\nMessage-ID: <m16@b.ru>\r\n\r\n"

    corrupt_15 = True

    def _custom_fetch(requested_uids: list[int], messages: dict[int, tuple[bytes, bytes]]):
        items: list[Any] = []
        for u in requested_uids:
            if u == 15 and corrupt_15:
                # Malformed FETCH item missing UID token
                items.append((b"15 (FLAGS (\\Seen) BODY[HEADER.FIELDS] {10})", h15))
                items.append(b")")
            elif u in messages:
                flags_b, headers_b = messages[u]
                meta = f"{u} (UID {u} FLAGS ({flags_b.decode('ascii')}) BODY[HEADER.FIELDS] {{{len(headers_b)}}})".encode(
                    "ascii"
                )
                items.append((meta, headers_b))
                items.append(b")")
        return "OK", items

    folders_state = {
        "INBOX": {
            "uidvalidity": 50,
            "messages": {
                14: (b"", h14),
                15: (b"", h15),
                16: (b"", h16),
            },
            "custom_fetch": _custom_fetch,
        }
    }
    # Batch 1 = [14] (succeeds), Batch 2 = [15, 16] (fails on UID 15)
    service = ImapSyncService(client_factory=lambda *a: _FakeImapServerClient(folders_state), batch_size=1)

    with tenant_scope(1):
        res1 = await service.sync_connection(session, connection_id=13)
        assert res1["total_created"] == 1
        # Cursor advanced to 14 (Batch 1), stopped before failed Batch 2 (UID 15)
        assert conn.config["imap_sync_state"]["INBOX"]["last_uid"] == 14
        assert len(session.messages) == 1

        # Fix server response for UID 15 and re-run: UIDs 15 and 16 are both fetched and ingested
        corrupt_15 = False
        res2 = await service.sync_connection(session, connection_id=13)
        assert res2["total_created"] == 2
        assert conn.config["imap_sync_state"]["INBOX"]["last_uid"] == 16
        assert len(session.messages) == 3


@pytest.mark.asyncio
async def test_b1_b4_rescan_after_uidvalidity_reset_creates_zero_new_rows():
    """B-1..B-4 (Test 5): Re-scan after UIDVALIDITY reset creates zero new rows across all edge cases."""
    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=14,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="yandex_mail",
        auth_method="app_password",
        schema_version=1,
        label="Yandex",
        account_key="sales@yandex.ru",
        config={
            "account_login": "sales@yandex.ru",
            "imap_host": "imap.yandex.ru",
            "imap_folders": ["INBOX", "Archive"],
        },
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=14, secret_encrypted=encrypt_secret("secret")))

    h_shared_folder = b"From: c@d.ru\r\nTo: sales@yandex.ru\r\nSubject: FolderCopy\r\nMessage-ID: <fcopy@d.ru>\r\n\r\n"
    h_dup_id = b"From: c@d.ru\r\nTo: sales@yandex.ru\r\nSubject: DupID\r\nMessage-ID: <dupid@d.ru>\r\n\r\n"
    h_no_id = b"From: c@d.ru\r\nTo: sales@yandex.ru\r\nSubject: NoID\r\nDate: Wed, 04 Nov 2026 12:00:00 +0000\r\n\r\n"

    folders_state = {
        "INBOX": {
            "uidvalidity": 100,
            "messages": {
                1: (b"", h_shared_folder),
                2: (b"", h_dup_id),
                3: (b"", h_dup_id),
                4: (b"", h_no_id),
            },
        },
        "Archive": {
            "uidvalidity": 200,
            "messages": {
                9: (b"\\Seen", h_shared_folder),
            },
        },
    }
    service = ImapSyncService(client_factory=lambda *a: _FakeImapServerClient(folders_state))

    with tenant_scope(1):
        init_res = await service.sync_connection(session, connection_id=14)
        assert init_res["total_created"] == 5
        assert len(session.messages) == 5

        # UIDVALIDITY reset on both folders
        folders_state["INBOX"] = {
            "uidvalidity": 101,
            "messages": {
                11: (b"", h_shared_folder),
                12: (b"", h_dup_id),
                13: (b"", h_dup_id),
                14: (b"", h_no_id),
            },
        }
        folders_state["Archive"] = {
            "uidvalidity": 201,
            "messages": {
                19: (b"\\Seen", h_shared_folder),
            },
        }
        reset_res = await service.sync_connection(session, connection_id=14)
        assert reset_res["total_created"] == 0
        assert reset_res["total_uid_rebound"] == 5
        assert len(session.messages) == 5

        # Immediate subsequent re-scan after UIDVALIDITY reset also creates zero new rows
        rescan_res = await service.sync_connection(session, connection_id=14)
        assert rescan_res["total_created"] == 0
        assert rescan_res["total_uid_rebound"] == 0
        assert len(session.messages) == 5


@pytest.mark.asyncio
async def test_b13_one_failing_connection_keeps_the_other_connection_rows():
    """B-13: a failing connection rolls back only its own savepoint; an earlier connection's rows survive."""

    class _FailSecondService:
        def __init__(self):
            self.calls: list[int] = []

        async def sync_connection(self, session, *, connection_id, folders=None):
            self.calls.append(connection_id)
            if connection_id == 2:
                raise RuntimeError("imap_session_expired")
            session.add(
                UnifiedMessage(
                    tenant_id=1,
                    connection_id=connection_id,
                    channel="email",
                    original_id=f"INBOX:1:{connection_id}",
                    direction="inbound",
                    sent_at=datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc),
                    received_at=datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc),
                    content_hash="sha256:" + str(connection_id) * 64,
                    is_read=False,
                )
            )
            return {"connection_id": connection_id, "created": 1}

    session = _InMemoryImapDbSession()
    for cid, provider in ((1, "mailru_mail"), (2, "yandex_mail")):
        session.add(
            WorkspaceConnection(
                id=cid,
                tenant_id=1,
                owner_user_id=7,
                platform="email",
                provider_id=provider,
                auth_method="app_password",
                schema_version=1,
                label=f"Mail {cid}",
                account_key=f"team{cid}@example.com",
                config={"account_login": f"team{cid}@example.com", "imap_host": "imap.example.com"},
                is_active=True,
                state="verified",
            )
        )

    service = _FailSecondService()
    with tenant_scope(1):
        result = await handle_email_sync({}, sync_service=service, session=session)

    assert service.calls == [1, 2]
    assert result["connections_synced"] == 1
    assert result["errors"] == [{"connection_id": 2, "error": "RuntimeError"}]
    assert [m.connection_id for m in session.messages] == [1]


@pytest.mark.asyncio
async def test_b14_empty_search_reply_never_marks_the_folder_unavailable():
    """B-14: an OK SEARCH reply without data leaves the stored backlog untouched."""

    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=21,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="mailru_mail",
        auth_method="app_password",
        schema_version=1,
        label="Mail.ru",
        account_key="sales@mail.ru",
        config={"account_login": "sales@mail.ru", "imap_host": "imap.mail.ru", "imap_folders": ["INBOX"]},
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=21, secret_encrypted=encrypt_secret("pw")))

    headers = (
        b"From: Client <client@example.com>\r\n"
        b"To: sales@mail.ru\r\n"
        b"Subject: Invoice\r\n"
        b"Date: Wed, 04 Nov 2026 10:00:00 +0000\r\n"
        b"Message-ID: <invoice-1@example.com>\r\n\r\n"
    )
    folders_state: dict[str, dict[str, Any]] = {"INBOX": {"uidvalidity": 5, "messages": {1: (b"", headers)}}}
    service = ImapSyncService(client_factory=lambda *a: _FakeImapServerClient(folders_state))

    with tenant_scope(1):
        first = await service.sync_connection(session, connection_id=21)
        assert first["total_created"] == 1
        assert [m.unavailable_at for m in session.messages] == [None]

        # The server now answers the scan with no data although the folder still has content.
        folders_state["INBOX"] = {"uidvalidity": 5, "messages": {1: (b"", headers)}, "empty_search_reply": True}
        second = await service.sync_connection(session, connection_id=21)

        assert second["total_marked_unavailable"] == 0
        assert [m.unavailable_at for m in session.messages] == [None], "an empty reply is not proof of deletion"


@pytest.mark.asyncio
async def test_b5_b6_dispatcher_bootstrap_registration_and_imap_expunge_marks_unavailable():
    """B-5, B-6: comms.email.sync is registered in HANDLERS/DEFAULT_TASKS and expunged UIDs mark unavailable_at."""
    from app.jobs.dispatcher import _LOG_JOB_TYPES, _job_label
    from app.jobs.handlers import HANDLERS
    from app.tasks.bootstrap import DEFAULT_TASKS

    assert "comms.email.sync" in HANDLERS
    assert "comms.email.sync" in _LOG_JOB_TYPES
    assert _job_label("comms.email.sync") == "Синхронизация почты"
    assert "comms-email-sync" in DEFAULT_TASKS
    assert DEFAULT_TASKS["comms-email-sync"][1] == "comms.email.sync"

    session = _InMemoryImapDbSession()
    conn = WorkspaceConnection(
        id=15,
        tenant_id=1,
        owner_user_id=7,
        platform="email",
        provider_id="yandex_mail",
        auth_method="app_password",
        schema_version=1,
        label="Yandex",
        account_key="sales@yandex.ru",
        config={"account_login": "sales@yandex.ru", "imap_host": "imap.yandex.ru", "imap_folders": ["INBOX"]},
        is_active=True,
        state="verified",
    )
    session.add(conn)
    session.add(WorkspaceConnectionSecret(connection_id=15, secret_encrypted=encrypt_secret("secret")))

    h1 = b"From: u@v.ru\r\nTo: sales@yandex.ru\r\nSubject: To Be Expunged\r\nMessage-ID: <exp-1@v.ru>\r\n\r\n"
    h2 = b"From: u@v.ru\r\nTo: sales@yandex.ru\r\nSubject: Kept\r\nMessage-ID: <kept-2@v.ru>\r\n\r\n"
    folders_state = {
        "INBOX": {
            "uidvalidity": 77,
            "messages": {
                10: (b"", h1),
                11: (b"", h2),
            },
        }
    }
    service = ImapSyncService(client_factory=lambda *a: _FakeImapServerClient(folders_state))

    with tenant_scope(1):
        await service.sync_connection(session, connection_id=15)
        msg10 = next(m for m in session.messages if m.original_id == "INBOX:77:10")
        msg11 = next(m for m in session.messages if m.original_id == "INBOX:77:11")
        assert msg10.is_unavailable is False
        assert msg11.is_unavailable is False

        # Expunge UID 10 from server
        del folders_state["INBOX"]["messages"][10]
        res_after_delete = await service.sync_connection(session, connection_id=15)
        assert res_after_delete["total_marked_unavailable"] == 1
        assert msg10.is_unavailable is True
        assert msg11.is_unavailable is False

