"""Real test-schema authorization with mock HTTP; no real account or network."""

import asyncio
import secrets
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.catalog import CATALOG_VERSION
from app.models import (
    PlatformRole,
    Tenant,
    TenantUser,
    User,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
    WorkspaceRole,
)
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.yandex_reader import list_owned_folder
from app.utils.crypto import encrypt_secret

pytestmark = pytest.mark.tenancy
TEST_TOKEN = "p8t34-synthetic-token"


@pytest.fixture
async def workspace_data(monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    suffix = secrets.token_hex(5)
    async with async_session_maker() as session, session.begin():
        platform_role = (
            await session.execute(select(PlatformRole.id).where(PlatformRole.codename == "user"))
        ).scalar_one()
        roles = dict((await session.execute(select(WorkspaceRole.codename, WorkspaceRole.id))).all())
        users = [
            User(
                username=f"p8t34-{suffix}-{i}",
                email=f"p8t34-{suffix}-{i}@example.test",
                hashed_password="not-a-real-hash",
                role_id=platform_role,
                is_active=True,
            )
            for i in range(2)
        ]
        tenants = [Tenant(name=f"P8-T34 mock {i}", slug=f"p8t34-{suffix}-{i}") for i in range(2)]
        session.add_all(users + tenants)
        await session.flush()
        session.add_all(
            [
                TenantUser(
                    tenant_id=t.id,
                    user_id=u.id,
                    channel="web",
                    external_user_id=str(u.id),
                    role_id=roles["manager"],
                    is_active=True,
                )
                for t in tenants
                for u in users
            ]
        )
        conn = WorkspaceConnection(
            tenant_id=tenants[0].id,
            owner_user_id=users[0].id,
            platform="yandex_disk",
            provider_id="yandex_disk",
            auth_method="oauth_token",
            schema_version=CATALOG_VERSION,
            label="Mock drive",
            account_key=f"p8t34-{suffix}",
            config={
                "file_policy": {
                    "version": 1,
                    "root": "disk:/work",
                    "restrictions": [{"path": "disk:/work/private", "deny": ["list", "read"]}],
                }
            },
            is_active=True,
            state="unverified",
        )
        session.add(conn)
        await session.flush()
        session.add(WorkspaceConnectionSecret(connection_id=conn.id, secret_encrypted=encrypt_secret(TEST_TOKEN)))
    data = SimpleNamespace(users=users, tenants=tenants, roles=roles, connection_id=conn.id)
    try:
        yield data
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Tenant).where(Tenant.id.in_([t.id for t in tenants])))
            await session.execute(delete(User).where(User.id.in_([u.id for u in users])))


@contextmanager
def actor(data, *, user=0, tenant=0):
    with tenant_scope(data.tenants[tenant].id), permission_scope(data.users[user]):
        yield dict(tenant_id=data.tenants[tenant].id, actor_id=data.users[user].id, connection_id=data.connection_id)


def provider_response():
    return {
        "path": "disk:/work",
        "type": "dir",
        "_embedded": {
            "offset": 0,
            "items": [
                {"path": "disk:/work/private", "name": "private", "type": "dir"},
                {"path": "disk:/work/report.txt", "name": "report.txt", "type": "file"},
            ],
        },
    }


async def test_owner_read_and_hidden_child(workspace_data, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=provider_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with actor(workspace_data) as args:
            page = await list_owned_folder(**args, folder="/work", client=client)
            assert [item.name for item in page.items] == ["report.txt"]
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder="/work/private", client=client)
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder="/workspace", client=client)
    assert len(calls) == 1
    assert calls[0].headers["authorization"] == f"OAuth {TEST_TOKEN}"
    assert TEST_TOKEN not in repr(page) + caplog.text


async def test_foreign_owner_and_tenant_fail_before_provider(workspace_data):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
        for user, tenant in ((1, 0), (0, 1), (1, 1)):
            with actor(workspace_data, user=user, tenant=tenant) as args:
                with pytest.raises(ConnectionDenied):
                    await list_owned_folder(**args, folder="/work", client=client)
        with actor(workspace_data) as args:
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(
                    **{**args, "actor_id": workspace_data.users[1].id}, folder="/work", client=client
                )
    assert calls == []


@pytest.mark.parametrize(
    "revoked", ["user", "tenant", "membership", "role", "connection", "secret", "provider", "schema", "policy"]
)
async def test_revocation_or_invalid_state_fails_closed(workspace_data, revoked):
    with actor(workspace_data) as args:
        async with async_session_maker() as session, session.begin():
            if revoked == "user":
                await session.execute(update(User).where(User.id == args["actor_id"]).values(is_active=False))
            elif revoked == "tenant":
                await session.execute(update(Tenant).where(Tenant.id == args["tenant_id"]).values(is_active=False))
            elif revoked in {"membership", "role"}:
                values = {"is_active": False} if revoked == "membership" else {"role_id": None}
                await session.execute(
                    update(TenantUser)
                    .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                    .values(**values)
                )
            elif revoked == "secret":
                await session.execute(
                    delete(WorkspaceConnectionSecret).where(
                        WorkspaceConnectionSecret.connection_id == args["connection_id"]
                    )
                )
            else:
                values = {
                    "connection": {"is_active": False},
                    "provider": {"provider_id": "mailru_mail"},
                    "schema": {"schema_version": 999},
                    "policy": {
                        "config": {
                            "file_policy": {
                                "version": 1,
                                "root": "/work",
                                "restrictions": [{"path": "/work/private", "deny": ["list", "unknown"]}],
                            }
                        }
                    },
                }[revoked]
                await session.execute(
                    update(WorkspaceConnection).where(WorkspaceConnection.id == args["connection_id"]).values(**values)
                )
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder="/work", client=client)
        assert calls == []


