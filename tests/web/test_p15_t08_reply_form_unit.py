"""Web reply form: CSRF, UPDATE permission, and no approval path (P15-T08)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.web import reply as web


def _app():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(web.router)
    return app


def _request(can_update=True, active=True):
    perms = SimpleNamespace(can=lambda resource, action: can_update and action == "update")
    return SimpleNamespace(
        state=SimpleNamespace(web_user=SimpleNamespace(is_active=active), web_perms=perms),
    )


FORM = {"thread_id": "3", "inbound_message_id": "4", "body": "Thanks", "_csrf": "token"}


@pytest.fixture
def created(monkeypatch):
    create = AsyncMock(return_value={"id": 7, "state": "validated"})
    monkeypatch.setattr(web, "create_reply_draft", create)
    return create


@pytest.mark.asyncio
async def test_missing_csrf_is_refused_before_any_write(monkeypatch, created):
    monkeypatch.setattr(web, "ensure_csrf", lambda request, token: False)
    response = await web.reply_draft(**{k: v for k, v in FORM.items() if k != "_csrf"}, request=_request(), token="")
    assert response.status_code == 403
    created.assert_not_awaited()


@pytest.mark.asyncio
async def test_viewer_cannot_create_a_reply(monkeypatch, created):
    monkeypatch.setattr(web, "ensure_csrf", lambda request, token: True)
    response = await web.reply_draft(
        thread_id=3,
        inbound_message_id=4,
        body="Thanks",
        subject="",
        timezone="UTC",
        idempotency_key="k",
        token="token",
        request=_request(can_update=False),
    )
    assert response.status_code == 403
    created.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_source_is_a_safe_404(monkeypatch, created):
    monkeypatch.setattr(web, "ensure_csrf", lambda request, token: True)
    created.side_effect = web.ReplyUnavailable("inbound 4 missing")
    response = await web.reply_draft(
        thread_id=3,
        inbound_message_id=4,
        body="Thanks",
        subject="",
        timezone="UTC",
        idempotency_key="k",
        token="token",
        request=_request(),
    )
    assert response.status_code == 404
    assert "inbound 4 missing" not in response.body.decode()


def test_form_has_no_approval_or_send_route():
    paths = {(route.path, tuple(sorted(route.methods))) for route in web.router.routes}
    assert ("/app/communications/reply", ("POST",)) in paths
    assert not [path for path, _ in paths if path.endswith(("/schedule", "/approve", "/send"))]
