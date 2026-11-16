"""Assembled cookie/CSRF routes and real synthetic PostgreSQL transactions.

No authentication with Telegram: the trusted adapter result and browser session
are synthetic. Use the normal root conftest and an exclusively owned test schema.
"""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import TenantUser, UserCredential
from app.models import WorkspaceAuthorizationGrant as Grant
from app.services.social import connector_authorizations as service
from app.services.social.connector_authorizations import VerifiedTelegramResult, record_telegram_result
from app.utils.token import create_access_token
from tests.shared.connector_accounts import connector_actor as actor
from tests.shared.connector_accounts import connector_data  # noqa: F401

pytestmark = pytest.mark.tenancy
ROOT = "/app/settings/telegram-accounts/workspace"
FAILURE = "SYNTHETIC-INTERNAL-FAILURE-MUST-NOT-LEAK"


@pytest.fixture(autouse=True)
def no_provider_or_legacy_session(monkeypatch):
    from app.services.social import telegram_login, telegram_login_provider, tg_session

    def forbidden(*args, **kwargs):
        raise AssertionError("Provider I/O and legacy vault/session use are forbidden")

    monkeypatch.setattr(telegram_login, "telegram_login_step", forbidden)
    monkeypatch.setattr(telegram_login_provider, "telegram_login_step", forbidden)
    for name in (
        "load_session",
        "require_session",
        "build_client",
        "check_session",
        "save_session",
        "interactive_login",
    ):
        monkeypatch.setattr(tg_session, name, forbidden)


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = attrs.get("action")
            self.forms[self.current] = {}
        elif tag == "input" and self.current and attrs.get("name"):
            self.forms[self.current][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


async def seed(data, *, user=0):
    with actor(data, user=user) as args:
        return await record_telegram_result(
            **args,
            result=VerifiedTelegramResult(
                subject_id=90001, api_id=123, api_hash="SYNTHETIC-API-HASH", session="SYNTHETIC-NOT-A-TELETHON-SESSION"
            ),
        )


@asynccontextmanager
async def browser(data, *, user=0, tenant=0):
    # Sign only this fixture's fake session, never read a deployment/browser cookie.
    session = {
        "token": create_access_token(subject=str(data.users[user].id))[0],
        "app_tenant_id": data.tenants[tenant].id,
    }
    cookie = TimestampSigner(settings.SECRET_KEY).sign(base64.b64encode(json.dumps(session).encode())).decode()
    async with AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="https://testserver", follow_redirects=False
    ) as client:
        client.cookies.set("session", cookie, domain="testserver.local", path="/")
        yield client


async def forms(client):
    response = await client.get(ROOT)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "SYNTHETIC-API-HASH" not in response.text and "SYNTHETIC-NOT-A-TELETHON-SESSION" not in response.text
    return Forms(response.text).forms


def attach_path(account):
    return f"{ROOT}/accounts/{account['authorization_id']}/attach"


def detach_path(grant):
    return f"{ROOT}/grants/{grant['grant_id']}/detach"


async def grants_for(data, *, tenant=1):
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(Grant)
                    .where(Grant.owner_user_id == data.users[0].id, Grant.tenant_id == data.tenants[tenant].id)
                    .order_by(Grant.id)
                )
            )
            .scalars()
            .all()
        )
        return [(row.id, row.authorization_id, row.authorization_revision, row.is_active) for row in rows]


async def account_state(account):
    async with async_session_maker() as session:
        row = await session.get(Authorization, account["authorization_id"])
        return row.state, row.revision, row.secret_encrypted


async def test_real_cookie_attach_idempotency_and_workspace_only_detach(connector_data):
    account, origin = await seed(connector_data)
    original = await account_state(account)
    async with browser(connector_data, tenant=1) as client:
        path = attach_path(account)
        selection = (await forms(client))[path]
        for _ in range(2):
            response = await client.post(path, data=selection)
            assert response.status_code == 303 and response.headers["location"] == ROOT
        linked = await grants_for(connector_data)
        assert len(linked) == 1 and linked[0][1:] == (account["authorization_id"], 1, True)
        path = f"{ROOT}/grants/{linked[0][0]}/detach"
        selection = (await forms(client))[path]
        for _ in range(2):
            assert (await client.post(path, data=selection)).status_code == 303
    assert await grants_for(connector_data) == [(linked[0][0], account["authorization_id"], 1, False)]
    assert await grants_for(connector_data, tenant=0) == [(origin["grant_id"], account["authorization_id"], 1, True)]
    assert await account_state(account) == original
    async with async_session_maker() as session:
        assert not (
            await session.execute(select(UserCredential.id).where(UserCredential.user_id == connector_data.users[0].id))
        ).all()