async def test_revocation_waits_for_inflight_read_and_blocks_next(workspace_data):
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json=provider_response())

    with actor(workspace_data) as args:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reading = asyncio.create_task(list_owned_folder(**args, folder="/work", client=client))
            revoking = None
            try:
                await asyncio.wait_for(started.wait(), timeout=5)

                async def revoke():
                    async with async_session_maker() as session, session.begin():
                        await session.execute(
                            update(WorkspaceConnection)
                            .where(WorkspaceConnection.id == args["connection_id"])
                            .values(is_active=False)
                        )

                revoking = asyncio.create_task(revoke())
                await asyncio.sleep(0.1)
                assert not revoking.done()
                release.set()
                assert (await asyncio.wait_for(reading, timeout=5)).items
                await asyncio.wait_for(revoking, timeout=5)
                with pytest.raises(ConnectionDenied):
                    await list_owned_folder(**args, folder="/work", client=client)
            finally:
                release.set()
                if not reading.done():
                    reading.cancel()
                    await asyncio.gather(reading, return_exceptions=True)
                if revoking is not None and not revoking.done():
                    await asyncio.wait_for(revoking, timeout=5)


async def test_policy_tightening_applies_to_next_request(workspace_data):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=provider_response())

    with actor(workspace_data) as args:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert (await list_owned_folder(**args, folder="/work", client=client)).items
            async with async_session_maker() as session, session.begin():
                await session.execute(
                    update(WorkspaceConnection)
                    .where(WorkspaceConnection.id == args["connection_id"])
                    .values(
                        config={
                            "file_policy": {
                                "version": 1,
                                "root": "/work",
                                "restrictions": [{"path": "/work", "deny": ["list", "read"]}],
                            }
                        }
                    )
                )
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder="/work", client=client)
    assert len(calls) == 1


async def test_viewer_can_read_own_connection_but_cannot_cross_owner(workspace_data):
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(TenantUser)
            .where(
                TenantUser.tenant_id == workspace_data.tenants[0].id, TenantUser.user_id == workspace_data.users[0].id
            )
            .values(role_id=workspace_data.roles["viewer"])
        )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=provider_response()))
    ) as client:
        with actor(workspace_data) as args:
            assert (await list_owned_folder(**args, folder="/work", client=client)).items
        with actor(workspace_data, user=1) as args:
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder="/work", client=client)


async def test_create_connection_encrypts_secret_and_can_read_with_mock(workspace_data, caplog):
    from app.services.file_workspace.connections import create_yandex_connection
    from app.utils.crypto import decrypt_secret

    with actor(workspace_data) as args:
        result = await create_yandex_connection(
            tenant_id=args["tenant_id"],
            actor_id=args["actor_id"],
            label="Документы",
            root="/work",
            oauth_token=TEST_TOKEN,
        )
        assert result["state"] == "unverified" and TEST_TOKEN not in repr(result)
        async with async_session_maker() as session:
            row = await session.get(WorkspaceConnection, result["id"])
            secret = await session.get(WorkspaceConnectionSecret, result["id"])
            assert row.owner_user_id == args["actor_id"] and row.tenant_id == args["tenant_id"]
            assert row.config == {"file_policy": {"version": 1, "root": "/work", "restrictions": []}}
            assert TEST_TOKEN not in secret.secret_encrypted and decrypt_secret(secret.secret_encrypted) == TEST_TOKEN
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=provider_response()))
        ) as client:
            page = await list_owned_folder(**{**args, "connection_id": result["id"]}, folder="/work", client=client)
        assert {item.name for item in page.items} == {"private", "report.txt"}
    assert TEST_TOKEN not in caplog.text


@pytest.mark.parametrize(
    "label,root,token",
    [
        ("label", "/work/../other", TEST_TOKEN),
        ("label", "/work/%2f", TEST_TOKEN),
        ("bad\nlabel", "/work", TEST_TOKEN),
        ("label", "/work", "bad\ntoken"),
        ("label", "/work", ""),
        ("label", None, TEST_TOKEN),
    ],
)
async def test_create_rejects_bad_input_without_echo_or_write(workspace_data, label, root, token):
    from sqlalchemy import func

    from app.services.file_workspace.connections import FolderConnectionInputError, create_yandex_connection

    with actor(workspace_data) as args:
        async with async_session_maker() as session:
            before = (await session.execute(select(func.count()).select_from(WorkspaceConnection))).scalar_one()
        with pytest.raises(FolderConnectionInputError) as exc:
            await create_yandex_connection(
                tenant_id=args["tenant_id"], actor_id=args["actor_id"], label=label, root=root, oauth_token=token
            )
        assert TEST_TOKEN not in str(exc.value) and "bad\ntoken" not in str(exc.value)
        async with async_session_maker() as session:
            after = (await session.execute(select(func.count()).select_from(WorkspaceConnection))).scalar_one()
        assert after == before


