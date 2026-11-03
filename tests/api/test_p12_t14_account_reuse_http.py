"""Assembled authenticated ASGI routes for the persistence/reuse foundation only."""

import builtins

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.database import async_session_maker
from app.main import create_application
from app.models import TenantUser
from app.services.social.connector_authorizations import VerifiedTelegramResult, record_telegram_result
from app.utils.token import create_access_token
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy
ROOT = "/api/v1/connector-accounts"


def headers(data, user=0, tenant=0):
    return {
        "Authorization": "Bearer " + create_access_token(subject=str(data.users[user].id))[0],
        "X-Tenant-Id": str(data.tenants[tenant].id),
    }


async def seed(data):
    with actor(data) as args:
        return await record_telegram_result(
            **args,
            result=VerifiedTelegramResult(
                subject_id=10001, api_id=123, api_hash="FAKE-API-HASH", session="FAKE-SESSION"
            ),
        )


async def test_authenticated_explicit_reuse_detach_and_global_revoke(connector_data):
    account, original = await seed(connector_data)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        target = headers(connector_data, tenant=1)
        assert (await client.get(ROOT)).status_code == 401
        listing = await client.get(ROOT, headers=target)
        assert listing.status_code == 200
        assert listing.json() == {"accounts": [account], "can_attach": True}
        assert "FAKE-" not in listing.text
        assert (await client.get(ROOT + "/grants", headers=target)).json() == []
        attached = await client.post(
            ROOT + f"/{account['authorization_id']}/attach", json={"expected_revision": 1}, headers=target
        )
        assert attached.status_code == 200
        linked = attached.json()
        assert linked["authorization_id"] == original["authorization_id"]
        detached = await client.post(ROOT + f"/grants/{linked['grant_id']}/detach", headers=target)
        assert detached.status_code == 200 and detached.json()["is_active"] is False
        local = await client.get(ROOT + "/grants", headers=headers(connector_data))
        assert local.json()[0]["is_active"] is True
        revoked = await client.post(
            ROOT + f"/{account['authorization_id']}/revoke", json={"expected_revision": 1}, headers=target
        )
        assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
        local = await client.get(ROOT + "/grants", headers=headers(connector_data))
        assert local.json()[0]["is_active"] is False
        stale = await client.post(
            ROOT + f"/{account['authorization_id']}/attach", json={"expected_revision": 1}, headers=target
        )
        assert stale.status_code == 404 and stale.json() == {"detail": "connection_unavailable"}


async def test_cross_user_and_cross_tenant_ids_cannot_manage_connections(connector_data):
    account, original = await seed(connector_data)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        foreign = headers(connector_data, user=1)
        assert (await client.get(ROOT, headers=foreign)).json()["accounts"] == []
        for action in ("attach", "revoke"):
            response = await client.post(
                ROOT + f"/{account['authorization_id']}/{action}", json={"expected_revision": 1}, headers=foreign
            )
            assert response.status_code == 404 and "FAKE-" not in response.text
        for selected in (foreign, headers(connector_data, tenant=1)):
            response = await client.post(ROOT + f"/grants/{original['grant_id']}/detach", headers=selected)
            assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_revision": True},
        {"expected_revision": "1"},
        {"expected_revision": 0},
        {"expected_revision": 1, "owner_user_id": 42},
        {"expected_revision": 1, "tenant_id": 42},
        {"expected_revision": 1, "session": "must-not-be-accepted"},
    ],
)
async def test_revision_and_scope_cannot_be_supplied_as_secret_import(connector_data, payload):
    account, _ = await seed(connector_data)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        response = await client.post(
            ROOT + f"/{account['authorization_id']}/attach", json=payload, headers=headers(connector_data, tenant=1)
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_account_selection"}
    assert "must-not-be-accepted" not in response.text


async def test_revoked_membership_denied_before_reuse(connector_data):
    account, _ = await seed(connector_data)
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(TenantUser)
            .where(
                TenantUser.tenant_id == connector_data.tenants[1].id, TenantUser.user_id == connector_data.users[0].id
            )
            .values(is_active=False)
        )
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        response = await client.post(
            ROOT + f"/{account['authorization_id']}/attach",
            json={"expected_revision": 1},
            headers=headers(connector_data, tenant=1),
        )
    assert response.status_code == 403


async def test_legacy_http_login_never_reads_terminal_or_calls_provider(connector_data, monkeypatch):
    from app.services.social import tg_session

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP must not invoke terminal or provider login")

    monkeypatch.setattr(builtins, "input", forbidden)
    monkeypatch.setattr(tg_session, "interactive_login", forbidden)
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/credentials/login", json={"platform": "telegram"}, headers=headers(connector_data)
        )
    assert response.status_code == 409
    assert response.json() == {"detail": "workspace_telegram_authorization_not_available"}
