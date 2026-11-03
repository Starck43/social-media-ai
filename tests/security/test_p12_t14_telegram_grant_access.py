"""Synthetic PostgreSQL integration for source metadata and shared-account locks.

Use root conftest, the exclusive synthetic-schema lock/inventory/cleanup procedure
and fake provider boundaries. This tests metadata snapshots, not runtime leasing.
"""

import asyncio
import json
import secrets
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text, update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Platform, Source, Tenant, TenantUser, User
from app.models import WorkspaceAuthorizationGrant as Grant
from app.services.social import connector_authorizations as accounts
from app.services.social import telegram_account_lock as locking
from app.services.social import telegram_grant_access as access
from app.services.social.connector_authorizations import AuthorizationUnavailable, VerifiedTelegramResult
from app.services.social.connector_authorizations import connector_authorizations as manager
from app.types import PlatformType, SourceType
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def runtime_data(connector_data, monkeypatch):
    with actor(connector_data) as args:
        account, grant = await accounts.record_telegram_result(
            **args,
            result=VerifiedTelegramResult(
                subject_id=90013,
                api_id=123,
                api_hash="SYNTHETIC-HASH",
                session="SYNTHETIC-NOT-A-TELETHON-SESSION",
            ),
        )
    with actor(connector_data, tenant=1) as other:
        other_grant = await manager.attach(
            **other, authorization_id=account["authorization_id"], expected_revision=account["revision"]
        )
    async with async_session_maker() as session, session.begin():
        platform = (
            await session.execute(select(Platform).where(Platform.platform_type == PlatformType.TELEGRAM))
        ).scalar_one()
        source = Source(
            tenant_id=args["tenant_id"],
            platform_id=platform.id,
            external_id="p12t14-grant-" + secrets.token_hex(5),
            name="Synthetic grant snapshot",
            source_type=SourceType.CHANNEL,
            owner_user_id=args["actor_id"],
            params={
                "mode": "user",
                "telegram_authorization": {
                    "grant_id": grant["grant_id"],
                    "authorization_revision": account["revision"],
                },
            },
            is_active=True,
        )
        session.add(source)
        await session.flush()

    def forbid_decryption(*args, **kwargs):
        raise AssertionError("metadata snapshots must never decrypt a credential")

    monkeypatch.setattr(accounts, "decrypt_secret", forbid_decryption)
    try:
        yield SimpleNamespace(
            fixture=connector_data,
            args=args,
            source=source,
            account=account,
            grant=grant,
            other_grant=other_grant,
            pin=access.source_grant_selection(source),
        )
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(Source).where(Source.id == source.id))


async def test_fresh_snapshot_is_scoped_and_has_no_secret(runtime_data):
    data = runtime_data
    with tenant_scope(data.args["tenant_id"]):
        snapshot = await access.inspect_source_grant(data.pin)
    assert snapshot.authorization_id == data.account["authorization_id"]
    assert not hasattr(snapshot, "session") and not hasattr(snapshot, "secret_encrypted")
    with tenant_scope(data.fixture.tenants[1].id), pytest.raises(AuthorizationUnavailable):
        await access.inspect_source_grant(data.pin)


@pytest.mark.parametrize(
    "changed",
    ["source", "user", "tenant", "member", "role", "grant", "state", "revision", "grant_revision", "empty_secret"],
)
async def test_fresh_snapshot_denies_changed_source_owner_membership_consent_and_account(runtime_data, changed):
    data = runtime_data
    with tenant_scope(data.args["tenant_id"]):
        await access.inspect_source_grant(data.pin)
    async with async_session_maker() as session, session.begin():
        if changed == "source":
            statement = update(Source).where(Source.id == data.source.id).values(is_active=False)
        elif changed == "user":
            statement = update(User).where(User.id == data.args["actor_id"]).values(is_active=False)
        elif changed == "tenant":
            statement = update(Tenant).where(Tenant.id == data.args["tenant_id"]).values(is_active=False)
        elif changed in ("member", "role"):
            statement = (
                update(TenantUser)
                .where(
                    TenantUser.user_id == data.args["actor_id"],
                    TenantUser.tenant_id == data.args["tenant_id"],
                    TenantUser.channel == "web",
                )
                .values(**({"is_active": False} if changed == "member" else {"role_id": None}))
            )
        elif changed in ("grant", "grant_revision"):
            statement = (
                update(Grant)
                .where(Grant.id == data.grant["grant_id"])
                .values(**({"is_active": False} if changed == "grant" else {"authorization_revision": 2}))
            )
        else:
            values = {"state": "reauth"} if changed == "state" else {"revision": 2}
            if changed == "empty_secret":
                values = {"secret_encrypted": ""}
            statement = (
                update(Authorization).where(Authorization.id == data.account["authorization_id"]).values(**values)
            )
        await session.execute(statement)
    with tenant_scope(data.args["tenant_id"]), pytest.raises(AuthorizationUnavailable):
        await access.inspect_source_grant(data.pin)