async def test_create_requires_current_actor_and_create_grant(workspace_data):
    from app.services.file_workspace.connections import create_yandex_connection

    with actor(workspace_data) as args:
        with pytest.raises(ConnectionDenied):
            await create_yandex_connection(
                tenant_id=args["tenant_id"],
                actor_id=workspace_data.users[1].id,
                label="x",
                root="/work",
                oauth_token=TEST_TOKEN,
            )
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser)
                .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                .values(role_id=workspace_data.roles["viewer"])
            )
        with pytest.raises(ConnectionDenied):
            await create_yandex_connection(
                tenant_id=args["tenant_id"], actor_id=args["actor_id"], label="x", root="/work", oauth_token=TEST_TOKEN
            )


async def test_failed_encryption_rolls_back_setup(workspace_data, monkeypatch):
    from sqlalchemy import func

    from app.services.file_workspace import connections

    def fail(_):
        raise RuntimeError("simulated encryption failure")

    monkeypatch.setattr(connections, "encrypt_secret", fail)
    with actor(workspace_data) as args:
        async with async_session_maker() as session:
            before = (await session.execute(select(func.count()).select_from(WorkspaceConnection))).scalar_one()
        with pytest.raises(RuntimeError):
            await connections.create_yandex_connection(
                tenant_id=args["tenant_id"], actor_id=args["actor_id"], label="x", root="/work", oauth_token=TEST_TOKEN
            )
        async with async_session_maker() as session:
            after = (await session.execute(select(func.count()).select_from(WorkspaceConnection))).scalar_one()
        assert after == before


async def test_assembled_settings_save_and_disable_without_provider_io(workspace_data, caplog):
    import re
    from urllib.parse import urlencode

    from app.core.hashing import get_password_hash
    from app.main import create_application

    password = "p8t34-test-password"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id.in_([u.id for u in workspace_data.users]))
            .values(hashed_password=get_password_hash(password))
        )
    app = create_application()
    csrf_re = re.compile(r'name="_csrf" value="([^"]+)"')

    async def login(client, user):
        page = await client.get("/app/login")
        csrf = csrf_re.search(page.text).group(1)
        response = await client.post(
            "/app/login", data={"username": user.username, "password": password, "_csrf": csrf}
        )
        assert response.status_code in (302, 303)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        await login(client, workspace_data.users[0])
        page = await client.get("/app/settings?tab=connections")
        assert page.status_code == 200
        assert 'action="/app/settings/yandex-connections"' in page.text
        assert "может охватывать весь диск" in page.text and "ещё в разработке" in page.text
        csrf = csrf_re.search(page.text).group(1)
        form = {
            "_csrf": csrf,
            "label": "Mock personal disk",
            "root": "/work",
            "oauth_token": TEST_TOKEN,
            "scope_ack": "on",
        }
        assert (await client.post("/app/settings/yandex-connections", data={**form, "_csrf": "bad"})).status_code == 403
        assert (
            await client.post(
                "/app/settings/yandex-connections", data={k: v for k, v in form.items() if k != "scope_ack"}
            )
        ).status_code == 400
        assert (
            await client.post("/app/settings/yandex-connections", data={**form, "tenant_id": "123"})
        ).status_code == 400
        duplicate = urlencode([*form.items(), ("scope_ack", "on")])
        assert (
            await client.post(
                "/app/settings/yandex-connections",
                content=duplicate,
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        ).status_code == 400
        response = await client.post("/app/settings/yandex-connections", data=form)
        assert response.status_code == 303 and TEST_TOKEN not in response.text
        async with async_session_maker() as session:
            row = (
                await session.execute(
                    select(WorkspaceConnection).where(WorkspaceConnection.label == "Mock personal disk")
                )
            ).scalar_one()
            cid = row.id
            assert row.owner_user_id == workspace_data.users[0].id and row.state == "unverified"
        refreshed = await client.get("/app/settings?tab=connections")
        assert "Mock personal disk" in refreshed.text and TEST_TOKEN not in refreshed.text
        assert f'data-yandex-connection="{cid}"' in refreshed.text
        assert f'data-workspace-connection="{cid}"' not in refreshed.text
        assert (
            await client.post(f"/app/settings/workspace-connections/{cid}/check", data={"_csrf": csrf})
        ).status_code == 403
        assert (
            await client.post(f"/app/settings/yandex-connections/{cid}/disable", data={"_csrf": "bad"})
        ).status_code == 403
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=False
        ) as other:
            await login(other, workspace_data.users[1])
            other_page = await other.get("/app/settings?tab=connections")
            other_csrf = csrf_re.search(other_page.text).group(1)
            assert "Mock personal disk" not in other_page.text
            assert (
                await other.post(f"/app/settings/yandex-connections/{cid}/disable", data={"_csrf": other_csrf})
            ).status_code == 403
        assert (
            await client.post(f"/app/settings/yandex-connections/{cid}/disable", data={"_csrf": csrf})
        ).status_code == 303
        async with async_session_maker() as session:
            row = await session.get(WorkspaceConnection, cid)
            secret = await session.get(WorkspaceConnectionSecret, cid)
            assert row.is_active is False and row.state == "disabled" and secret.secret_encrypted == ""
    assert TEST_TOKEN not in caplog.text


