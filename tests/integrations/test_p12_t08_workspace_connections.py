"""Real migrated PostgreSQL persistence/rights; all provider I/O is mocked."""

import asyncio
import json
import secrets
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.catalog import InvalidConnectorInput
from app.integrations.mail_check import CheckResult
from app.models import (
    PlatformRole,
    Tenant,
    TenantUser,
    User,
    UserCredential,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
    WorkspaceRole,
)
from app.models.managers import workspace_connection_manager as module
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.models.managers.workspace_connection_manager import workspace_connections as manager
from app.utils.crypto import decrypt_secret

pytestmark = pytest.mark.tenancy
TEST_SECRET = secrets.token_hex(18)


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
                username=f"p12t08-{suffix}-{i}",
                email=f"p12t08-{suffix}-{i}@example.com",
                hashed_password=secrets.token_hex(18),
                role_id=platform_role,
                is_active=True,
            )
            for i in range(2)
        ]
        tenants = [Tenant(name=f"P12-T08 mock {i}", slug=f"p12t08-{suffix}-{i}") for i in range(2)]
        session.add_all(users + tenants)
        await session.flush()
        members = [
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
        session.add_all(members)
    data = SimpleNamespace(users=users, tenants=tenants, roles=roles, members=members)
    try:
        yield data
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Tenant).where(Tenant.id.in_([t.id for t in tenants])))
            await session.execute(delete(User).where(User.id.in_([u.id for u in users])))


@contextmanager
def actor(data, *, user=0, tenant=0):
    with tenant_scope(data.tenants[tenant].id), permission_scope(data.users[user]):
        yield dict(tenant_id=data.tenants[tenant].id, actor_id=data.users[user].id)


async def save(arguments, login="first@example.com", **extra):
    return await manager.save(
        **arguments,
        provider_id="mailru_mail",
        method="app_password",
        label="Mock purchasing inbox",
        values={"account_login": login, "app_password": TEST_SECRET},
        **extra,
    )


async def test_atomic_encryption_multiple_accounts_and_safe_projection(workspace_data, caplog):
    with actor(workspace_data) as args:
        first = await save(args)
        second = await save(args, "second@example.com")
        assert first["id"] != second["id"]
        rows, can_write = await manager.list_for_actor(**args)
    assert len(rows) == 2 and can_write
    assert first["state"] == "unverified" and first["checked_at"] is None
    assert TEST_SECRET not in repr(rows) + caplog.text
    async with async_session_maker() as session:
        connection = await session.get(WorkspaceConnection, first["id"])
        secret = await session.get(WorkspaceConnectionSecret, first["id"])
        assert connection.config["send_enabled"] is False
        assert "app_password" not in json.dumps(connection.config)
        assert decrypt_secret(secret.secret_encrypted) == TEST_SECRET
        assert TEST_SECRET not in secret.secret_encrypted
        assert (
            await session.execute(
                select(func.count()).select_from(UserCredential).where(UserCredential.user_id == args["actor_id"])
            )
        ).scalar_one() == 0


async def test_failed_encryption_leaves_neither_record(workspace_data, monkeypatch):
    def broken(_):
        raise RuntimeError("synthetic encryption failure")

    monkeypatch.setattr(module, "encrypt_secret", broken)
    with actor(workspace_data) as args:
        with pytest.raises(RuntimeError):
            await save(args)
        rows, _ = await manager.list_for_actor(**args)
    assert rows == []


async def test_foreign_actor_workspace_and_ids_are_denied(workspace_data):
    with actor(workspace_data) as args:
        row = await save(args)
        with pytest.raises(ConnectionDenied):
            await manager.list_for_actor(**{**args, "actor_id": workspace_data.users[1].id})
        with pytest.raises(ConnectionDenied):
            await manager.list_for_actor(**{**args, "tenant_id": workspace_data.tenants[1].id})
    for user, tenant in ((1, 0), (0, 1)):
        with actor(workspace_data, user=user, tenant=tenant) as other:
            assert (await manager.list_for_actor(**other))[0] == []
            for operation in (manager.disable, manager.verify):
                with pytest.raises(ConnectionDenied):
                    await operation(**other, connection_id=row["id"])
            with pytest.raises(ConnectionDenied):
                await save(other, connection_id=row["id"])
    with actor(workspace_data, tenant=1) as other:
        assert (await save(other))["id"] != row["id"]


@pytest.mark.parametrize("target", ["user", "tenant", "membership", "role", "null_role"])
async def test_fresh_revocation_and_role_denial(workspace_data, target):
    with actor(workspace_data) as args:
        row = await save(args)
        async with async_session_maker() as session, session.begin():
            if target == "user":
                await session.execute(update(User).where(User.id == args["actor_id"]).values(is_active=False))
            elif target == "tenant":
                await session.execute(update(Tenant).where(Tenant.id == args["tenant_id"]).values(is_active=False))
            else:
                values = (
                    {"is_active": False}
                    if target == "membership"
                    else {"role_id": workspace_data.roles["viewer"] if target == "role" else None}
                )
                await session.execute(
                    update(TenantUser)
                    .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                    .values(**values)
                )
        for operation in (manager.disable, manager.verify):
            with pytest.raises(ConnectionDenied):
                await operation(**args, connection_id=row["id"])
        with pytest.raises(ConnectionDenied):
            await save(args, "second@example.com")
        if target != "role":
            with pytest.raises(ConnectionDenied):
                await manager.list_for_actor(**args)


