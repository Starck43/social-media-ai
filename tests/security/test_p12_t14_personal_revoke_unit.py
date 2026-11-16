"""DB-exempt personal revocation contracts with explicit fake transactions."""

import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.services.social import connector_authorizations as accounts
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.connector_authorizations import connector_authorizations as manager

ACTOR = SimpleNamespace(id=7, is_active=True)
METADATA = dict(
    authorization_id=12, provider_id="telegram", auth_method="mtproto", subject_key="90001", revision=1, state="active"
)


def account(**changes):
    values = dict(
        id=12,
        owner_user_id=7,
        provider_id="telegram",
        auth_method="mtproto",
        subject_key="90001",
        revision=1,
        state="active",
        secret_encrypted="SYNTHETIC-CIPHERTEXT",
    )
    values.update(changes)
    return SimpleNamespace(**values)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.value


class FakeSession:
    def __init__(self, results):
        self.results, self.statements, self.transaction = iter(results), [], None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @asynccontextmanager
    async def begin(self):
        try:
            yield
        except BaseException:
            self.transaction = "rolled_back"
            raise
        else:
            self.transaction = "committed"

    async def execute(self, statement):
        self.statements.append(statement)
        value = next(self.results)
        if isinstance(value, BaseException):
            raise value
        return FakeResult(value)


@pytest.fixture(autouse=True)
def no_database_or_decryption(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DB/provider/decryption forbidden in unit tests")

    monkeypatch.setattr(accounts, "async_session_maker", forbidden)
    monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def use_session(monkeypatch, results):
    session = FakeSession(results)
    monkeypatch.setattr(accounts, "async_session_maker", lambda: session)
    return session


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_revoke_without_membership_key_or_provider_clears_ciphertext_and_all_own_grants(monkeypatch):
    row = account()
    session = use_session(monkeypatch, [7, row, None])
    with tenant_scope(), permission_scope(ACTOR):
        result = await manager.revoke_personal(actor_id=7, authorization_id=12, expected_revision=1)
    assert result == {**METADATA, "revision": 2, "state": "revoked"}
    assert row.secret_encrypted == "" and session.transaction == "committed"
    assert len(session.statements) == 3
    active_user, owned_account, grants = map(sql, session.statements)
    assert "users.is_active IS true" in active_user and "FOR SHARE" in active_user
    assert "personal_connector_authorizations.owner_user_id =" in owned_account
    assert "FOR UPDATE" in owned_account
    assert "secret_encrypted" not in owned_account.split("\nFROM", 1)[0]
    assert "workspace_authorization_grants.owner_user_id =" in grants
    assert "workspace_authorization_grants.authorization_id =" in grants
    assert "tenant_id" not in grants  # withdraw every own consent, not one tenant
    assert "secret" not in repr(result).lower() and "SYNTHETIC" not in repr(result)


@pytest.mark.asyncio
async def test_personal_list_never_reads_ciphertext_grants_or_workspace_data(monkeypatch):
    session = use_session(monkeypatch, [7, [METADATA]])
    with tenant_scope(), permission_scope(ACTOR):
        assert await manager.list_personal(actor_id=7) == [METADATA]
    query = sql(session.statements[-1])
    assert "secret_encrypted" not in query
    assert "workspace_authorization_grants" not in query and "tenant_users" not in query
    assert "owner_user_id =" in query and "provider_id =" in query and "auth_method =" in query
    assert session.transaction == "committed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor,tenant,bypass,actor_id",
    [
        (None, None, False, 7),
        (SimpleNamespace(id=8, is_active=True), None, False, 7),
        (SimpleNamespace(id=7, is_active=False), None, False, 7),
        (ACTOR, 12, False, 7),
        (ACTOR, None, True, 7),
        (ACTOR, None, False, True),
        (ACTOR, None, False, 0),
    ],
)
async def test_actor_spoofing_inactive_account_and_ambient_scopes_are_denied_before_query(
    monkeypatch, actor, tenant, bypass, actor_id
):
    session = use_session(monkeypatch, [])
    with tenant_scope(tenant, bypass=bypass), permission_scope(actor), pytest.raises(AuthorizationUnavailable):
        await manager.revoke_personal(actor_id=actor_id, authorization_id=12, expected_revision=1)
    assert session.statements == []


