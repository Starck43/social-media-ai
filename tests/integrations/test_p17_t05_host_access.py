"""Read-use of approved hosts preserves the legacy write authorization contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.managers import workspace_outbound_host_manager as hosts
from app.models.managers.workspace_connection_manager import ConnectionDenied


@pytest.mark.parametrize("read", [False, True])
async def test_host_use_keeps_exact_tenant_lock_and_distinct_authorization(monkeypatch, read):
    authorize = AsyncMock()
    monkeypatch.setattr(hosts, "_authorize", authorize)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 19)))
    manager = hosts.WorkspaceOutboundHostManager()
    require = manager.require_approved_for_read if read else manager.require_approved
    assert await require(session=session, tenant_id=11, actor_id=7, host="ERP.EXAMPLE.COM.") == "erp.example.com"
    authorize.assert_awaited_once_with(session, 11, 7, **({"require_source_view": True} if read else {"write": True}))
    statement = session.execute.call_args.args[0]
    assert statement.compile().params == {"tenant_id_1": 11, "host_1": "erp.example.com"}
    assert statement._for_update_arg.read is True


async def test_read_use_refuses_missing_or_foreign_approval(monkeypatch):
    monkeypatch.setattr(hosts, "_authorize", AsyncMock())
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
    with pytest.raises(ConnectionDenied):
        await hosts.WorkspaceOutboundHostManager().require_approved_for_read(
            session=session, tenant_id=11, actor_id=7, host="erp.example.com"
        )


async def test_read_use_checks_live_actor_before_looking_up_approval(monkeypatch):
    monkeypatch.setattr(hosts, "_authorize", AsyncMock(side_effect=ConnectionDenied()))
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ConnectionDenied):
        await hosts.WorkspaceOutboundHostManager().require_approved_for_read(
            session=session, tenant_id=11, actor_id=7, host="erp.example.com"
        )
    session.execute.assert_not_awaited()
