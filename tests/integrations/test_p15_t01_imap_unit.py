"""DB-exempt unit and contract tests for P15-T01 IMAP incremental read sync."""

from __future__ import annotations

import imaplib
import ssl
import sys
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
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)


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
                    and (i.connection_id == conn_id or i.connection_id is None)
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
