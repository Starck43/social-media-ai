"""Personal-account reuse is explicit consent, not tenant-wide secret availability."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import async_session_maker
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Tenant, TenantUser, User, UserCredential
from app.models import WorkspaceAuthorizationGrant as Grant
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.social import connector_authorizations as module
from app.services.social.connector_authorizations import (
    AuthorizationAlreadyExists,
    AuthorizationUnavailable,
    VerifiedTelegramResult,
)
from app.services.social.connector_authorizations import connector_authorizations as manager
from app.services.social.connector_authorizations import (
    record_telegram_result,
)
from app.utils.crypto import decrypt_secret, encrypt_secret
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy
MATERIAL = {"api_id": 123, "api_hash": "SYNTHETIC-APPLICATION-HASH", "session": "SYNTHETIC-SESSION-NOT-FOR-TELETHON"}


async def record(args, subject=90001):
    return await record_telegram_result(**args, result=VerifiedTelegramResult(subject_id=subject, **MATERIAL))


async def attach(args, account):
    return await manager.attach(
        **args, authorization_id=account["authorization_id"], expected_revision=account["revision"]
    )


async def test_one_ciphertext_explicit_two_workspace_grants_no_legacy_vault(connector_data, caplog):
    with actor(connector_data) as first:
        account, original = await record(first)
    with actor(connector_data, tenant=1) as second:
        rows, can_write = await manager.list_reusable(**second)
        assert rows == [account] and can_write
        assert await manager.list_grants(**second) == []  # joining a workspace never auto-attaches
        linked = await attach(second, account)
        assert await attach(second, account) == linked
        assert linked["authorization_id"] == original["authorization_id"]
        assert linked["grant_id"] != original["grant_id"]
    async with async_session_maker() as session:
        accounts = (
            (await session.execute(select(Authorization).where(Authorization.owner_user_id == first["actor_id"])))
            .scalars()
            .all()
        )
        grants = (await session.execute(select(Grant).where(Grant.owner_user_id == first["actor_id"]))).scalars().all()
        assert len(accounts) == 1 and len(grants) == 2
        payload = json.loads(decrypt_secret(accounts[0].secret_encrypted))
        assert payload["material"] == MATERIAL
        assert MATERIAL["session"] not in accounts[0].secret_encrypted
        assert (
            await session.execute(
                select(func.count()).select_from(UserCredential).where(UserCredential.user_id == first["actor_id"])
            )
        ).scalar_one() == 0
    assert MATERIAL["session"] not in repr(rows) + caplog.text
    assert MATERIAL["api_hash"] not in repr(rows) + repr(VerifiedTelegramResult(subject_id=1, **MATERIAL)) + caplog.text


async def test_same_telegram_subject_of_another_app_user_is_never_borrowed(connector_data):
    with actor(connector_data) as first:
        account, _ = await record(first)
    with actor(connector_data, user=1) as other:
        assert (await manager.list_reusable(**other))[0] == []
        with pytest.raises(AuthorizationUnavailable):
            await attach(other, account)
        own, _ = await record(other)  # provider identity alone doesn't merge application users
        assert own["authorization_id"] != account["authorization_id"]


async def test_local_detach_and_personal_revoke_have_distinct_scope(connector_data):
    with actor(connector_data) as first:
        account, original = await record(first)
    with actor(connector_data, tenant=1) as second:
        linked = await attach(second, account)
        detached = await manager.detach(**second, grant_id=linked["grant_id"])
        assert detached["is_active"] is False
    with actor(connector_data) as first:
        assert (await manager.list_grants(**first))[0]["is_active"] is True
    with actor(connector_data, tenant=1) as second:
        assert (await attach(second, account))["grant_id"] == linked["grant_id"]
        revoked = await manager.revoke(**second, authorization_id=account["authorization_id"], expected_revision=1)
        assert revoked["state"] == "revoked" and revoked["revision"] == 2
        with pytest.raises(AuthorizationUnavailable):
            await attach(second, account)
    with actor(connector_data) as first:
        assert (await manager.list_grants(**first))[0]["is_active"] is False
        assert (await manager.list_reusable(**first))[0] == []
    async with async_session_maker() as session:
        stored = await session.get(Authorization, account["authorization_id"])
        assert stored.secret_encrypted == ""


@pytest.mark.parametrize("user,tenant", [(1, 0), (0, 1)])
async def test_foreign_grant_cannot_be_detached(connector_data, user, tenant):
    with actor(connector_data) as args:
        account, grant = await record(args)
    with actor(connector_data, user=user, tenant=tenant) as other:
        with pytest.raises(AuthorizationUnavailable):
            await manager.detach(**other, grant_id=grant["grant_id"])


@pytest.mark.parametrize("target", ["user", "tenant", "membership", "null_role", "viewer"])
async def test_fresh_target_authorization_and_context_required(connector_data, target):
    with actor(connector_data) as first:
        account, _ = await record(first)
    with actor(connector_data, tenant=1) as args:
        async with async_session_maker() as session, session.begin():
            if target == "user":
                await session.execute(update(User).where(User.id == args["actor_id"]).values(is_active=False))
            elif target == "tenant":
                await session.execute(update(Tenant).where(Tenant.id == args["tenant_id"]).values(is_active=False))
            else:
                values = (
                    {"is_active": False}
                    if target == "membership"
                    else {"role_id": connector_data.roles["viewer"] if target == "viewer" else None}
                )
                await session.execute(
                    update(TenantUser)
                    .where(TenantUser.user_id == args["actor_id"], TenantUser.tenant_id == args["tenant_id"])
                    .values(**values)
                )
        with pytest.raises(ConnectionDenied):
            await attach(args, account)
        with pytest.raises(ConnectionDenied):
            await record(args, subject=90002)
        if target != "viewer":
            with pytest.raises(ConnectionDenied):
                await manager.list_reusable(**args)


async def test_context_and_actor_spoofing_are_denied(connector_data):
    with actor(connector_data) as args:
        account, _ = await record(args)
        for other in (
            {**args, "actor_id": connector_data.users[1].id},
            {**args, "tenant_id": connector_data.tenants[1].id},
        ):
            with pytest.raises(ConnectionDenied):
                await attach(other, account)


@pytest.mark.parametrize("corruption", ["key", "ciphertext", "swapped", "payload", "state", "method", "revision"])
async def test_unusable_or_stale_authorizations_fail_closed(connector_data, monkeypatch, corruption):
    with actor(connector_data) as args:
        account, _ = await record(args)
        another, _ = await record(args, subject=90002)
    async with async_session_maker() as session, session.begin():
        row = await session.get(Authorization, account["authorization_id"])
        if corruption == "key":
            monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
        elif corruption == "ciphertext":
            row.secret_encrypted = "not-ciphertext"
        elif corruption == "swapped":
            row.secret_encrypted = (await session.get(Authorization, another["authorization_id"])).secret_encrypted
        elif corruption == "payload":
            row.secret_encrypted = encrypt_secret(
                json.dumps({"identity": module._identity(row), "material": {"api_id": 1}})
            )
        elif corruption == "state":
            row.state = "reauth"
        elif corruption == "method":
            row.auth_method = "unsupported"
        else:
            row.revision = 2
    with actor(connector_data, tenant=1) as args:
        with pytest.raises(AuthorizationUnavailable):
            await attach(args, account)
        assert await manager.list_grants(**args) == []


async def test_encryption_failure_is_atomic_and_duplicate_identity_never_overwrites(connector_data, monkeypatch):
    with actor(connector_data) as args:
        account, _ = await record(args)
        with pytest.raises(AuthorizationAlreadyExists):
            await record(args)

        def broken(_):
            raise RuntimeError("synthetic unavailable key")

        monkeypatch.setattr(module, "encrypt_secret", broken)
        with pytest.raises(RuntimeError):
            await record(args, subject=90002)
        assert len((await manager.list_reusable(**args))[0]) == 1
        assert len(await manager.list_grants(**args)) == 1


async def test_composite_fk_rejects_foreign_owner_even_outside_service(connector_data):
    with actor(connector_data) as args:
        account, _ = await record(args)
    with pytest.raises(IntegrityError):
        async with async_session_maker() as session, session.begin():
            session.add(
                Grant(
                    tenant_id=connector_data.tenants[1].id,
                    owner_user_id=connector_data.users[1].id,
                    authorization_id=account["authorization_id"],
                    authorization_revision=1,
                )
            )
            await session.flush()


async def test_concurrent_attach_returns_one_grant_and_revoke_wins(connector_data):
    with actor(connector_data) as args:
        account, _ = await record(args)

    # Independent threads/loops emulate separate workers and inherit no actor context.
    def run_attach():
        async def operation():
            with actor(connector_data, tenant=1) as args:
                return await attach(args, account)

        return asyncio.run(operation())

    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(run_attach) for _ in range(2)]
        linked = [await asyncio.wrap_future(future) for future in pending]
    assert linked[0]["grant_id"] == linked[1]["grant_id"]

    def run_revoke():
        async def operation():
            with actor(connector_data) as args:
                return await manager.revoke(**args, authorization_id=account["authorization_id"], expected_revision=1)

        return asyncio.run(operation())

    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(run_attach), executor.submit(run_revoke)]
        outcomes = await asyncio.gather(*(asyncio.wrap_future(future) for future in pending), return_exceptions=True)
    assert isinstance(outcomes[0], (dict, AuthorizationUnavailable))
    assert outcomes[1]["state"] == "revoked"
    with actor(connector_data, tenant=1) as args:
        assert (await manager.list_grants(**args))[0]["is_active"] is False
        with pytest.raises(AuthorizationUnavailable):
            await attach(args, account)


async def test_losing_origin_membership_does_not_implicitly_share_or_revoke_other_grants(connector_data):
    with actor(connector_data) as origin:
        account, _ = await record(origin)
    with actor(connector_data, tenant=1) as target:
        linked = await attach(target, account)
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(TenantUser)
            .where(TenantUser.tenant_id == origin["tenant_id"], TenantUser.user_id == origin["actor_id"])
            .values(is_active=False)
        )
    with actor(connector_data) as origin:
        with pytest.raises(ConnectionDenied):
            await manager.list_grants(**origin)
    with actor(connector_data, tenant=1) as target:
        assert (await manager.list_reusable(**target))[0] == [account]
        assert await attach(target, account) == linked


async def test_origin_workspace_deletion_preserves_personal_account_and_other_grant(connector_data):
    from sqlalchemy import delete

    with actor(connector_data) as origin:
        account, _ = await record(origin)
    with actor(connector_data, tenant=1) as target:
        linked = await attach(target, account)
    async with async_session_maker() as session, session.begin():
        await session.execute(delete(Tenant).where(Tenant.id == origin["tenant_id"]))
    with actor(connector_data, tenant=1) as target:
        assert (await manager.list_reusable(**target))[0] == [account]
        assert await manager.list_grants(**target) == [linked]


async def test_viewer_can_withdraw_own_consent_but_cannot_attach(connector_data):
    with actor(connector_data) as args:
        account, grant = await record(args)
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser)
                .where(TenantUser.tenant_id == args["tenant_id"], TenantUser.user_id == args["actor_id"])
                .values(role_id=connector_data.roles["viewer"])
            )
        assert (await manager.list_reusable(**args))[1] is False
        with pytest.raises(ConnectionDenied):
            await attach(args, account)
        assert (await manager.detach(**args, grant_id=grant["grant_id"]))["is_active"] is False
        result = await manager.revoke(**args, authorization_id=account["authorization_id"], expected_revision=1)
        assert result["state"] == "revoked"


async def test_concurrent_initial_results_cannot_duplicate_or_replace_identity(connector_data):
    def run_record():
        async def operation():
            with actor(connector_data) as args:
                return await record(args)

        return asyncio.run(operation())

    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(run_record) for _ in range(2)]
        outcomes = await asyncio.gather(*(asyncio.wrap_future(future) for future in pending), return_exceptions=True)
    assert sum(isinstance(result, AuthorizationAlreadyExists) for result in outcomes) == 1
    assert sum(isinstance(result, tuple) for result in outcomes) == 1
    with actor(connector_data) as args:
        assert len((await manager.list_reusable(**args))[0]) == 1
        assert len(await manager.list_grants(**args)) == 1


async def test_failed_initial_grant_rolls_back_authorization_too(connector_data, monkeypatch):
    async def failed_attach(*args, **kwargs):
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(module, "_attach", failed_attach)
    with actor(connector_data) as args:
        with pytest.raises(RuntimeError):
            await record(args)
        assert (await manager.list_reusable(**args))[0] == []
        assert await manager.list_grants(**args) == []
