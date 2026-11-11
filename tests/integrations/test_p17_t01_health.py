"""Exercise health orchestration with explicit fake sessions and transport."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations.onec import health
from app.integrations.onec.credentials import BasicCredentials, OneCCredentialError
from app.integrations.onec.transport import HealthResult, OneCHealthError
from app.models.managers.workspace_connection_manager import ConnectionDenied


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self, row, secret):
        self.row, self.secret = row, secret
        self.statements = []
        self.flushed = False

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.row)

    async def get(self, model, key):
        return self.secret

    async def flush(self):
        self.flushed = True


def fixture(monkeypatch):
    config = {"host": "erp.example.com", "port": 443, "base_path": "/base", "interface": "odata_standard"}
    row = SimpleNamespace(
        id=5,
        is_active=True,
        provider_id="onec",
        auth_method="odata_credentials",
        schema_version=1,
        config=config,
        account_key="account-digest",
        label="ERP",
        state="unverified",
        check_code=None,
        checked_at=None,
    )
    secret = SimpleNamespace(secret_encrypted="ciphertext")
    first, second = Session(row, secret), Session(row, secret)
    sessions = iter([first, second])
    service = health.OneCHealthService()

    @asynccontextmanager
    async def transaction():
        yield next(sessions)

    service._sessions._get_owned_session = transaction
    service._hosts.require_approved = AsyncMock(return_value="erp.example.com")
    monkeypatch.setattr(health, "_authorize", AsyncMock())
    monkeypatch.setattr(health, "_connection", AsyncMock(return_value=row))
    monkeypatch.setattr(health, "open_basic_credentials", lambda token: BasicCredentials("login", "password"))
    monkeypatch.setattr(health, "budget_for", lambda provider, account, *, connection_id: "synthetic-budget")
    transport = AsyncMock(return_value=HealthResult("healthy", "a" * 64))
    monkeypatch.setattr(health, "check_health", transport)
    return service, row, secret, first, second, transport


def run(service):
    return asyncio.run(service.check(tenant_id=11, actor_id=7, connection_id=5))


def test_success_locks_connection_and_approval_and_persists_hash(monkeypatch):
    service, row, _, first, second, transport = fixture(monkeypatch)
    result = run(service)
    assert first.statements[0]._for_update_arg.read is True
    assert first.statements[0].compile().params == {"id_1": 5, "tenant_id_1": 11, "owner_user_id_1": 7}
    assert service._hosts.require_approved.await_count == 2
    assert transport.await_count == 1
    assert transport.await_args.kwargs["budget"] == "synthetic-budget"
    assert row.state == "verified" and row.config["metadata_sha256"] == "a" * 64
    assert result["check_code"] == "healthy" and second.flushed
    assert "password" not in str(result)


def test_revoked_host_blocks_dispatch(monkeypatch):
    service, row, _, _, second, transport = fixture(monkeypatch)
    service._hosts.require_approved.side_effect = ConnectionDenied()
    with pytest.raises(ConnectionDenied):
        run(service)
    transport.assert_not_awaited()
    assert row.state == "unverified" and not second.flushed


def test_credential_rotation_during_network_refuses_stale_result(monkeypatch):
    service, row, secret, _, second, transport = fixture(monkeypatch)

    async def rotate(*args, **kwargs):
        secret.secret_encrypted = "new-ciphertext"
        return HealthResult("healthy", "a" * 64)

    transport.side_effect = rotate
    with pytest.raises(ConnectionDenied):
        run(service)
    assert row.state == "unverified" and not second.flushed


def test_disabled_during_network_cannot_be_revived(monkeypatch):
    service, row, _, _, second, transport = fixture(monkeypatch)

    async def disable(*args, **kwargs):
        row.is_active = False
        return HealthResult("healthy", "a" * 64)

    transport.side_effect = disable
    with pytest.raises(ConnectionDenied):
        run(service)
    assert row.state == "unverified" and not second.flushed


def test_failed_auth_is_fixed_code_and_reauth(monkeypatch):
    service, row, _, _, _, transport = fixture(monkeypatch)
    transport.side_effect = OneCHealthError("authentication_failed")
    result = run(service)
    assert result["state"] == "reauth" and result["check_code"] == "authentication_failed"
    assert row.config["metadata_sha256"] is None


def test_unreadable_secret_never_contacts_provider(monkeypatch):
    service, _, _, _, _, transport = fixture(monkeypatch)

    def unavailable(_):
        raise OneCCredentialError("secret_unavailable")

    monkeypatch.setattr(health, "open_basic_credentials", unavailable)
    result = run(service)
    assert result["state"] == "reauth" and result["check_code"] == "secret_unavailable"
    transport.assert_not_awaited()


def test_shared_budget_deferral_is_recorded_without_leaking_details(monkeypatch):
    service, _, _, _, _, transport = fixture(monkeypatch)
    transport.side_effect = health.ProviderDeferred(120)
    result = run(service)
    assert (result["state"], result["check_code"]) == ("error", "rate_limited")