@pytest.mark.parametrize("changed", ["owner", "grant", "revision", "target", "selection_removed"])
async def test_queued_pin_cannot_follow_source_edits(runtime_data, changed):
    data = runtime_data
    params = dict(data.source.params)
    values = {}
    if changed == "owner":
        params["token_owner"] = data.fixture.users[1].id
    elif changed == "grant":
        # A real grant of the same owner, but in the other workspace.
        params["telegram_authorization"] = {
            "grant_id": data.other_grant["grant_id"],
            "authorization_revision": 1,
        }
    elif changed == "revision":
        params["telegram_authorization"] = {"grant_id": data.grant["grant_id"], "authorization_revision": 2}
    elif changed == "target":
        values["external_id"] = "edited-synthetic-target"
    else:
        params.pop("telegram_authorization")
    async with async_session_maker() as session, session.begin():
        await session.execute(update(Source).where(Source.id == data.source.id).values(params=params, **values))
    with tenant_scope(data.args["tenant_id"]), pytest.raises(AuthorizationUnavailable):
        await access.inspect_source_grant(data.pin)


async def test_same_owner_foreign_workspace_grant_is_not_consent_here(runtime_data):
    data = runtime_data
    params = {
        "mode": "user",
        "telegram_authorization": {"grant_id": data.other_grant["grant_id"], "authorization_revision": 1},
    }
    async with async_session_maker() as session, session.begin():
        source = await session.get(Source, data.source.id)
        source.params = params
        pin = access.source_grant_selection(source)
    with tenant_scope(data.args["tenant_id"]), pytest.raises(AuthorizationUnavailable):
        await access.inspect_source_grant(pin)


async def test_local_revoke_invalidates_a_previously_checked_pin(runtime_data):
    data = runtime_data
    with actor(data.fixture) as args:
        await access.inspect_source_grant(data.pin)
        await manager.revoke(**args, authorization_id=data.account["authorization_id"], expected_revision=1)
        with pytest.raises(AuthorizationUnavailable):
            await access.inspect_source_grant(data.pin)


async def test_snapshot_does_not_promise_envelope_integrity_or_provider_health(runtime_data):
    data = runtime_data
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(Authorization)
            .where(Authorization.id == data.account["authorization_id"])
            .values(secret_encrypted="synthetic-unreadable-not-a-session")
        )
    # Deliberately metadata-only. The future leased request boundary must decrypt
    # and validate, then check Telegram; this object cannot authorize provider I/O.
    with tenant_scope(data.args["tenant_id"]):
        assert (await access.inspect_source_grant(data.pin)).authorization_id == data.account["authorization_id"]


async def add_source(data, *, tenant=1, independent=False):
    """Synthetic metadata only; unreadable ciphertext never proves session health."""
    async with async_session_maker() as session, session.begin():
        if independent:
            account = Authorization(
                owner_user_id=data.fixture.users[1].id,
                provider_id="telegram",
                auth_method="mtproto",
                subject_key="90014",
                revision=1,
                state="active",
                secret_encrypted="SYNTHETIC-UNREADABLE-LOCK-TEST",
            )
            session.add(account)
            await session.flush()
            grant = Grant(
                tenant_id=data.fixture.tenants[tenant].id,
                owner_user_id=account.owner_user_id,
                authorization_id=account.id,
                authorization_revision=1,
            )
            session.add(grant)
            await session.flush()
            grant_id, owner_id = grant.id, account.owner_user_id
        else:
            grant_id, owner_id = data.other_grant["grant_id"], data.args["actor_id"]
        source = Source(
            tenant_id=data.fixture.tenants[tenant].id,
            platform_id=data.source.platform_id,
            owner_user_id=owner_id,
            external_id="p12t14-lock-" + secrets.token_hex(5),
            name="Synthetic shared-account lock",
            source_type=SourceType.CHANNEL,
            params={"mode": "user", "telegram_authorization": {"grant_id": grant_id, "authorization_revision": 1}},
            is_active=True,
        )
        session.add(source)
        await session.flush()
        return access.source_grant_selection(source)