@pytest.fixture
def pinned_http(monkeypatch):
    """Real bounded transport and service with a fake TLS opener, never sockets."""
    import json

    from app.integrations.file_workspace.yandex_http import YandexMetadataClient
    from app.services.file_workspace import yandex_reader

    state = SimpleNamespace(calls=[], response=provider_response(), status=b"200 OK", entered=None, release=None)

    class Reader:
        def __init__(self):
            body = json.dumps(state.response).encode()
            self.data = (
                b"HTTP/1.1 " + state.status + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            )

        async def read(self, count):
            if state.entered is not None:
                state.entered.set()
                await state.release.wait()
            head, self.data = self.data[:count], self.data[count:]
            return head

    class Writer:
        def __init__(self):
            self.data = b""
            self.closed = False

        def write(self, data):
            self.data += data

        async def drain(self):
            pass

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    async def opener():
        reader, writer = Reader(), Writer()
        state.calls.append(writer)
        return reader, writer

    monkeypatch.setattr(yandex_reader, "YandexMetadataClient", lambda: YandexMetadataClient(_opener=opener))
    return state


async def test_default_transport_checks_owner_tenant_and_restricted_children(workspace_data, pinned_http, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    with actor(workspace_data) as args:
        page = await list_owned_folder(**args, folder="/work")
        assert [item.name for item in page.items] == ["report.txt"]
        for folder in ("/work/private", "/workspace", "/work/../private"):
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder=folder)
    for user, tenant in ((1, 0), (0, 1), (1, 1)):
        with actor(workspace_data, user=user, tenant=tenant) as args:
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder="/work")
    assert len(pinned_http.calls) == 1
    writer = pinned_http.calls[0]
    assert writer.closed and b"GET /v1/disk/resources?" in writer.data
    assert f"OAuth {TEST_TOKEN}".encode() in writer.data
    assert TEST_TOKEN not in caplog.text and "disk:/work" not in caplog.text


@pytest.mark.parametrize("revoked", ["membership", "role", "connection", "secret", "policy"])
async def test_default_transport_revoked_access_never_opens(workspace_data, pinned_http, revoked):
    with actor(workspace_data) as args:
        async with async_session_maker() as session, session.begin():
            if revoked in {"membership", "role"}:
                values = {"is_active": False} if revoked == "membership" else {"role_id": None}
                await session.execute(
                    update(TenantUser)
                    .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                    .values(**values)
                )
            elif revoked == "secret":
                await session.execute(
                    delete(WorkspaceConnectionSecret).where(
                        WorkspaceConnectionSecret.connection_id == args["connection_id"]
                    )
                )
            else:
                values = {"is_active": False} if revoked == "connection" else {"config": {"file_policy": {}}}
                await session.execute(
                    update(WorkspaceConnection).where(WorkspaceConnection.id == args["connection_id"]).values(**values)
                )
        with pytest.raises(ConnectionDenied):
            await list_owned_folder(**args, folder="/work")
    assert pinned_http.calls == []


async def test_default_transport_reauthorization_error_is_sanitized(workspace_data, pinned_http, caplog):
    from app.integrations.file_workspace.yandex_disk import MetadataError

    pinned_http.status = b"401 Unauthorized"
    with actor(workspace_data) as args:
        with pytest.raises(MetadataError, match="^reauthorization_required$"):
            await list_owned_folder(**args, folder="/work")
    assert len(pinned_http.calls) == 1 and pinned_http.calls[0].closed
    assert TEST_TOKEN not in caplog.text


async def test_default_transport_revocation_waits_and_blocks_next_request(workspace_data, pinned_http):
    pinned_http.entered, pinned_http.release = asyncio.Event(), asyncio.Event()
    with actor(workspace_data) as args:
        reading = asyncio.create_task(list_owned_folder(**args, folder="/work"))
        revoking = None
        try:
            await asyncio.wait_for(pinned_http.entered.wait(), timeout=5)

            async def revoke():
                async with async_session_maker() as session, session.begin():
                    await session.execute(
                        update(WorkspaceConnection)
                        .where(WorkspaceConnection.id == args["connection_id"])
                        .values(is_active=False)
                    )

            revoking = asyncio.create_task(revoke())
            await asyncio.sleep(0.1)
            assert not revoking.done()
            pinned_http.release.set()
            assert (await asyncio.wait_for(reading, timeout=5)).items
            await asyncio.wait_for(revoking, timeout=5)
            with pytest.raises(ConnectionDenied):
                await list_owned_folder(**args, folder="/work")
            assert len(pinned_http.calls) == 1 and pinned_http.calls[0].closed
        finally:
            pinned_http.release.set()
            if not reading.done():
                reading.cancel()
                await asyncio.gather(reading, return_exceptions=True)
            if revoking is not None and not revoking.done():
                await asyncio.wait_for(revoking, timeout=5)


