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


def setup_replace(monkeypatch, *, active=False, secret=True):
    from types import SimpleNamespace

    service, session = make_service()
    row = SimpleNamespace(
        id=19,
        provider_id="onec",
        auth_method="odata_credentials",
        schema_version=1,
        config={
            "host": "erp.example.com",
            "port": 443,
            "base_path": "/base",
            "interface": "odata_standard",
            "metadata_sha256": "old",
        },
        account_key="old",
        label="Accounting",
        state="disabled" if not active else "verified",
        is_active=active,
        check_code="old",
        checked_at="old",
    )
    secret_row = SimpleNamespace(secret_encrypted="old-ciphertext") if secret else None
    session.get = AsyncMock(return_value=secret_row)
    monkeypatch.setattr(connections, "_authorize", AsyncMock())
    monkeypatch.setattr(connections, "_connection", AsyncMock(return_value=row))
    monkeypatch.setattr(connections, "seal_basic_credentials", lambda login, password: "new-ciphertext")
    return service, session, row, secret_row


@pytest.mark.parametrize("active", [False, True])
def test_replace_credentials_resets_health_and_reactivates(monkeypatch, active):
    service, session, row, secret = setup_replace(monkeypatch, active=active)
    result = run(
        service.replace_credentials(
            tenant_id=11, actor_id=7, connection_id=19, login="new-login", password="new-password"
        )
    )
    connections._authorize.assert_awaited_once_with(session, 11, 7, write=True)
    connections._connection.assert_awaited_once_with(session, 11, 7, 19)
    service._hosts.require_approved.assert_awaited_once_with(
        session=session, tenant_id=11, actor_id=7, host="erp.example.com"
    )
    assert secret.secret_encrypted == "new-ciphertext"
    assert row.state == "unverified" and row.is_active and row.checked_at is None and row.check_code is None
    assert "metadata_sha256" not in row.config and row.account_key != "old"
    assert "new-password" not in str(result) + str(row.config) + row.account_key


def test_replace_credentials_creates_missing_secret(monkeypatch):
    service, session, row, _ = setup_replace(monkeypatch, secret=False)
    run(service.replace_credentials(tenant_id=11, actor_id=7, connection_id=19, login="user", password="password"))
    assert len(session.rows) == 1
    assert session.rows[0].connection_id == row.id
    assert session.rows[0].secret_encrypted == "new-ciphertext"


def test_replace_forbidden_host_or_wrong_provider_changes_nothing(monkeypatch):
    service, session, row, secret = setup_replace(monkeypatch)
    service._hosts.require_approved.side_effect = ConnectionDenied()
    with pytest.raises(ConnectionDenied):
        run(service.replace_credentials(tenant_id=11, actor_id=7, connection_id=19, login="user", password="password"))
    assert secret.secret_encrypted == "old-ciphertext" and not row.is_active
    service._hosts.require_approved.reset_mock(side_effect=True)
    row.provider_id = "another-erp"
    with pytest.raises(ConnectionDenied):
        run(service.replace_credentials(tenant_id=11, actor_id=7, connection_id=19, login="user", password="password"))
    service._hosts.require_approved.assert_not_awaited()


def test_replace_refuses_corrupt_stored_endpoint(monkeypatch):
    service, _, row, secret = setup_replace(monkeypatch)
    row.config["host"] = "127.0.0.1"
    with pytest.raises(connections.OneCConnectionError, match="invalid_configuration"):
        run(service.replace_credentials(tenant_id=11, actor_id=7, connection_id=19, login="user", password="password"))
    assert secret.secret_encrypted == "old-ciphertext"
    service._hosts.require_approved.assert_not_awaited()


def test_replace_duplicate_identity_returns_fixed_error(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    service, session, _, _ = setup_replace(monkeypatch)
    session.flush = AsyncMock(side_effect=IntegrityError("statement", {}, Exception("private details")))
    with pytest.raises(connections.OneCConnectionError) as exc:
        run(service.replace_credentials(tenant_id=11, actor_id=7, connection_id=19, login="user", password="password"))
    assert exc.value.code == "duplicate_connection"
    assert "private details" not in str(exc.value)


def test_replace_denied_actor_never_reads_connection_or_secret(monkeypatch):
    service, session, row, secret = setup_replace(monkeypatch)
    connections._authorize.side_effect = ConnectionDenied()
    with pytest.raises(ConnectionDenied):
        run(service.replace_credentials(tenant_id=11, actor_id=7, connection_id=19, login="user", password="password"))
    connections._connection.assert_not_awaited()
    session.get.assert_not_awaited()
    assert not row.is_active and secret.secret_encrypted == "old-ciphertext"