async def test_workspace_switch_invalidates_old_consent_without_writing(connector_data):
    account, _ = await seed(connector_data)
    async with browser(connector_data) as client:
        page = await forms(client)
        old_selection = page[attach_path(account)]
        switch = page["/app/workspaces/select"]
        switch["tenant_id"] = str(connector_data.tenants[1].id)
        assert (await client.post("/app/workspaces/select", data=switch)).status_code == 302
        rejected = await client.post(attach_path(account), data=old_selection)
        assert rejected.status_code == 403
        assert await grants_for(connector_data) == []
        fresh = (await forms(client))[attach_path(account)]
        assert (await client.post(attach_path(account), data=fresh)).status_code == 303
    assert len(await grants_for(connector_data)) == 1


@pytest.mark.parametrize("action", ["attach", "detach"])
async def test_foreign_selection_with_valid_own_csrf_is_opaque(connector_data, action):
    foreign, foreign_grant = await seed(connector_data)
    own, own_grant = await seed(connector_data, user=1)
    original = await account_state(foreign)
    async with browser(connector_data, user=1) as client:
        page = await forms(client)
        path = attach_path(foreign) if action == "attach" else detach_path(foreign_grant)
        selection = page[attach_path(own) if action == "attach" else detach_path(own_grant)]
        response = await client.post(path, data=selection)
        assert response.status_code == 404
        assert "Выбор недоступен" in response.text
    assert await account_state(foreign) == original
    assert await grants_for(connector_data, tenant=0) == [
        (foreign_grant["grant_id"], foreign["authorization_id"], 1, True)
    ]


async def test_stale_revision_after_personal_revoke_never_reactivates(connector_data):
    account, _ = await seed(connector_data)
    async with browser(connector_data, tenant=1) as client:
        selection = (await forms(client))[attach_path(account)]
        with tenant_scope(), permission_scope(connector_data.users[0]):
            await service.connector_authorizations.revoke_personal(
                actor_id=connector_data.users[0].id, authorization_id=account["authorization_id"], expected_revision=1
            )
        assert (await client.post(attach_path(account), data=selection)).status_code == 404
    assert await grants_for(connector_data) == []
    assert await account_state(account) == ("revoked", 2, "")
    assert all(not row[3] for row in await grants_for(connector_data, tenant=0))


@pytest.mark.parametrize("change", ["membership", "role"])
async def test_membership_or_role_change_after_render_cannot_authorize_attach(connector_data, change):
    account, _ = await seed(connector_data)
    async with browser(connector_data, tenant=1) as client:
        selection = (await forms(client))[attach_path(account)]
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser)
                .where(
                    TenantUser.user_id == connector_data.users[0].id,
                    TenantUser.tenant_id == connector_data.tenants[1].id,
                )
                .values(
                    **({"is_active": False} if change == "membership" else {"role_id": connector_data.roles["viewer"]})
                )
            )
        response = await client.post(attach_path(account), data=selection)
        # Lost membership changes the active workspace; a viewer stays selected
        # but fails the fresh CREATE check in the real authorization service.
        assert response.status_code == (403 if change == "membership" else 404)
    assert await grants_for(connector_data) == []


async def test_viewer_can_detach_existing_own_grant_but_not_attach(connector_data):
    account, _ = await seed(connector_data)
    with actor(connector_data, tenant=1) as args:
        linked = await service.connector_authorizations.attach(
            **args, authorization_id=account["authorization_id"], expected_revision=1
        )
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(TenantUser)
            .where(
                TenantUser.user_id == connector_data.users[0].id, TenantUser.tenant_id == connector_data.tenants[1].id
            )
            .values(role_id=connector_data.roles["viewer"])
        )
    async with browser(connector_data, tenant=1) as client:
        page = await forms(client)
        assert attach_path(account) not in page
        assert (await client.post(detach_path(linked), data=page[detach_path(linked)])).status_code == 303
    assert (await grants_for(connector_data))[0][3] is False
    assert (await account_state(account))[0:2] == ("active", 1)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_attach_error_or_cancellation_after_flush_rolls_back(
    connector_data, monkeypatch, caplog, existing, failure
):
    account, _ = await seed(connector_data)
    if existing:
        with actor(connector_data, tenant=1) as args:
            linked = await service.connector_authorizations.attach(
                **args, authorization_id=account["authorization_id"], expected_revision=1
            )
            await service.connector_authorizations.detach(**args, grant_id=linked["grant_id"])
    baseline = await grants_for(connector_data)
    original = await account_state(account)
    real_attach = service._attach

    async def fail_after_flush(*args, **kwargs):
        await real_attach(*args, **kwargs)
        raise asyncio.CancelledError(FAILURE) if failure == "cancel" else RuntimeError(FAILURE)

    async with browser(connector_data, tenant=1) as client:
        selection = (await forms(client))[attach_path(account)]
        with monkeypatch.context() as patch:
            patch.setattr(service, "_attach", fail_after_flush)
            if failure == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await client.post(attach_path(account), data=selection)
            else:
                response = await client.post(attach_path(account), data=selection)
                assert response.status_code == 503 and "Результат не подтверждён" in response.text
                assert FAILURE not in response.text
        assert await grants_for(connector_data) == baseline
        assert await account_state(account) == original
        assert (await client.post(attach_path(account), data=selection)).status_code == 303
    assert FAILURE not in caplog.text
    assert len(await grants_for(connector_data)) == 1


