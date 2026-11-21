from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.integrations import task_resources as resources
from app.integrations.catalog.task_capabilities import Direction, ResourceKind

__source_only__ = True


@pytest.mark.asyncio
async def test_resolution_requires_explicit_account_and_rechecks_revocation(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    connection = SimpleNamespace(
        id=7,
        provider_id="mailru_mail",
        schema_version=resources.CATALOG_VERSION,
        auth_method="app_password",
        is_active=True,
        state="verified",
        config={},
    )
    monkeypatch.setattr(resources, "_connection", AsyncMock(return_value=connection))
    session = SimpleNamespace(scalar=AsyncMock(return_value=7))
    request = dict(tenant_id=1, actor_id=2, capability_id="mailru_mail.headers.read", direction=Direction.READ)
    selection = resources.ResourceSelection(ResourceKind.MAIL_FOLDER, 7, "INBOX")
    assert (
        await resources.resolve_task_resource(session, **request, selection=None)
        == resources.ResolutionIssue.SELECT_RESOURCE
    )
    assert await resources.resolve_task_resource(session, **request, selection=selection) == resources.ScopedResource(
        1, 2, "mailru_mail.headers.read", selection
    )
    connection.is_active = False
    assert (
        await resources.resolve_task_resource(session, **request, selection=selection)
        == resources.ResolutionIssue.UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_read_only_and_reserved_operations_never_read_connection(monkeypatch):
    monkeypatch.setattr(resources, "_authorize", AsyncMock())
    connection = AsyncMock()
    monkeypatch.setattr(resources, "_connection", connection)
    common = dict(tenant_id=1, actor_id=2, selection=None)
    assert (
        await resources.resolve_task_resource(
            None, **common, capability_id="bitrix24.tasks.list", direction=Direction.WRITE
        )
        == resources.ResolutionIssue.READ_ONLY
    )
    assert (
        await resources.resolve_task_resource(
            None, **common, capability_id="telegram.bot.read", direction=Direction.READ
        )
        == resources.ResolutionIssue.UNSUPPORTED
    )
    connection.assert_not_awaited()
