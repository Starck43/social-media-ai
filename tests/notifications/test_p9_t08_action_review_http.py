"""CSRF, review-version binding and workspace rights on proposal decisions."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentAction
from app.types import BotActionStatus
from tests.shared.fixtures.personal_workspace import personal_world
from tests.shared.web_session import session_cookie

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def test_review_requires_csrf_reviewed_hash_and_current_workspace(personal_world):
    w = personal_world
    app = create_application()
    with tenant_scope(w.tenants[0].id):
        row, _ = await AgentAction.objects.propose(
            idempotency_key="http", capability="business.reply", target_ref="document:1", payload={"text": "reviewed"}
        )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver.invalid", follow_redirects=False
    ) as client:
        client.cookies.set("session", session_cookie(w.users[0], w.tenants[0].id), domain="testserver.invalid")
        detail = await client.get(f"/app/actions/{row.id}")
        assert detail.status_code == 200 and "reviewed" in detail.text
        csrf = app.state.csrf_manager.generate_token()
        assert (
            await client.post(f"/app/actions/{row.id}/approve", data={"payload_hash": row.payload_hash})
        ).status_code == 403
        assert (
            await client.post(f"/app/actions/{row.id}/approve", data={"_csrf": csrf, "payload_hash": "stale"})
        ).status_code == 409
        approved = await client.post(
            f"/app/actions/{row.id}/approve", data={"_csrf": csrf, "payload_hash": row.payload_hash}
        )
        assert approved.status_code == 303
        with tenant_scope(w.tenants[0].id):
            current = await AgentAction.objects.get(id=row.id)
            assert current.status == BotActionStatus.APPROVED and current.attempts == 0
            assert current.history[-1]["actor_user_id"] == w.users[0].id
        assert (await client.post(f"/app/actions/{row.id}/cancel", data={"_csrf": csrf})).status_code == 303
        client.cookies.clear()
        client.cookies.set("session", session_cookie(w.users[2], w.tenants[1].id), domain="testserver.invalid")
        assert (await client.get(f"/app/actions/{row.id}")).status_code == 404