async def test_lock_excludes_same_account_across_workspaces_and_releases_on_exit(runtime_data):
    data = runtime_data
    second = await add_source(data)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            actual = (
                await guard._session.execute(
                    text("SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')")
                )
            ).one()
            assert tuple(actual) == ("read committed", "on")
            assert (await guard.inspect()).authorization_id == data.account["authorization_id"]
            with tenant_scope(second.tenant_id), pytest.raises(locking.TelegramAccountBusy):
                async with locking.locked_source_grant(second):
                    pytest.fail("same personal account must not be concurrent")
        with pytest.raises(AuthorizationUnavailable):
            await guard.inspect()
    with tenant_scope(second.tenant_id):
        async with locking.locked_source_grant(second) as later:
            assert (await later.inspect()).authorization_id == data.account["authorization_id"]


async def test_different_accounts_can_be_locked_concurrently(runtime_data):
    data = runtime_data
    other = await add_source(data, independent=True)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin):
            with tenant_scope(other.tenant_id):
                async with locking.locked_source_grant(other) as guard:
                    assert (await guard.inspect()).authorization_id != data.account["authorization_id"]


@pytest.mark.parametrize("cancel", [False, True])
async def test_lock_released_after_caller_error_or_cancellation(runtime_data, cancel):
    data = runtime_data
    entered, never = asyncio.Event(), asyncio.Event()

    async def holder():
        with tenant_scope(data.pin.tenant_id):
            async with locking.locked_source_grant(data.pin):
                entered.set()
                if cancel:
                    await never.wait()
                raise RuntimeError("synthetic callback failure")

    task = asyncio.create_task(holder())
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            await guard.inspect()


@pytest.mark.parametrize("change", ["target", "binding", "membership", "revoke"])
async def test_lock_does_not_freeze_revocation_and_rechecks_fresh_source_identity(runtime_data, change):
    data = runtime_data
    with actor(data.fixture) as args:
        async with locking.locked_source_grant(data.pin) as guard:
            await guard.inspect()  # Populate the identity map before another transaction changes it.
            if change == "revoke":
                await asyncio.wait_for(
                    manager.revoke(**args, authorization_id=data.account["authorization_id"], expected_revision=1),
                    timeout=5,
                )
            else:
                async with async_session_maker() as session, session.begin():
                    if change == "membership":
                        statement = (
                            update(TenantUser)
                            .where(TenantUser.user_id == args["actor_id"], TenantUser.tenant_id == args["tenant_id"])
                            .values(is_active=False)
                        )
                    else:
                        values = {"external_id": "edited-lock-target"} if change == "target" else {"params": {}}
                        statement = update(Source).where(Source.id == data.source.id).values(**values)
                    await session.execute(statement)
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()
            with pytest.raises(AuthorizationUnavailable):
                await guard.inspect()  # An invalid handle never silently recovers.


async def test_post_discovery_source_change_is_denied_and_lock_released(runtime_data, monkeypatch):
    data = runtime_data
    original, calls = locking._inspect_source_grant, 0

    async def change_after_discovery(session, selection):
        nonlocal calls
        calls += 1
        result = await original(session, selection)
        if calls == 1:
            async with async_session_maker() as writer, writer.begin():
                await writer.execute(
                    update(Source).where(Source.id == data.source.id).values(external_id="changed-between")
                )
        return result

    monkeypatch.setattr(locking, "_inspect_source_grant", change_after_discovery)
    with tenant_scope(data.pin.tenant_id), pytest.raises(AuthorizationUnavailable):
        async with locking.locked_source_grant(data.pin):
            pytest.fail("must not yield stale selection")
    assert calls == 2
    async with async_session_maker() as session, session.begin():
        acquired = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(721215, :id)"), {"id": data.account["authorization_id"]}
        )
        assert acquired is True


