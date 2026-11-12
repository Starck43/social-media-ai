"""Mocked authorization/SQL boundary; no database or network is contacted."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.models.managers import workspace_outbound_host_manager as hosts
from app.models.managers.trusted_outbound_host_manager import TrustedHostError
from app.models.managers.workspace_connection_manager import ConnectionDenied


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Session:
    def __init__(self, *values):
        self.values = iter(values)
        self.statements = []
        self.added = []
        self.deleted = []

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(next(self.values))

    def add(self, row):
        self.added.append(row)
        row.id = 19

    async def flush(self):
        pass

    async def delete(self, row):
        self.deleted.append(row)


def run(coro):
    return asyncio.run(coro)


def manager_with_session(session):
    manager = hosts.WorkspaceOutboundHostManager()

    @asynccontextmanager
    async def session_context():
        yield session

    manager._sessions._get_owned_session = session_context
    return manager


@pytest.mark.parametrize("role", ["manager", "analyst", "viewer", None])
def test_only_workspace_owner_or_admin_can_approve(monkeypatch, role):
    authorize = AsyncMock(return_value=True)
    monkeypatch.setattr(hosts, "_authorize", authorize)
    session = Session(role)
    with pytest.raises(ConnectionDenied):
        run(manager_with_session(session).approve(tenant_id=11, actor_id=7, host="erp.example.com"))
    authorize.assert_awaited_once_with(session, 11, 7, write=True)
    assert session.added == []


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_approval_canonicalizes_host_and_binds_tenant_actor(monkeypatch, role):
    monkeypatch.setattr(hosts, "_authorize", AsyncMock(return_value=True))
    session = Session(role)
    result = run(manager_with_session(session).approve(tenant_id=11, actor_id=7, host="ERP.Example.com."))
    assert result == {"id": 19, "host": "erp.example.com"}
    row = session.added[0]
    assert (row.tenant_id, row.approved_by_user_id) == (11, 7)
    assert session.statements[0].compile().params["tenant_id_1"] == 11


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "*.example.com", "https://erp.example.com"])
def test_invalid_host_cannot_be_approved(host):
    with pytest.raises(TrustedHostError):
        run(manager_with_session(Session()).approve(tenant_id=11, actor_id=7, host=host))


def test_use_requires_matching_tenant_and_shared_lock(monkeypatch):
    authorize = AsyncMock(return_value=True)
    monkeypatch.setattr(hosts, "_authorize", authorize)
    session = Session(19)
    value = run(
        hosts.WorkspaceOutboundHostManager().require_approved(
            session=session, tenant_id=11, actor_id=7, host="ERP.EXAMPLE.COM"
        )
    )
    assert value == "erp.example.com"
    authorize.assert_awaited_once_with(session, 11, 7, write=True)
    statement = session.statements[0]
    assert statement.compile().params == {"tenant_id_1": 11, "host_1": "erp.example.com"}
    assert statement._for_update_arg.read is True


def test_missing_or_foreign_approval_denies_use(monkeypatch):
    monkeypatch.setattr(hosts, "_authorize", AsyncMock(return_value=True))
    with pytest.raises(ConnectionDenied):
        run(
            hosts.WorkspaceOutboundHostManager().require_approved(
                session=Session(None), tenant_id=11, actor_id=7, host="erp.example.com"
            )
        )


def test_revoke_is_scoped_and_exclusively_locked(monkeypatch):
    monkeypatch.setattr(hosts, "_authorize", AsyncMock(return_value=True))
    row = object()
    session = Session("admin", row)
    run(manager_with_session(session).revoke(tenant_id=11, actor_id=7, host_id=19))
    assert session.deleted == [row]
    statement = session.statements[1]
    assert statement.compile().params == {"tenant_id_1": 11, "id_1": 19}
    assert statement._for_update_arg.read is False
