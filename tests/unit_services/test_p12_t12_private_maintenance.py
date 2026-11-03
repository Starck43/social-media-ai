"""The platform expiry worker is bounded and requires trusted bypass."""

__source_only__ = True

from unittest.mock import AsyncMock

import pytest

from app.core.tenant_context import tenant_scope
from app.models.managers.file_insight_manager import PrivateInsightUnavailable
from app.services.file_workspace import private_maintenance


@pytest.mark.asyncio
async def test_maintenance_requires_superuser_bypass_before_database_access(monkeypatch):
    run = AsyncMock(return_value=0)
    monkeypatch.setattr(private_maintenance.FileInsightManager, "_purge_expired_batch", run)

    with pytest.raises(PrivateInsightUnavailable):
        await private_maintenance.purge_expired_private_insights()

    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_maintenance_uses_existing_bounded_batch_under_bypass(monkeypatch):
    run = AsyncMock(return_value=7)
    monkeypatch.setattr(private_maintenance.FileInsightManager, "_purge_expired_batch", run)

    with tenant_scope(bypass=True):
        assert await private_maintenance.purge_expired_private_insights(limit=7) == 7

    run.assert_awaited_once_with(7)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5])
async def test_maintenance_refuses_unbounded_or_noninteger_limits(monkeypatch, limit):
    run = AsyncMock(return_value=0)
    monkeypatch.setattr(private_maintenance.FileInsightManager, "_purge_expired_batch", run)

    with tenant_scope(bypass=True), pytest.raises(PrivateInsightUnavailable):
        await private_maintenance.purge_expired_private_insights(limit=limit)

    run.assert_not_awaited()