class _FormsParser:
    """Parse rendered HTML with the standard library, without browser or network."""

    @staticmethod
    def parse(html):
        from html.parser import HTMLParser

        class Parser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms = []
                self.tags = []
                self.current = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                self.tags.append(tag)
                if tag == "form":
                    self.current = {"action": attrs.get("action"), "fields": {}, "text": ""}
                    self.forms.append(self.current)
                elif tag in {"input", "button"} and self.current is not None and "name" in attrs:
                    self.current["fields"][attrs["name"]] = attrs.get("value", "")

            def handle_endtag(self, tag):
                if tag == "form":
                    self.current = None

            def handle_data(self, text):
                if self.current is not None:
                    self.current["text"] += text

        parser = Parser()
        parser.feed(html)
        return parser


async def test_assembled_browse_csrf_privacy_and_authorization(workspace_data, pinned_http, caplog):
    import logging
    import re
    from urllib.parse import urlencode

    from app.core.hashing import get_password_hash
    from app.main import create_application
    from app.services.file_workspace.connections import create_yandex_connection

    caplog.set_level(logging.DEBUG)
    password = "p8t34-test-password"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id.in_([u.id for u in workspace_data.users]))
            .values(hashed_password=get_password_hash(password))
        )
    with actor(workspace_data, user=1) as args:
        other = await create_yandex_connection(
            tenant_id=args["tenant_id"],
            actor_id=args["actor_id"],
            label="Other disk",
            root="/work",
            oauth_token=TEST_TOKEN,
        )
    dangerous = '<img src=x onerror="alert(1)">'
    pinned_http.response["_embedded"]["items"].extend(
        [
            {"path": f"disk:/work/{dangerous}", "name": dangerous, "type": "file"},
            {"path": "disk:/work/subfolder", "name": "subfolder", "type": "dir"},
        ]
    )
    app = create_application()
    transport = httpx.ASGITransport(app=app)
    cid = workspace_data.connection_id
    endpoint = f"/app/settings/yandex-connections/{cid}/browse"

    async def login_and_get_browse_token(client, user, connection_id):
        login = await client.get("/app/login")
        csrf = re.search(r'name="_csrf" value="([^"]+)"', login.text).group(1)
        response = await client.post(
            "/app/login", data={"username": user.username, "password": password, "_csrf": csrf}
        )
        assert response.status_code in (302, 303)
        settings_page = await client.get("/app/settings?tab=connections")
        parsed = _FormsParser.parse(settings_page.text)
        form = next(
            form
            for form in parsed.forms
            if form["action"] == f"/app/settings/yandex-connections/{connection_id}/browse"
        )
        return form["fields"]["_csrf"]

    async with (
        httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as owner,
        httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as stranger,
    ):
        csrf = await login_and_get_browse_token(owner, workspace_data.users[0], cid)
        other_csrf = await login_and_get_browse_token(stranger, workspace_data.users[1], other["id"])
        assert (await owner.get(endpoint)).status_code == 405
        assert (await owner.post(endpoint, data={"_csrf": "bad"})).status_code == 403
        # The legacy unbound web CSRF token is not accepted for this route.
        unbound = app.state.csrf_manager.generate_token()
        assert (await owner.post(endpoint, data={"_csrf": unbound})).status_code == 403
        assert (await owner.post(endpoint, data={"_csrf": other_csrf})).status_code == 403
        assert (await stranger.post(endpoint, data={"_csrf": csrf})).status_code == 403
        assert (await stranger.post(endpoint, data={"_csrf": other_csrf})).status_code == 403
        duplicate = urlencode([("_csrf", csrf), ("_csrf", csrf)])
        assert (
            await owner.post(endpoint, content=duplicate, headers={"content-type": "application/x-www-form-urlencoded"})
        ).status_code == 400
        assert (await owner.post(endpoint, data={"_csrf": csrf, "tenant_id": "123"})).status_code == 400
        assert (
            await owner.post(endpoint, data={"_csrf": csrf, "folder": "/work", "offset": "1000001"})
        ).status_code == 400
        assert (
            await owner.post(
                endpoint, content=b"a=" + b"x" * 65536, headers={"content-type": "application/x-www-form-urlencoded"}
            )
        ).status_code == 400
        assert (
            await owner.post(endpoint, data={"_csrf": csrf, "folder": "/work/private", "offset": "0"})
        ).status_code == 403
        assert (
            await owner.post(endpoint, data={"_csrf": csrf, "folder": "/work/../private", "offset": "0"})
        ).status_code == 403
        assert pinned_http.calls == []

        response = await owner.post(endpoint, data={"_csrf": csrf})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert TEST_TOKEN not in response.text and "report.txt" in response.text
        parsed = _FormsParser.parse(response.text)
        assert "script" not in parsed.tags and "img" not in parsed.tags
        assert dangerous not in response.text and "&lt;img" in response.text
        assert "Папка: subfolder" in response.text and "Папка: private" not in response.text
        child = next(form for form in parsed.forms if "subfolder" in form["text"])
        fields = child["fields"]
        assert fields["folder"] == "disk:/work/subfolder"
        assert len(pinned_http.calls) == 1 and pinned_http.calls[0].closed
        pinned_http.response = {"path": "disk:/work/subfolder", "type": "dir", "_embedded": {"offset": 0, "items": []}}
        nested = await owner.post(endpoint, data=fields)
        nested_parsed = _FormsParser.parse(nested.text)
        assert nested.status_code == 200 and "нет доступных элементов" in nested.text
        back = next(form for form in nested_parsed.forms if "На уровень выше" in form["text"])
        assert back["fields"]["folder"] == "disk:/work"
        assert len(pinned_http.calls) == 2
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(WorkspaceConnection).where(WorkspaceConnection.id == cid).values(is_active=False)
            )
        assert (await owner.post(endpoint, data=fields)).status_code == 403
        assert len(pinned_http.calls) == 2
    assert TEST_TOKEN not in caplog.text