@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_detach_error_or_cancellation_after_flush_rolls_back(connector_data, caplog, failure):
    account, linked = await seed(connector_data)
    baseline = await grants_for(connector_data, tenant=0)
    original = await account_state(account)

    def fail_after_flush(session, context):
        if any(isinstance(row, Grant) and row.id == linked["grant_id"] and not row.is_active for row in session.dirty):
            raise asyncio.CancelledError(FAILURE) if failure == "cancel" else RuntimeError(FAILURE)

    async with browser(connector_data) as client:
        selection = (await forms(client))[detach_path(linked)]
        event.listen(Session, "after_flush", fail_after_flush)
        try:
            if failure == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await client.post(detach_path(linked), data=selection)
            else:
                response = await client.post(detach_path(linked), data=selection)
                assert response.status_code == 503 and FAILURE not in response.text
        finally:
            event.remove(Session, "after_flush", fail_after_flush)
        assert await grants_for(connector_data, tenant=0) == baseline
        assert await account_state(account) == original
        assert (await client.post(detach_path(linked), data=selection)).status_code == 303
    assert FAILURE not in caplog.text


async def test_local_revoke_waits_for_web_attach_then_invalidates_all_grants(connector_data, monkeypatch):
    account, _ = await seed(connector_data)
    flushed, revoking, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    backend = {}
    real_attach, real_account = service._attach, service._account

    async def paused_attach(session, **kwargs):
        result = await real_attach(session, **kwargs)
        backend["attach"] = (await session.execute(select(func.pg_backend_pid()))).scalar_one()
        flushed.set()
        await release.wait()
        return result

    async def observed_account(session, *args, **kwargs):
        if asyncio.current_task().get_name() == "p12t14-personal-revoke":
            backend["revoke"] = (await session.execute(select(func.pg_backend_pid()))).scalar_one()
            revoking.set()
        return await real_account(session, *args, **kwargs)

    async def revoke():
        with tenant_scope(), permission_scope(connector_data.users[0]):
            return await service.connector_authorizations.revoke_personal(
                actor_id=connector_data.users[0].id, authorization_id=account["authorization_id"], expected_revision=1
            )

    async def assert_actual_lock_wait():
        while True:
            async with async_session_maker() as session:
                blockers = (await session.execute(select(func.pg_blocking_pids(backend["revoke"])))).scalar_one()
            if backend["attach"] in blockers:
                return
            await asyncio.sleep(0.01)

    async with browser(connector_data, tenant=1) as client:
        selection = (await forms(client))[attach_path(account)]
        monkeypatch.setattr(service, "_attach", paused_attach)
        monkeypatch.setattr(service, "_account", observed_account)
        pending = []
        try:
            pending.append(asyncio.create_task(client.post(attach_path(account), data=selection)))
            await asyncio.wait_for(flushed.wait(), timeout=10)
            pending.append(asyncio.create_task(revoke(), name="p12t14-personal-revoke"))
            await asyncio.wait_for(revoking.wait(), timeout=10)
            await asyncio.wait_for(assert_actual_lock_wait(), timeout=10)
            release.set()
            attached, revoked = await asyncio.wait_for(asyncio.gather(*pending), timeout=10)
            assert attached.status_code == 303 and revoked["state"] == "revoked"
        finally:
            release.set()
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        assert (await client.post(attach_path(account), data=selection)).status_code == 404
    assert await account_state(account) == ("revoked", 2, "")
    for tenant in (0, 1):
        rows = await grants_for(connector_data, tenant=tenant)
        assert len(rows) == 1 and rows[0][3] is False
