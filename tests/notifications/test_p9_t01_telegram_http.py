"""Real web auth/CSRF/approval/execution flow; Telegram HTTP never leaves sandbox."""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.channels.telegram import TelegramChannel
from app.core.config import settings
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentAction, TenantChannel
from app.services.actions import telegram_message as capability
from app.types import BotActionStatus
from tests.shared.fixtures.personal_workspace import personal_world
from tests.shared.web_session import session_cookie

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture
async def http_world(personal_world, monkeypatch):
    w = personal_world
    with tenant_scope(bypass=True), operator_permission_scope():
        w.binding = await TenantChannel.objects.create(
            tenant_id=w.tenants[0].id,
            channel="telegram",
            chat_id=f"-100{10000000 + w.tenants[0].id}",
            kind="channel",
        )
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123456:synthetic-never-network")
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", "1")
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_BINDINGS", f"{w.tenants[0].id}:{w.binding.id}")
    w.send = AsyncMock(return_value={"outcome": "sent", "message_id": "91"})
    monkeypatch.setattr(TelegramChannel, "send_part", w.send)
    w.app = create_application()
    w.csrf = w.app.state.csrf_manager.generate_token()
    async with AsyncClient(
        transport=ASGITransport(app=w.app), base_url="https://testserver.invalid", follow_redirects=False
    ) as client:
        client.cookies.set("session", session_cookie(w.users[0], w.tenants[0].id), domain="testserver.invalid")
        w.client = client
        yield w


def form(w, **kwargs):
    return {
        "binding_id": w.binding.id,
        "text": "reviewed channel message",
        "idempotency_key": "http-message",
        "_csrf": w.csrf,
        **kwargs,
    }


async def create(w):
    response = await w.client.post("/app/actions/telegram-message", data=form(w))
    assert response.status_code == 303
    path = response.headers["location"]
    with tenant_scope(w.tenants[0].id):
        row = await AgentAction.objects.get(id=int(path.rsplit("/", 1)[1]))
    return path, row


async def approve(w, path, row):
    response = await w.client.post(f"{path}/approve", data={"_csrf": w.csrf, "payload_hash": row.payload_hash})
    assert response.status_code == 303


async def test_web_propose_review_separate_execute_csrf_and_replay(http_world):
    w = http_world
    listing = await w.client.get("/app/actions")
    assert listing.status_code == 200 and 'action="/app/actions/telegram-message"' in listing.text
    assert listing.headers["cache-control"] == "no-store"
    assert (await w.client.post("/app/actions/telegram-message", data=form(w, _csrf=""))).status_code == 403
    path, row = await create(w)
    assert w.send.await_count == 0 and row.status == BotActionStatus.PENDING
    replay, duplicate = await create(w)
    assert replay == path and duplicate.id == row.id
    assert (await w.client.post("/app/actions/telegram-message", data=form(w, text="changed"))).status_code == 409
    detail = await w.client.get(path)
    assert detail.status_code == 200 and "reviewed channel message" in detail.text
    assert "synthetic-never-network" not in detail.text
    assert (await w.client.get(f"{path}/execute-telegram")).status_code == 405
    assert (
        await w.client.post(f"{path}/execute-telegram", data={"_csrf": w.csrf, "payload_hash": row.payload_hash})
    ).status_code == 409
    await approve(w, path, row)
    assert w.send.await_count == 0
    assert (await w.client.post(f"{path}/execute-telegram", data={"payload_hash": row.payload_hash})).status_code == 403
    assert (await w.client.post(f"{path}/execute-telegram", data={"_csrf": w.csrf})).status_code == 422
    assert (
        await w.client.post(f"{path}/execute-telegram", data={"_csrf": w.csrf, "payload_hash": "stale"})
    ).status_code == 409
    data = {"_csrf": w.csrf, "payload_hash": row.payload_hash}
    assert (await w.client.post(f"{path}/execute-telegram", data=data)).status_code == 303
    assert (await w.client.post(f"{path}/execute-telegram", data=data)).status_code == 409
    assert w.send.await_count == 1
    detail = await w.client.get(path)
    assert '"message_id": "91"' in detail.text and 'execute-telegram"' not in detail.text


async def test_web_default_off_and_fresh_kill_switch(http_world, monkeypatch):
    w = http_world
    path, row = await create(w)
    await approve(w, path, row)
    monkeypatch.setenv("P9_TELEGRAM_MESSAGE_ENABLED", "0")
    listing = await w.client.get("/app/actions")
    assert 'action="/app/actions/telegram-message"' not in listing.text
    assert (
        await w.client.post("/app/actions/telegram-message", data=form(w, idempotency_key="off"))
    ).status_code == 403
    assert (
        await w.client.post(f"{path}/execute-telegram", data={"_csrf": w.csrf, "payload_hash": row.payload_hash})
    ).status_code == 403
    assert w.send.await_count == 0


async def test_web_foreign_workspace_and_nonowner_cannot_publish(http_world):
    w = http_world
    path, row = await create(w)
    await approve(w, path, row)
    # Foreign owner may read its own ledger (404 for an absent scoped row);
    # manager lacks botaction.view and is refused before any row lookup (403).
    for user, denied_status in ((w.users[2], 404), (w.users[0], 403)):
        w.client.cookies.clear()
        w.client.cookies.set("session", session_cookie(user, w.tenants[1].id), domain="testserver.invalid")
        assert (await w.client.get(path)).status_code == denied_status
        assert (
            await w.client.post(f"{path}/execute-telegram", data={"_csrf": w.csrf, "payload_hash": row.payload_hash})
        ).status_code in (403, 409)
        assert (await w.client.post("/app/actions/telegram-message", data=form(w))).status_code == 403
    assert w.send.await_count == 0


async def test_web_receipt_storage_failure_is_unconfirmed_and_retry_never_resends(http_world, monkeypatch):
    w = http_world
    path, row = await create(w)
    await approve(w, path, row)
    monkeypatch.setattr(capability, "_finish", AsyncMock(side_effect=RuntimeError("synthetic storage loss")))
    data = {"_csrf": w.csrf, "payload_hash": row.payload_hash}
    response = await w.client.post(f"{path}/execute-telegram", data=data)
    assert response.status_code == 503 and response.json()["code"] == "external_outcome_unconfirmed"
    assert (await w.client.post(f"{path}/execute-telegram", data=data)).status_code == 409
    with tenant_scope(w.tenants[0].id):
        assert (await AgentAction.objects.get(id=row.id)).status == BotActionStatus.CLAIMED
    assert w.send.await_count == 1