async def test_connection_loss_invalidates_guard_and_never_auto_replays(runtime_data):
    data = runtime_data
    with tenant_scope(data.pin.tenant_id), pytest.raises(AuthorizationUnavailable):
        async with locking.locked_source_grant(data.pin) as guard:
            async with async_session_maker() as session, session.begin():
                pid = await session.scalar(
                    text(
                        "SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=721215 "
                        "AND objid=:id AND objsubid=2 AND granted"
                    ),
                    {"id": data.account["authorization_id"]},
                )
                assert type(pid) is int
                assert await session.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}) is True
            await guard.inspect()
    with pytest.raises(AuthorizationUnavailable):
        await guard.inspect()
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as replacement:
            await replacement.inspect()


async def test_another_process_observes_shared_lock(runtime_data, tmp_path):
    data = runtime_data
    child = """
import asyncio,json,sys
from app.core.tenant_context import tenant_scope
from app.services.social.telegram_grant_access import SourceGrantSelection
from app.services.social.telegram_account_lock import locked_source_grant,TelegramAccountBusy
selection=SourceGrantSelection(**json.load(sys.stdin))
async def main():
    with tenant_scope(selection.tenant_id):
        try:
            async with locked_source_grant(selection):
                print("acquired")
        except TelegramAccountBusy:
            print("busy")
asyncio.run(main())
"""
    # Do not inherit deployment secrets or a .env into the independent worker.
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTHON_DOTENV_DISABLED": "1",
        "POSTGRES_URL": settings.POSTGRES_URL,
        "DB_SCHEMA": settings.DB_SCHEMA,
        "DB_POOL": "null",
        "SECRET_KEY": "SYNTHETIC-P12T14-CHILD-NOT-A-REAL-KEY",
        "REDIS_URL": "memory://",
    }

    def attempt():
        result = subprocess.run(
            [sys.executable, "-c", child],
            input=json.dumps(asdict(data.pin)),
            text=True,
            capture_output=True,
            cwd=tmp_path,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin):
            assert await asyncio.to_thread(attempt) == "busy"
    assert await asyncio.to_thread(attempt) == "acquired"


async def test_platform_display_name_is_not_provider_identity(runtime_data):
    data = runtime_data
    async with async_session_maker() as session, session.begin():
        platform = await session.get(Platform, data.source.platform_id)
        original = platform.name
        platform.name = "Synthetic localized Telegram " + secrets.token_hex(3)
    try:
        with tenant_scope(data.pin.tenant_id):
            async with locking.locked_source_grant(data.pin) as guard:
                await guard.inspect()
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(update(Platform).where(Platform.id == data.source.platform_id).values(name=original))


async def test_non_telegram_platform_never_supplies_telegram_consent(runtime_data):
    data = runtime_data
    async with async_session_maker() as session, session.begin():
        vk = (await session.execute(select(Platform.id).where(Platform.platform_type == PlatformType.VK))).scalar_one()
        source = await session.get(Source, data.source.id)
        source.platform_id = vk
        pin = access.source_grant_selection(source)
    with tenant_scope(pin.tenant_id), pytest.raises(AuthorizationUnavailable):
        async with locking.locked_source_grant(pin):
            pytest.fail("grant cannot change the source platform identity")


async def test_cancellation_during_post_lock_inspection_releases_lock(runtime_data, monkeypatch):
    data = runtime_data
    entered, never = asyncio.Event(), asyncio.Event()
    original, calls = locking._inspect_source_grant, 0

    async def paused_post_lock(session, selection):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await never.wait()
        return await original(session, selection)

    async def acquire():
        with tenant_scope(data.pin.tenant_id):
            async with locking.locked_source_grant(data.pin):
                pytest.fail("cancelled acquisition must not yield")

    with monkeypatch.context() as patch:
        patch.setattr(locking, "_inspect_source_grant", paused_post_lock)
        task = asyncio.create_task(acquire())
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    with tenant_scope(data.pin.tenant_id):
        async with locking.locked_source_grant(data.pin) as guard:
            await guard.inspect()