@pytest.mark.parametrize(
    "result",
    [
        CheckResult("verified", "imap_authenticated"),
        CheckResult("reauth", "authentication_failed"),
        CheckResult("error", "connection_failed"),
    ],
)
async def test_check_states_edit_reset_disable_and_no_send(workspace_data, monkeypatch, result):
    calls = []

    def fake_check(*values):
        calls.append(values)
        return result

    monkeypatch.setattr(module, "check_mail", fake_check)
    with actor(workspace_data) as args:
        row = await save(args)
        assert calls == []
        checked = await manager.verify(**args, connection_id=row["id"])
        assert checked["state"] == result.state and checked["checked_at"] is not None
        assert calls == [("mailru_mail", "first@example.com", TEST_SECRET)]
        edited = await save(args, connection_id=row["id"])
        assert edited["state"] == "unverified" and edited["checked_at"] is None
        await manager.disable(**args, connection_id=row["id"])
        with pytest.raises(ConnectionDenied):
            await manager.verify(**args, connection_id=row["id"])
        rows, _ = await manager.list_for_actor(**args)
        assert rows[0]["state"] == "disabled"
    async with async_session_maker() as session:
        secret = await session.get(WorkspaceConnectionSecret, row["id"])
        assert secret.secret_encrypted == ""
        config = (await session.get(WorkspaceConnection, row["id"])).config
        assert config["send_enabled"] is False


async def test_duplicate_creation_is_database_enforced(workspace_data):
    with actor(workspace_data) as args:
        results = await asyncio.gather(save(args), save(args), return_exceptions=True)
        assert len([r for r in results if isinstance(r, dict)]) == 1
        assert len([r for r in results if isinstance(r, InvalidConnectorInput)]) == 1
        assert len((await manager.list_for_actor(**args))[0]) == 1