async def test_assembled_browse_pagination_and_provider_error(workspace_data, pinned_http):
    import re

    from app.core.hashing import get_password_hash
    from app.main import create_application

    password = "p8t34-test-password"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id == workspace_data.users[0].id)
            .values(hashed_password=get_password_hash(password))
        )
    pinned_http.response = {
        "path": "disk:/work",
        "type": "dir",
        "_embedded": {
            "offset": 0,
            "items": [{"path": f"disk:/work/item-{i}", "name": f"item-{i}", "type": "file"} for i in range(100)],
        },
    }
    app = create_application()
    transport = httpx.ASGITransport(app=app)
    endpoint = f"/app/settings/yandex-connections/{workspace_data.connection_id}/browse"
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        login = await client.get("/app/login")
        csrf = re.search(r'name="_csrf" value="([^"]+)"', login.text).group(1)
        await client.post(
            "/app/login", data={"username": workspace_data.users[0].username, "password": password, "_csrf": csrf}
        )
        settings_page = await client.get("/app/settings?tab=connections")
        settings_parsed = _FormsParser.parse(settings_page.text)
        csrf = next(form for form in settings_parsed.forms if form["action"] == endpoint)["fields"]["_csrf"]
        first = await client.post(endpoint, data={"_csrf": csrf})
        assert first.status_code == 200
        parsed = _FormsParser.parse(first.text)
        next_form = next(form for form in parsed.forms if "Следующая страница" in form["text"])
        fields = next_form["fields"]
        assert fields["offset"] == "100" and fields["folder"] == "disk:/work"
        pinned_http.response["_embedded"] = {"offset": 100, "items": []}
        second = await client.post(endpoint, data=fields)
        assert second.status_code == 200 and "Предыдущая страница" in second.text
        assert "Следующая страница" not in second.text
        pinned_http.status = b"401 Unauthorized"
        error = await client.post(endpoint, data={"_csrf": csrf})
        assert error.status_code == 503 and "Проверьте токен" in error.text
        # A provider failure is not a folder view: no listing legend, no sibling nav.
        assert "Содержимое папки" not in error.text and "Следующая страница" not in error.text
        assert TEST_TOKEN not in error.text and error.headers["cache-control"] == "no-store"
        assert len(pinned_http.calls) == 3 and all(writer.closed for writer in pinned_http.calls)


