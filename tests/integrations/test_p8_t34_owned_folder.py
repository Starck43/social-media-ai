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