async def test_check_cannot_revive_concurrently_disabled_connection(workspace_data, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def fake_check(*_):
        started.set()
        assert release.wait(5)
        return CheckResult("verified", "imap_authenticated")

    monkeypatch.setattr(module, "check_mail", fake_check)
    with actor(workspace_data) as args:
        row = await save(args)
        checking = asyncio.create_task(manager.verify(**args, connection_id=row["id"]))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            disabling = asyncio.create_task(manager.disable(**args, connection_id=row["id"]))
            await asyncio.sleep(0.1)
            assert not disabling.done()
        finally:
            release.set()
        await checking
        await disabling
        assert (await manager.list_for_actor(**args))[0][0]["state"] == "disabled"


async def test_tampered_endpoint_is_not_executed_and_bad_key_is_safe(workspace_data, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module, "check_mail", lambda *a: (calls.append(a) or CheckResult("verified", "imap_authenticated"))
    )
    with actor(workspace_data) as args:
        row = await save(args)
        async with async_session_maker() as session, session.begin():
            connection = await session.get(WorkspaceConnection, row["id"])
            connection.config = {**connection.config, "imap_host": "127.0.0.1"}
        await manager.verify(**args, connection_id=row["id"])
        assert calls[0][0] == "mailru_mail"
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
        assert (await manager.verify(**args, connection_id=row["id"]))["state"] == "reauth"
        assert len(calls) == 1


async def test_settings_routes_save_check_csrf_and_foreign_id(workspace_data, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from app.web.settings import router

    app = FastAPI()
    app.include_router(router, prefix="/app")
    app.state.csrf_manager = SimpleNamespace(verify_token=lambda value: value == "valid")

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.web_user = workspace_data.users[0]
        request.state.tenant_id = workspace_data.tenants[0].id
        request.scope["session"] = {}
        with actor(workspace_data):
            return await call_next(request)

    monkeypatch.setattr(module, "check_mail", lambda *_: CheckResult("verified", "imap_authenticated"))
    form = dict(
        _csrf="valid",
        provider_id="yandex_mail",
        method="app_password",
        account_login="mock@yandex.ru",
        app_password=TEST_SECRET,
        label="Mock",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test.invalid", follow_redirects=False
    ) as client:
        assert (
            await client.post("/app/settings/workspace-connections", data={**form, "_csrf": "bad"})
        ).status_code == 403
        for extra in (
            {"tenant_id": str(workspace_data.tenants[1].id)},
            {"imap_host": "evil"},
            {"method": "oauth"},
            {"account_login": "not-mail"},
        ):
            response = await client.post("/app/settings/workspace-connections", data={**form, **extra})
            assert response.status_code == 303
            with actor(workspace_data) as args:
                assert (await manager.list_for_actor(**args))[0] == []
        response = await client.post("/app/settings/workspace-connections", data=form)
        assert response.status_code == 303 and "app_password" not in response.text
        with actor(workspace_data) as args:
            row = (await manager.list_for_actor(**args))[0][0]
        assert row["state"] == "unverified"
        assert (
            await client.post(f"/app/settings/workspace-connections/{row['id']}/check", data={"_csrf": "valid"})
        ).status_code == 303
        assert (
            await client.post(f"/app/settings/workspace-connections/{row['id']}/disable", data={"_csrf": "bad"})
        ).status_code == 403
        assert (
            await client.post("/app/settings/workspace-connections/2147483647/check", data={"_csrf": "valid"})
        ).status_code == 403
        # Legacy mail secrets are rejected rather than creating unscoped credentials.
        await client.post(
            "/app/settings/credentials",
            data={"_csrf": "valid", "kind": "email::app_password", "secret": TEST_SECRET},
        )
    async with async_session_maker() as session:
        assert (
            await session.execute(
                select(func.count()).select_from(UserCredential).where(UserCredential.user_id == args["actor_id"])
            )
        ).scalar_one() == 0


async def test_migration_round_trip_preserves_legacy_vault(workspace_data):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from app.utils.crypto import encrypt_secret
    from scripts.ci.schema_inventory import snapshot

    schema = settings.DB_SCHEMA
    before = snapshot(schema)
    async with async_session_maker() as session, session.begin():
        legacy = UserCredential(
            user_id=workspace_data.users[0].id,
            platform="vk",
            kind="user_token",
            secret_encrypted=encrypt_secret("synthetic-legacy"),
            meta={"legacy": "preserve"},
        )
        session.add(legacy)
        await session.flush()
        legacy_id = legacy.id
    env = {**os.environ, "DB_SCHEMA": schema}

    async def migrate(target):
        def run():
            return subprocess.run(
                [sys.executable, "-m", "alembic", target[0], target[1]],
                env=env,
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                timeout=45,
            )

        result = await asyncio.to_thread(run)
        assert result.returncode == 0, result.stderr[-2000:]

    try:
        await migrate(("downgrade", "0120"))
        down = snapshot(schema)
        assert "workspace_connections" not in down["tables"]
        assert "workspace_connection_secrets" not in down["tables"]
        assert down["alembic_version_max"] == "0120"
        async with async_session_maker() as session:
            old = await session.get(UserCredential, legacy_id)
            assert decrypt_secret(old.secret_encrypted) == "synthetic-legacy"
            assert old.meta == {"legacy": "preserve"}
    finally:
        await migrate(("upgrade", "0121"))
    assert snapshot(schema) == before
    with actor(workspace_data) as args:
        assert (await manager.list_for_actor(**args))[0] == []
        assert (await save(args))["state"] == "unverified"


async def test_disable_still_erases_secret_without_encryption_key(workspace_data, monkeypatch):
    with actor(workspace_data) as args:
        row = await save(args)
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", None)
        await manager.disable(**args, connection_id=row["id"])
    async with async_session_maker() as session:
        assert (await session.get(WorkspaceConnectionSecret, row["id"])).secret_encrypted == ""


async def test_cancelled_check_keeps_lock_until_transport_finishes(workspace_data, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def fake_check(*_):
        started.set()
        assert release.wait(5)
        return CheckResult("verified", "imap_authenticated")

    monkeypatch.setattr(module, "check_mail", fake_check)
    with actor(workspace_data) as args:
        row = await save(args)
        checking = asyncio.create_task(manager.verify(**args, connection_id=row["id"]))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            checking.cancel()
            disabling = asyncio.create_task(manager.disable(**args, connection_id=row["id"]))
            await asyncio.sleep(0.1)
            assert not checking.done() and not disabling.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await checking
        await disabling
        assert (await manager.list_for_actor(**args))[0][0]["state"] == "disabled"


async def test_membership_revocation_waits_for_check_then_denies_reuse(workspace_data, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def fake_check(*_):
        started.set()
        assert release.wait(5)
        return CheckResult("verified", "imap_authenticated")

    monkeypatch.setattr(module, "check_mail", fake_check)

    async def revoke(args):
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser)
                .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                .values(is_active=False)
            )

    with actor(workspace_data) as args:
        row = await save(args)
        checking = asyncio.create_task(manager.verify(**args, connection_id=row["id"]))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            revoking = asyncio.create_task(revoke(args))
            await asyncio.sleep(0.1)
            assert not revoking.done()
        finally:
            release.set()
        await checking
        await revoking
        with pytest.raises(ConnectionDenied):
            await manager.verify(**args, connection_id=row["id"])


@pytest.mark.parametrize(
    "role,allowed", [("owner", True), ("admin", True), ("manager", True), ("analyst", False), ("viewer", False)]
)
async def test_role_presets_and_unbound_context_fail_closed(workspace_data, role, allowed):
    with actor(workspace_data) as args:
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser)
                .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                .values(role_id=workspace_data.roles[role])
            )
        if allowed:
            row = await save(args)
            assert row["state"] == "unverified"
        else:
            with pytest.raises(ConnectionDenied):
                await save(args)
    with pytest.raises(ConnectionDenied):
        await manager.list_for_actor(**args)