async def test_settings_oauth_consent_verifies_and_stores_only_the_selected_folder(
    workspace_data, pinned_http, monkeypatch
):
    import json
    import re
    from urllib.parse import parse_qs, urlsplit

    from pydantic import SecretStr

    from app.core.hashing import get_password_hash
    from app.integrations.file_workspace import yandex_http
    from app.main import create_application
    from app.services.file_workspace import yandex_oauth
    from app.utils.crypto import decrypt_secret

    monkeypatch.setattr(settings, "YANDEX_DISK_CLIENT_ID", "test-yandex-client")
    monkeypatch.setattr(settings, "YANDEX_DISK_CLIENT_SECRET", SecretStr("test-yandex-client-secret"))
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")
    exchanged = []
    payload = {"access_token": TEST_TOKEN, "refresh_token": "discard-me"}

    async def exchange_stream():
        reader = asyncio.StreamReader()
        body = json.dumps(payload).encode()
        reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        reader.feed_eof()
        writer = _PickerWriter()
        exchanged.append(writer)
        return reader, writer

    monkeypatch.setattr(yandex_http, "open_yandex_oauth_tls", exchange_stream)
    actor = workspace_data.users[0]
    password = "p8t34-test-password"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User).where(User.id == actor.id).values(hashed_password=get_password_hash(password))
        )

    transport = httpx.ASGITransport(app=create_application())
    csrf_pattern = r'name="_csrf" value="([^"]+)"'
    start_path = "/app/settings/yandex-connections/oauth/start"
    callback = yandex_oauth.CALLBACK_PATH
    settings_path = "/app/settings?tab=connections"
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        login = await client.get("/app/login")
        login_csrf = re.search(csrf_pattern, login.text).group(1)
        assert (
            await client.post(
                "/app/login", data={"username": actor.username, "password": password, "_csrf": login_csrf}
            )
        ).status_code in (302, 303)
        page = await client.get(settings_path)
        oauth_form = page.text.split(f'action="{start_path}"', 1)[1].split("</form>", 1)[0]
        csrf = re.search(csrf_pattern, oauth_form).group(1)
        assert "Подключить через Яндекс" in oauth_form
        assert 'name="root"' not in oauth_form and 'name="label"' not in oauth_form
        form = {"_csrf": csrf}

        assert (await client.post(start_path, data={**form, "_csrf": login_csrf})).status_code == 403
        assert (await client.post(start_path, data={**form, "root": "/work"})).status_code == 400

        async def start():
            response = await client.post(start_path, data=form)
            assert response.status_code == 303
            assert response.headers["cache-control"] == "no-store"
            url = urlsplit(response.headers["location"])
            assert (url.scheme, url.hostname, url.path) == ("https", "oauth.yandex.ru", "/authorize")
            params = parse_qs(url.query)
            assert params["scope"] == ["cloud_api:disk.read"]
            assert params["force_confirm"] == ["yes"]
            assert params["code_challenge_method"] == ["S256"]
            assert params["redirect_uri"] == [f"http://localhost:8000{callback}"]
            assert "test-yandex-client-secret" not in url.query and "Документы" not in url.query
            return params["state"][0]

        state = await start()
        assert (await client.get(callback, params={"state": state, "error": "access_denied"})).status_code == 303
        assert (await client.get(callback, params={"state": state, "code": "reused"})).status_code == 303
        assert not exchanged
        target = "/Документы"
        query = select(WorkspaceConnection).where(
            WorkspaceConnection.owner_user_id == actor.id, WorkspaceConnection.label == "Яндекс.Диск"
        )

        payload["scope"] = "cloud_api:disk.read cloud_api:disk.write"
        state = await start()
        assert (await client.get(callback, params={"state": state, "code": "broad-scope"})).status_code == 303
        assert not pinned_http.calls
        del payload["scope"]

        pinned_http.status = b"503 Unavailable"
        state = await start()
        assert (await client.get(callback, params={"state": state, "code": "unavailable-folder"})).status_code == 303
        pinned_http.status = b"200 OK"
        home_items = [{"path": f"disk:{target}", "name": target[1:], "type": "dir"}]
        pinned_http.response = {"path": "disk:/", "type": "dir", "_embedded": {"offset": 0, "items": home_items}}
        picker = "/app/settings/cloud-folders/pick"

        def fields(response, label):
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert TEST_TOKEN not in response.text
            return next(form["fields"] for form in _FormsParser.parse(response.text).forms if label in form["text"])

        state = await start()
        response = await client.get(callback, params={"state": state, "code": "cancel-picker"})
        cancel = fields(response, "Отменить")
        assert (await client.post(picker, data=cancel)).status_code == 303
        assert (await client.post(picker, data={**cancel, "action": "list"})).status_code == 303

        state = await start()
        response = await client.get(callback, params={"state": state, "code": "provider-code"})
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "Использовать эту папку" not in response.text
        assert (await client.get(callback, params={"state": state, "code": "reused"})).status_code == 303
        assert b"POST /token HTTP/1.1" in exchanged[-1].data and all(writer.closed for writer in exchanged)
        async with async_session_maker() as session:
            assert await session.scalar(query) is None
        child = fields(response, target[1:])
        pinned_http.response = {"path": f"disk:{target}", "type": "dir"}
        opened = await client.post(picker, data=child)
        back = fields(opened, "На уровень выше")
        pinned_http.response = {"path": "disk:/", "type": "dir", "_embedded": {"offset": 0, "items": home_items}}
        response = await client.post(picker, data=back)
        child = fields(response, target[1:])
        pinned_http.response = {"path": f"disk:{target}", "type": "dir"}
        opened = await client.post(picker, data=child)
        confirm = fields(opened, "Использовать эту папку")
        calls = len(pinned_http.calls)
        response = await client.post(picker, data=confirm)
        assert response.status_code == 303
        assert response.headers["location"] == f"{settings_path}#conn-ws-yandex_disk"
        assert (await client.post(picker, data=confirm)).status_code == 303
        assert len(pinned_http.calls) == calls + 1
        assert b"limit=1" in pinned_http.calls[-1].data

        async with async_session_maker() as session:
            row = await session.scalar(query)
            secret = await session.get(WorkspaceConnectionSecret, row.id)
            assert row.state == "verified" and row.checked_at is not None
            assert row.owner_user_id == actor.id
            assert row.config["file_policy"] == {"version": 1, "root": target, "restrictions": []}
            assert secret.secret_encrypted != TEST_TOKEN and decrypt_secret(secret.secret_encrypted) == TEST_TOKEN
            connection_id = row.id

        refreshed = await client.get(settings_path)
        assert "Доступ к папке проверен при подключении" in refreshed.text
        assert TEST_TOKEN not in refreshed.text and "discard-me" not in refreshed.text
        disable_form = refreshed.text.split(f'/yandex-connections/{connection_id}/disable"', 1)[1]
        disable_csrf = re.search(csrf_pattern, disable_form).group(1)
        assert (
            await client.post(f"/app/settings/yandex-connections/{connection_id}/disable", data={"_csrf": disable_csrf})
        ).status_code == 303
        async with async_session_maker() as session:
            assert not (await session.get(WorkspaceConnection, connection_id)).is_active
            assert (await session.get(WorkspaceConnectionSecret, connection_id)).secret_encrypted == ""


