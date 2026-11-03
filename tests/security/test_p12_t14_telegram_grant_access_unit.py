"""DB-exempt source-grant selection checks; PostgreSQL SQL is compiled, not run."""

import socket
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.core.tenant_context import tenant_scope
from app.services.social import connector_authorizations
from app.services.social import telegram_grant_access as access
from app.services.social.connector_authorizations import AuthorizationUnavailable


def source(**values):
    fields = dict(
        id=11,
        tenant_id=12,
        platform_id=13,
        external_id="synthetic-chat",
        owner_user_id=14,
        params={"mode": "user", "telegram_authorization": {"grant_id": 15, "authorization_revision": 2}},
    )
    fields.update(values)
    return SimpleNamespace(**fields)


@pytest.fixture(autouse=True)
def no_external_boundaries(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("forbidden boundary in DB-exempt tests")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(access, "async_session_maker", deny)
    monkeypatch.setattr(connector_authorizations, "decrypt_secret", deny)


class FakeSession:
    def __init__(self, row=None, error=None):
        self.row, self.error, self.statements = row, error, []
        self.transaction = None

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
        if self.error:
            raise self.error
        return self

    def one_or_none(self):
        return self.row


def use_session(monkeypatch, row=None, error=None):
    session = FakeSession(row, error)
    monkeypatch.setattr(access, "async_session_maker", lambda: session)
    return session


def test_selection_is_an_immutable_pin_not_a_secret_or_inferred_account():
    pin = access.source_grant_selection(source())
    assert pin == access.SourceGrantSelection(11, 12, 13, "synthetic-chat", 14, 15, 2)
    assert "synthetic-chat" not in repr(pin)
    with pytest.raises(FrozenInstanceError):
        pin.grant_id = 99
    with pytest.raises(AuthorizationUnavailable):
        access.source_grant_selection(source(params={}))


@pytest.mark.parametrize(
    "field", ["source_id", "tenant_id", "platform_id", "owner_user_id", "grant_id", "authorization_revision"]
)
@pytest.mark.parametrize("invalid", [None, True, False, 0, -1, "15", 1.0])
def test_all_pin_identifiers_are_strict_positive_integers(field, invalid):
    pin = access.source_grant_selection(source())
    with pytest.raises(AuthorizationUnavailable):
        replace(pin, **{field: invalid})


@pytest.mark.parametrize("invalid", [None, "", 1, "x" * 101])
def test_pin_requires_a_bounded_source_target(invalid):
    with pytest.raises(AuthorizationUnavailable):
        access.source_grant_selection(source(external_id=invalid))


@pytest.mark.parametrize(
    "params",
    [
        None,
        [],
        {"mode": "api", "telegram_authorization": {"grant_id": 15, "authorization_revision": 2}},
        {"mode": "bogus", "telegram_authorization": {"grant_id": 15, "authorization_revision": 2}},
        {"telegram_authorization": None},
        {"telegram_authorization": 15},
        {"telegram_authorization": {"grant_id": 15}},
        {"telegram_authorization": {"grant_id": 15, "authorization_revision": 2, "owner_user_id": 14}},
        {"telegram_authorization": {"grant_id": True, "authorization_revision": 2}},
        {"telegram_authorization": {"grant_id": 15, "authorization_revision": "2"}},
        {"telegram_authorization": {"grant_id": 15, "authorization_revision": 0}},
    ],
)
def test_missing_forged_or_legacy_selections_are_not_auto_attached(params):
    with pytest.raises(AuthorizationUnavailable):
        access.source_grant_selection(source(params=params))


def test_explicit_owner_is_pinned_and_invalid_owner_never_falls_back():
    row = source()
    row.params["token_owner"] = 17
    assert access.source_grant_selection(row).owner_user_id == 17
    for invalid in (True, 0, "17"):
        row.params["token_owner"] = invalid
        with pytest.raises(AuthorizationUnavailable):
            access.source_grant_selection(row)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant,bypass", [(None, False), (13, False), (12, True), (None, True), (True, False)])
async def test_wrong_missing_or_bypass_context_is_denied_before_session(tenant, bypass):
    pin = access.source_grant_selection(source())
    with tenant_scope(tenant, bypass=bypass), pytest.raises(AuthorizationUnavailable, match="^connection_unavailable$"):
        await access.inspect_source_grant(pin)


@pytest.mark.asyncio
async def test_only_typed_selections_are_accepted():
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        await access.inspect_source_grant({"source_id": 11})


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "analyst", "manager", "admin", "owner"])
async def test_one_fresh_joined_query_produces_only_non_secret_snapshot(monkeypatch, role):
    current = source()
    pin = access.source_grant_selection(current)
    session = use_session(monkeypatch, (current, 99, role))
    with tenant_scope(12):
        snapshot = await access.inspect_source_grant(pin)
    assert snapshot == access.SourceGrantSnapshot(pin, 99)
    assert session.transaction == "committed" and len(session.statements) == 1
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    selected = sql.split("\nFROM", 1)[0]
    assert "secret_encrypted" not in selected
    assert "subject_key" not in selected
    for table in (
        "sources",
        "platforms",
        "tenants",
        "users",
        "tenant_users",
        "workspace_roles",
        "workspace_authorization_grants",
        "personal_connector_authorizations",
    ):
        assert "test_schema." + table in sql
    assert (
        "personal_connector_authorizations.owner_user_id = test_schema.workspace_authorization_grants.owner_user_id"
        in sql
    )
    assert "workspace_authorization_grants.tenant_id = test_schema.sources.tenant_id" in sql
    assert "workspace_authorization_grants.owner_user_id = test_schema.users.id" in sql
    assert sql.count("is_active IS true") == 6
    for predicate in (
        "tenant_users.channel =",
        "platforms.platform_type =",
        "personal_connector_authorizations.auth_method =",
        "personal_connector_authorizations.provider_id =",
        "personal_connector_authorizations.state =",
        "workspace_authorization_grants.authorization_revision =",
        "personal_connector_authorizations.revision =",
    ):
        assert predicate in sql
    assert "platforms.name" not in sql  # display label is not provider identity
    assert "FOR UPDATE" not in sql  # snapshot only, not an I/O lease


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [None, "unknown", "superuser"])
async def test_platform_or_unknown_roles_are_not_workspace_consent(monkeypatch, role):
    current = source()
    session = use_session(monkeypatch, (current, 99, role))
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        await access.inspect_source_grant(access.source_grant_selection(current))
    assert session.transaction == "rolled_back"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"external_id": "edited-chat"},
        {"platform_id": 999},
        {"owner_user_id": 99},
        {"params": {"mode": "user", "telegram_authorization": {"grant_id": 16, "authorization_revision": 2}}},
        {"params": {"mode": "user", "telegram_authorization": {"grant_id": 15, "authorization_revision": 3}}},
        {"params": {}},
    ],
)
async def test_stale_queued_source_never_follows_new_target_owner_grant_or_revision(monkeypatch, change):
    pin = access.source_grant_selection(source())
    use_session(monkeypatch, (source(**change), 99, "manager"))
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        await access.inspect_source_grant(pin)


@pytest.mark.asyncio
async def test_each_inspection_reloads_and_no_prior_snapshot_authorizes_revocation(monkeypatch):
    row = source()
    session = use_session(monkeypatch, (row, 99, "manager"))
    pin = access.source_grant_selection(row)
    with tenant_scope(12):
        await access.inspect_source_grant(pin)
        session.row = None
        with pytest.raises(AuthorizationUnavailable, match="^connection_unavailable$"):
            await access.inspect_source_grant(pin)
    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_database_error_propagates_without_fallback_or_plaintext_read(monkeypatch):
    use_session(monkeypatch, error=RuntimeError("synthetic-storage-failure"))
    with tenant_scope(12), pytest.raises(RuntimeError, match="synthetic-storage-failure"):
        await access.inspect_source_grant(access.source_grant_selection(source()))
