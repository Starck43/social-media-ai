"""Synthetic PostgreSQL/assembled-app handoff; unrun in remote no-DB lane."""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Tenant, TenantUser
from app.models import WorkspaceAuthorizationGrant as Grant
from app.services.social import connector_authorizations as accounts
from app.services.social.connector_authorizations import VerifiedTelegramResult
from app.services.social.connector_authorizations import connector_authorizations as manager
from app.utils.token import create_access_token
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy
ROOT = "/api/v1/connector-accounts"


async def seed(data, user=0):
    with actor(data, user=user) as args:
        account, grant = await accounts.record_telegram_result(
            **args,
            result=VerifiedTelegramResult(
                subject_id=90021, api_id=123, api_hash="SYNTHETIC-HASH", session="SYNTHETIC-SESSION"
            ),
        )
    return account, grant


def headers(data, user=0):
    return {"Authorization": "Bearer " + create_access_token(subject=str(data.users[user].id))[0]}


async def test_no_memberships_and_suspended_workspaces_do_not_prevent_own_revoke(connector_data, monkeypatch):
    data = connector_data
    account, _ = await seed(data)
    foreign, _ = await seed(data, user=1)
    with actor(data, tenant=1) as args:
        await manager.attach(**args, authorization_id=account["authorization_id"], expected_revision=1)
    async with async_session_maker() as session, session.begin():
        await session.execute(update(TenantUser).where(TenantUser.user_id == data.users[0].id).values(is_active=False))
        await session.execute(update(Tenant).where(Tenant.id.in_([t.id for t in data.tenants])).values(is_active=False))
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", "")

    def no_decryption(*args, **kwargs):
        raise AssertionError("personal list/revoke must not decrypt")

    monkeypatch.setattr(accounts, "decrypt_secret", no_decryption)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="https://testserver") as client:
        own = headers(data)
        listing = await client.get(ROOT + "/personal", headers=own)
        assert listing.status_code == 200 and listing.json() == [account]
        assert "SYNTHETIC" not in listing.text
        assert (await client.get(ROOT, headers=own)).status_code == 403
        assert (
            await client.post(
                ROOT + f"/{account['authorization_id']}/attach", json={"expected_revision": 1}, headers=own
            )
        ).status_code == 403
        revoked = await client.post(
            ROOT + f"/{account['authorization_id']}/revoke", json={"expected_revision": 1}, headers=own
        )
        assert revoked.status_code == 200 and revoked.json()["revision"] == 2
        assert revoked.json()["state"] == "revoked"
        stale = await client.post(
            ROOT + f"/{account['authorization_id']}/revoke", json={"expected_revision": 1}, headers=own
        )
        assert stale.status_code == 404
        repeated = await client.post(
            ROOT + f"/{account['authorization_id']}/revoke", json={"expected_revision": 2}, headers=own
        )
        assert repeated.status_code == 200 and repeated.json()["revision"] == 2
        denied = await client.post(
            ROOT + f"/{foreign['authorization_id']}/revoke", json={"expected_revision": 1}, headers=own
        )
        assert denied.status_code == 404 and denied.json() == {"detail": "connection_unavailable"}
    async with async_session_maker() as session:
        row = await session.get(Authorization, account["authorization_id"])
        other = await session.get(Authorization, foreign["authorization_id"])
        grants = (await session.execute(select(Grant).where(Grant.owner_user_id == data.users[0].id))).scalars().all()
        assert row.secret_encrypted == "" and row.state == "revoked" and row.revision == 2
        assert len(grants) == 2 and not any(g.is_active for g in grants)
        assert other.state == "active" and other.secret_encrypted and other.revision == 1


async def test_repeated_current_revision_revoke_repairs_inconsistent_local_rows(connector_data):
    account, grant = await seed(connector_data)
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(Authorization).where(Authorization.id == account["authorization_id"]).values(state="revoked")
        )
    with tenant_scope(), permission_scope(connector_data.users[0]):
        result = await manager.revoke_personal(
            actor_id=connector_data.users[0].id, authorization_id=account["authorization_id"], expected_revision=1
        )
    assert result["state"] == "revoked" and result["revision"] == 1
    async with async_session_maker() as session:
        assert (await session.get(Authorization, account["authorization_id"])).secret_encrypted == ""
        assert (await session.get(Grant, grant["grant_id"])).is_active is False


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_grant_update_failure_after_account_flush_rolls_back_both_stores(connector_data, monkeypatch, error):
    account, grant = await seed(connector_data)
    real_execute = AsyncSession.execute

    async def fail_after_flush(session, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and getattr(statement.table, "name", None) == Grant.__tablename__:
            await session.flush()  # actually write account changes before injected failure
            raise error("synthetic-grant-update-failure")
        return await real_execute(session, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", fail_after_flush)
        with tenant_scope(), permission_scope(connector_data.users[0]), pytest.raises(error):
            await manager.revoke_personal(
                actor_id=connector_data.users[0].id, authorization_id=account["authorization_id"], expected_revision=1
            )
    async with async_session_maker() as session:
        row = await session.get(Authorization, account["authorization_id"])
        assert row.state == "active" and row.revision == 1 and row.secret_encrypted
        assert (await session.get(Grant, grant["grant_id"])).is_active is True
