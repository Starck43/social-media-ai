"""Assembled JWT-only API journey with mocked Telegram, not a browser UX claim."""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_application
from app.utils.token import create_access_token
from tests.shared.connector_accounts import connector_data  # noqa: F401
from tests.shared.telegram_login import HASH, PHONE, SESSION, login_provider  # noqa: F401

pytestmark = pytest.mark.tenancy
ROOT = "/api/v1/connector-accounts/telegram/login"


def headers(data, user=0, tenant=0):
    return {
        "Authorization": "Bearer " + create_access_token(subject=str(data.users[user].id))[0],
        "X-Tenant-Id": str(data.tenants[tenant].id),
    }


async def test_authenticated_login_code_and_status_no_secret_projection(connector_data, login_provider, caplog):
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        args = {"headers": headers(connector_data)}
        assert (await client.post(ROOT, json={"request_id": str(uuid4()), "phone": PHONE})).status_code == 401
        # Ambient cookies cannot authorize this machine API (no cookie-based CSRF path).
        client.cookies.set("access_token", create_access_token(subject=str(connector_data.users[0].id))[0])
        assert (await client.post(ROOT, json={"request_id": str(uuid4()), "phone": PHONE})).status_code == 401
        client.cookies.clear()
        started = await client.post(ROOT, json={"request_id": str(uuid4()), "phone": PHONE}, **args)
        assert started.status_code == 200
        row = started.json()
        assert row["state"] == "code"
        path = ROOT + "/" + row["attempt_id"]
        for foreign in (headers(connector_data, user=1), headers(connector_data, tenant=1)):
            assert (await client.get(path, headers=foreign)).status_code == 404
            assert (
                await client.post(
                    path + "/code", headers=foreign, json={"expected_revision": row["revision"], "code": "12345"}
                )
            ).status_code == 404
        result = await client.post(path + "/code", json={"expected_revision": row["revision"], "code": "12345"}, **args)
        assert result.status_code == 200 and result.json()["state"] == "completed"
        status = await client.get(path, **args)
        assert status.status_code == 200 and status.json() == result.json()
        replay = await client.post(path + "/code", json={"expected_revision": row["revision"], "code": "12345"}, **args)
        assert replay.status_code == 409 and replay.json() == {"detail": "login_conflict"}
        for response in (started, result, status, replay):
            assert all(v not in response.text for v in (PHONE, SESSION, HASH, "12345"))
    assert login_provider == ["begin", "code"]
    assert all(v not in caplog.text for v in (PHONE, SESSION, HASH))


@pytest.mark.parametrize("extra", ["owner_user_id", "tenant_id", "session", "api_hash"])
async def test_start_rejects_spoofed_scope_or_secret_import_without_echo(connector_data, login_provider, extra):
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        response = await client.post(
            ROOT,
            headers=headers(connector_data),
            json={"request_id": str(uuid4()), "phone": PHONE, extra: "SYNTHETIC-SECRET"},
        )
    assert response.status_code == 422 and response.json() == {"detail": "invalid_account_selection"}
    assert login_provider == []


async def test_invalid_code_and_password_do_not_echo_body_or_invoke_provider(connector_data, login_provider):
    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        h = headers(connector_data)
        row = (await client.post(ROOT, headers=h, json={"request_id": str(uuid4()), "phone": PHONE})).json()
        path = ROOT + "/" + row["attempt_id"]
        for action, body in (
            ("code", {"expected_revision": row["revision"], "code": "SYNTHETIC-SECRET"}),
            ("password", {"expected_revision": True, "password": "SYNTHETIC-SECRET"}),
            ("password", {"expected_revision": row["revision"], "password": "x" * 257}),
        ):
            response = await client.post(path + "/" + action, headers=h, json=body)
            assert response.status_code == 422 and "SYNTHETIC-SECRET" not in response.text
        cancelled = await client.post(path + "/cancel", headers=h)
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
        assert (await client.post(path + "/cancel", headers=h)).json() == cancelled.json()
    assert login_provider == ["begin"]


async def test_real_password_route_uses_latest_revision_without_echo(connector_data, login_provider, monkeypatch):
    from app.services.social import telegram_login as service
    from app.services.social.connector_authorizations import VerifiedTelegramResult
    from app.services.social.telegram_login_provider import LoginProgress

    async with AsyncClient(transport=ASGITransport(app=create_application()), base_url="http://testserver") as client:
        h = headers(connector_data)
        row = (await client.post(ROOT, headers=h, json={"request_id": str(uuid4()), "phone": PHONE})).json()
        path = ROOT + "/" + row["attempt_id"]

        async def twofa(operation, material, secret=None):
            assert operation == "code"
            return LoginProgress("password", SESSION)

        monkeypatch.setattr(service, "telegram_login_step", twofa)
        challenge = await client.post(
            path + "/code", headers=h, json={"expected_revision": row["revision"], "code": "12345"}
        )
        assert challenge.status_code == 200 and challenge.json()["state"] == "password"

        async def verified(operation, material, secret=None):
            assert operation == "password" and secret == "  SYNTH-2FA-SECRET  "
            return LoginProgress("authorized", result=VerifiedTelegramResult(90001, 12345, HASH, SESSION))

        monkeypatch.setattr(service, "telegram_login_step", verified)
        result = await client.post(
            path + "/password",
            headers=h,
            json={"expected_revision": challenge.json()["revision"], "password": "  SYNTH-2FA-SECRET  "},
        )
        assert result.status_code == 200 and result.json()["state"] == "completed"
        assert "SYNTH-2FA-SECRET" not in result.text
