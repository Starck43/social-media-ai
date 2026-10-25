"""Credential status uses only personal vault metadata and avoids false reauth signals."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.social import connections


def stub_vault_row(monkeypatch, row):
    calls = []

    async def lookup(user_id, platform, kind):
        calls.append((user_id, platform, kind))
        return row

    monkeypatch.setattr(connections, "_vault_row", lookup)
    return calls


@pytest.mark.asyncio
async def test_near_expiry_is_visible_without_claiming_auto_renewal(monkeypatch):
    user_id = 101
    row = SimpleNamespace(
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        meta={},
    )
    calls = stub_vault_row(monkeypatch, row)

    status = await connections.connection_status("vk", user_id)

    assert calls == [(user_id, "vk", "user_token")]
    assert status.state == "connected"
    assert status.needs_action is False
    assert "истекает через" in status.detail
    assert "обновляется через" not in status.detail
    assert "продление без повторного входа" not in status.detail


@pytest.mark.asyncio
async def test_missing_credential_is_actionable_for_its_person(monkeypatch):
    user_id = 202
    monkeypatch.setattr(connections.settings, "VK_APP_ID", "test-app")
    calls = stub_vault_row(monkeypatch, None)

    status = await connections.connection_status("vk", user_id)

    assert calls == [(user_id, "vk", "user_token")]
    assert status.state == "missing"
    assert status.needs_action is True
    assert status.can_connect is True


@pytest.mark.asyncio
async def test_expired_credential_with_refresh_token_is_renewable_not_reauth(monkeypatch):
    row = SimpleNamespace(
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        meta={"refresh_token": "present"},
    )
    stub_vault_row(monkeypatch, row)

    status = await connections.connection_status("vk", 303)

    assert status.state == "renewable"
    assert status.needs_action is False
    assert status.tone == "ok"
    assert "продление без повторного входа" in status.detail


@pytest.mark.asyncio
async def test_expired_credential_without_refresh_token_requests_reauthentication(monkeypatch):
    row = SimpleNamespace(
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        meta={},
    )
    stub_vault_row(monkeypatch, row)

    status = await connections.connection_status("vk", 404)

    assert status.state == "reauth"
    assert status.needs_action is True
    assert status.tone == "bad"
