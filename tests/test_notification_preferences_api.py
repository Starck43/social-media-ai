"""Unit coverage for personal notification preference API validation and scope."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.models import NotificationPreference
from app.web.notifications import (
    PREFERENCE_DEFAULTS,
    notification_preferences_get,
    notification_preferences_reset,
    notification_preferences_save,
)


class FakePreferences:
    def __init__(self):
        self.saved = []

    async def get_for_user(self, user_id):
        return None

    async def save_for_user(self, user_id, value):
        self.saved.append((user_id, value))
        return value


class FakeCSRF:
    @staticmethod
    def verify_token(value):
        return value == "valid"


def request(method="GET", *, body=None, user_id=41, header_user_id=41):
    raw_body = json.dumps(body or {}).encode()
    consumed = False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    headers = [] if header_user_id is None else [(b"x-app-user", str(header_user_id).encode())]
    app = SimpleNamespace(state=SimpleNamespace(csrf_manager=FakeCSRF()))
    scope = {
        "type": "http",
        "method": method,
        "path": "/app/notifications/preferences",
        "headers": headers,
        "query_string": b"",
        "state": {"web_user": SimpleNamespace(id=user_id, is_active=True)},
        "app": app,
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_get_returns_defaults_without_creating_a_record(monkeypatch):
    manager = FakePreferences()
    monkeypatch.setattr(NotificationPreference, "objects", manager)

    response = await notification_preferences_get(request())

    assert response.status_code == 200
    assert json.loads(response.body) == {"user_id": 41, "preferences": PREFERENCE_DEFAULTS}
    assert response.headers["cache-control"] == "no-store"
    assert manager.saved == []


@pytest.mark.asyncio
async def test_save_uses_authenticated_user_and_rejects_invalid_values(monkeypatch):
    manager = FakePreferences()
    monkeypatch.setattr(NotificationPreference, "objects", manager)
    value = {**PREFERENCE_DEFAULTS, "tasks": False}

    response = await notification_preferences_save(request("POST", body={"_csrf": "valid", "preferences": value}))

    assert response.status_code == 200
    assert manager.saved == [(41, value)]
    for invalid in (
        {**value, "tasks": 1},
        {**value, "quiet": True, "start": "08:00", "end": "08:00"},
        {**value, "version": 2},
        {**value, "extra": True},
    ):
        response = await notification_preferences_save(request("POST", body={"_csrf": "valid", "preferences": invalid}))
        assert response.status_code == 422
    assert len(manager.saved) == 1


@pytest.mark.asyncio
async def test_save_and_reset_fail_closed_for_csrf_or_identity(monkeypatch):
    manager = FakePreferences()
    monkeypatch.setattr(NotificationPreference, "objects", manager)
    value = PREFERENCE_DEFAULTS

    bad_csrf = await notification_preferences_save(request("POST", body={"_csrf": "bad", "preferences": value}))
    wrong_identity = await notification_preferences_save(
        request("POST", body={"_csrf": "valid", "preferences": value}, header_user_id=99)
    )
    reset = await notification_preferences_reset(request("POST", body={"_csrf": "valid"}))

    assert bad_csrf.status_code == 403
    assert wrong_identity.status_code == 401
    assert reset.status_code == 200
    assert manager.saved == [(41, PREFERENCE_DEFAULTS)]