class _PickerWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def test_mailru_webdav_settings_picker_and_revoke(workspace_data, monkeypatch):
    import json

    from app.core.hashing import get_password_hash
    from app.integrations.file_workspace import mailru_webdav
    from app.main import create_application
    from app.utils.crypto import decrypt_secret

    account, app_password = "example@mail.ru", "synthetic-app-password"
    actor = workspace_data.users[0]
    password = "p12t19-test-password"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User).where(User.id == actor.id).values(hashed_password=get_password_hash(password))
        )

    def xml(*paths):
        rows = "".join(
            f"<response><href>{path}</href><propstat><prop><resourcetype><collection/></resourcetype></prop>"
            "<status>HTTP/1.1 200 OK</status></propstat></response>"
            for path in paths
        )
        return f'<multistatus xmlns="DAV:">{rows}</multistatus>'.encode()

    data = SimpleNamespace(status=b"207 Multi-Status", body=xml("/", "/work/"), writers=[])

    async def opener():
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"HTTP/1.1 "
            + data.status
            + b"\r\nContent-Length: "
            + str(len(data.body)).encode()
            + b"\r\n\r\n"
            + data.body
        )
        reader.feed_eof()
        writer = _PickerWriter()
        data.writers.append(writer)
        return reader, writer

    monkeypatch.setattr(mailru_webdav, "open_mailru_tls", opener)
    settings_path = "/app/settings?tab=connections"
    start = "/app/settings/mailru-cloud-connections/start"
    picker = "/app/settings/cloud-folders/pick"

    def form(response, *, action=None, text=None):
        return next(
            f["fields"]
            for f in _FormsParser.parse(response.text).forms
            if (action is None or f["action"] == action) and (text is None or text in f["text"])
        )

    query = select(WorkspaceConnection).where(
        WorkspaceConnection.provider_id == "mailru_cloud", WorkspaceConnection.owner_user_id == actor.id
    )
    transport = httpx.ASGITransport(app=create_application())
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        login = await client.get("/app/login")
        csrf = form(login, action="/app/login")["_csrf"]
        await client.post("/app/login", data={"username": actor.username, "password": password, "_csrf": csrf})
        page = await client.get(settings_path)
        initial = form(page, action=start)
        assert initial["account"] == actor.email
        assert "https://id.mail.ru/security" in page.text
        webdav_scope = "Полный доступ к Облаку (WebDAV)"
        broader_scope = "полным доступом к почте, облаку и календарю"
        assert page.text.index(webdav_scope) < page.text.index(broader_scope)
        creds = {"_csrf": initial["_csrf"], "account": account, "password": f" {app_password} \r\n"}
        data.status = b"401 Unauthorized"
        assert (await client.post(start, data=creds)).status_code == 303
        data.status = b"207 Multi-Status"
        root_listing = data.body
        oversized = b" " * (256 * 1024 + 1)
        data.body = oversized
        too_large_message = "В этой папке слишком много файлов и подпапок для просмотра."
        rejected = await client.post(start, data=creds, follow_redirects=True)
        assert rejected.history[0].status_code == 303
        assert too_large_message in rejected.text
        data.body = root_listing
        started = await client.post(start, data=creds)
        assert started.status_code == 200 and app_password not in started.text
        async with async_session_maker() as session:
            assert await session.scalar(query) is None
        open_work = form(started, action=picker, text="work")
        data.body = xml("/work/", "/work/nested/")
        opened = await client.post(picker, data=open_work)
        confirm = form(opened, action=picker, text="Использовать эту папку")
        assert (await client.post(picker, data=confirm)).status_code == 303
        async with async_session_maker() as session:
            row = await session.scalar(query)
            assert row.state == "verified" and row.config["file_policy"]["root"] == "/work"
            secret = await session.get(WorkspaceConnectionSecret, row.id)
            assert json.loads(decrypt_secret(secret.secret_encrypted)) == [account, app_password]
            cid = row.id
        page = await client.get(settings_path)
        endpoint = f"/app/settings/mailru-cloud-connections/{cid}/browse"
        read = form(page, action=endpoint)
        assert (await client.post(endpoint, data=read)).status_code == 200
        data.body = oversized
        rejected = await client.post(endpoint, data=read)
        assert rejected.status_code == 503 and too_large_message in rejected.text
        total = len(data.writers)
        for bad in ("/workspace", "/work/../other", "/work/%2e%2e"):
            assert (await client.post(endpoint, data={**read, "folder": bad, "offset": "0"})).status_code == 403
        assert len(data.writers) == total
        assert all(w.closed and b"PROPFIND " in w.data and b"Depth: 1" in w.data for w in data.writers)
        disable = f"/app/settings/mailru-cloud-connections/{cid}/disable"
        assert (await client.post(disable, data=form(page, action=disable))).status_code == 303
        assert (await client.post(endpoint, data=read)).status_code == 403
        assert len(data.writers) == total
        async with async_session_maker() as session:
            assert (await session.get(WorkspaceConnectionSecret, cid)).secret_encrypted == ""
