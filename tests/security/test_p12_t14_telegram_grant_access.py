"""Synthetic PostgreSQL integration handoff; NOT run by the remote DB-exempt lane.

Use root conftest, the exclusive synthetic-schema lock/inventory/cleanup procedure
and fake provider boundaries. This tests metadata snapshots, not runtime leasing.
"""

import secrets
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Platform, Source, Tenant, TenantUser, User
from app.models import WorkspaceAuthorizationGrant as Grant
from app.services.social import connector_authorizations as accounts
from app.services.social import telegram_grant_access as access
from app.services.social.connector_authorizations import AuthorizationUnavailable, VerifiedTelegramResult
from app.services.social.connector_authorizations import connector_authorizations as manager
from app.types import SourceType
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
        platform = (await session.execute(select(Platform).where(Platform.name == "telegram"))).scalar_one()
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