@pytest.mark.asyncio
async def test_fresh_user_deactivation_denies_even_if_authentication_snapshot_was_active(monkeypatch):
    session = use_session(monkeypatch, [None])
    with tenant_scope(), permission_scope(ACTOR), pytest.raises(AuthorizationUnavailable):
        await manager.revoke_personal(actor_id=7, authorization_id=12, expected_revision=1)
    assert len(session.statements) == 1 and session.transaction == "rolled_back"


@pytest.mark.asyncio
async def test_missing_or_foreign_account_has_same_opaque_denial(monkeypatch):
    session = use_session(monkeypatch, [7, None])
    with (
        tenant_scope(),
        permission_scope(ACTOR),
        pytest.raises(AuthorizationUnavailable, match="^connection_unavailable$"),
    ):
        await manager.revoke_personal(actor_id=7, authorization_id=12, expected_revision=1)
    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_stale_revision_never_mutates_account_or_grants(monkeypatch):
    row = account(revision=2)
    session = use_session(monkeypatch, [7, row])
    with tenant_scope(), permission_scope(ACTOR), pytest.raises(AuthorizationUnavailable):
        await manager.revoke_personal(actor_id=7, authorization_id=12, expected_revision=1)
    assert row.state == "active" and row.secret_encrypted == "SYNTHETIC-CIPHERTEXT"
    assert len(session.statements) == 2 and session.transaction == "rolled_back"


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, True, "1", 0, -1])
async def test_revision_is_strict_and_rejected_before_session(revision):
    with tenant_scope(), permission_scope(ACTOR), pytest.raises(AuthorizationUnavailable):
        await manager.revoke_personal(actor_id=7, authorization_id=12, expected_revision=revision)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["reauth", "revoked"])
async def test_unusable_account_can_be_revoked_and_repeated_current_revision_repairs_grants(monkeypatch, state):
    row = account(state=state)
    session = use_session(monkeypatch, [7, row, None])
    with tenant_scope(), permission_scope(ACTOR):
        result = await manager.revoke_personal(actor_id=7, authorization_id=12, expected_revision=1)
    assert result["revision"] == (1 if state == "revoked" else 2)
    assert result["state"] == "revoked" and row.secret_encrypted == ""
    assert len(session.statements) == 3


@pytest.mark.asyncio
async def test_transaction_failure_propagates_without_success_projection(monkeypatch):
    session = use_session(monkeypatch, [7, account(), RuntimeError("synthetic-update-failure")])
    with tenant_scope(), permission_scope(ACTOR), pytest.raises(RuntimeError, match="synthetic-update-failure"):
        await manager.revoke_personal(actor_id=7, authorization_id=12, expected_revision=1)
    assert session.transaction == "rolled_back"  # fake transaction, not PostgreSQL rollback proof


@pytest.mark.asyncio
async def test_workspace_service_keeps_its_fresh_membership_check_and_response(monkeypatch):
    row = account()
    session = use_session(monkeypatch, [row, None])
    authorize = AsyncMock()
    monkeypatch.setattr(accounts, "_authorize", authorize)
    with tenant_scope(12), permission_scope(ACTOR):
        result = await manager.revoke(tenant_id=12, actor_id=7, authorization_id=12, expected_revision=1)
    authorize.assert_awaited_once_with(session, 12, 7, require_source_view=True)
    assert result == {**METADATA, "revision": 2, "state": "revoked"}
    assert row.secret_encrypted == "" and session.transaction == "committed"


@pytest.mark.asyncio
async def test_default_account_loader_still_supplies_ciphertext_to_existing_attach_path(monkeypatch):
    row = account()
    session = use_session(monkeypatch, [row])
    assert await accounts._account(session, 7, 12) is row
    assert "secret_encrypted" in sql(session.statements[0]).split("\nFROM", 1)[0]
