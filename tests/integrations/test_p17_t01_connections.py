"""Offline storage wiring: no database, DNS, or provider connection."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.integrations.onec import connections
from app.models.integrations.connection import WorkspaceConnectionSecret
from app.models.managers.workspace_connection_manager import ConnectionDenied


class FakeSession:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) == 1:
            row.id = 123

    async def flush(self):
        pass


def make_service():
    service = connections.OneCConnectionService()
    session = FakeSession()

    @asynccontextmanager
    async def transaction():
        yield session

    service._sessions._get_owned_session = transaction
    service._hosts.require_approved = AsyncMock(return_value="erp.example.com")
    return service, session


def run(coro):
    return asyncio.run(coro)


def arguments():
    return dict(
        tenant_id=11,
        actor_id=7,
        label=" Accounting ",
        base_url="https://ERP.example.com/base/",
        interface="odata_standard",
        login="synthetic-login",
        password="synthetic-password",
    )


def test_create_binds_vault_secret_and_never_projects_credentials(monkeypatch):
    monkeypatch.setattr(connections, "seal_basic_credentials", lambda login, password: "synthetic-ciphertext")
    service, session = make_service()
    result = run(service.create(**arguments()))
    service._hosts.require_approved.assert_awaited_once_with(
        session=session, tenant_id=11, actor_id=7, host="erp.example.com"
    )
    row, secret = session.rows
    assert (row.tenant_id, row.owner_user_id, row.provider_id, row.auth_method) == (11, 7, "onec", "odata_credentials")
    assert isinstance(secret, WorkspaceConnectionSecret)
    assert (secret.connection_id, secret.secret_encrypted) == (123, "synthetic-ciphertext")
    assert result["label"] == "Accounting"
    assert result["state"] == "unverified"
    assert "synthetic-login" not in str(result) + str(row.config) + row.account_key
    assert "synthetic-password" not in str(result) + str(row.config) + row.account_key


def test_host_approval_failure_persists_nothing(monkeypatch):
    seal = AsyncMock()
    monkeypatch.setattr(connections, "seal_basic_credentials", seal)
    service, session = make_service()
    service._hosts.require_approved.side_effect = ConnectionDenied()
    with pytest.raises(ConnectionDenied):
        run(service.create(**arguments()))
    assert not session.rows
    seal.assert_not_called()


def test_invalid_endpoint_cannot_reach_storage():
    service, session = make_service()
    params = arguments()
    params["base_url"] = "http://erp.example.com/"
    with pytest.raises(connections.OneCEndpointError):
        run(service.create(**params))
    assert not session.rows
    service._hosts.require_approved.assert_not_awaited()


@pytest.mark.parametrize("label", ["", "  ", "x" * 101, "line\nbreak"])
def test_invalid_label_is_rejected(label):
    service, session = make_service()
    params = arguments()
    params["label"] = label
    with pytest.raises(connections.OneCConnectionError, match="invalid_label"):
        run(service.create(**params))
    assert not session.rows
