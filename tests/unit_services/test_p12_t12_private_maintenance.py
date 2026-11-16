"""The platform expiry worker is bounded and requires trusted bypass."""

__source_only__ = True

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.tenant_context import is_bypass, tenant_scope
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


@pytest.mark.asyncio
async def test_web_maintenance_runs_private_gc_under_bypass_without_lifespan(monkeypatch):
    from app import main

    scopes = []

    async def purge():
        assert is_bypass()
        return 3

    gc = AsyncMock(side_effect=purge)
    jobs = AsyncMock(return_value=0)
    cleanup = AsyncMock(side_effect=RuntimeError("synthetic job cleanup failure"))
    monkeypatch.setattr("app.jobs.lifecycle.reap_stale_jobs", jobs)
    monkeypatch.setattr("app.jobs.lifecycle.cleanup_terminal_job_history", cleanup)
    monkeypatch.setattr(private_maintenance, "purge_expired_private_insights", gc)

    original_scope = main.tenant_scope

    class ScopeProxy:
        def __call__(self, *, bypass=False, **kwargs):
            scopes.append(bypass)
            return original_scope(bypass=bypass, **kwargs)

    monkeypatch.setattr(main, "tenant_scope", ScopeProxy())

    async def stop_after_iteration(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", stop_after_iteration)
    with pytest.raises(asyncio.CancelledError):
        await main._job_maintenance_loop()

    jobs.assert_awaited_once()
    cleanup.assert_awaited_once()
    gc.assert_awaited_once_with(limit=100)
    assert scopes == [True, True]
    assert not is_bypass()


@pytest.mark.asyncio
async def test_telegram_maintenance_attempts_private_gc_after_platform_cleanup_failure(monkeypatch):
    from app.jobs import dispatcher

    async def purge(**kwargs):
        assert is_bypass()
        assert kwargs == {"limit": 100}
        return 1

    gc = AsyncMock(side_effect=purge)
    cleanup = AsyncMock(side_effect=RuntimeError("synthetic Telegram cleanup failure"))
    module = __import__("app.services.social.telegram_login_maintenance", fromlist=["expire_attempt_batch"])
    monkeypatch.setattr(module, "expire_attempt_batch", cleanup)
    monkeypatch.setattr(module, "wipe_terminal_attempt_batch", AsyncMock(return_value=0))
    monkeypatch.setattr(module, "purge_attempt_batch", AsyncMock(return_value=0))
    monkeypatch.setattr(private_maintenance, "purge_expired_private_insights", gc)

    async def stop_after_iteration(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(dispatcher.asyncio, "sleep", stop_after_iteration)
    with pytest.raises(asyncio.CancelledError):
        await dispatcher._telegram_login_maintenance_loop()

    cleanup.assert_awaited_once()
    gc.assert_awaited_once_with(limit=100)
    assert not is_bypass()


@pytest.mark.asyncio
async def test_telegram_maintenance_retries_immediately_after_full_private_batch(monkeypatch):
    from app.jobs import dispatcher

    module = __import__("app.services.social.telegram_login_maintenance", fromlist=["expire_attempt_batch"])
    monkeypatch.setattr(module, "expire_attempt_batch", AsyncMock(return_value=0))
    monkeypatch.setattr(module, "wipe_terminal_attempt_batch", AsyncMock(return_value=0))
    monkeypatch.setattr(module, "purge_attempt_batch", AsyncMock(return_value=0))

    async def purge(**kwargs):
        assert is_bypass()
        assert kwargs == {"limit": 100}
        return 100

    gc = AsyncMock(side_effect=purge)
    monkeypatch.setattr(private_maintenance, "purge_expired_private_insights", gc)
    delays = []

    async def stop_after_iteration(seconds):
        delays.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(dispatcher.asyncio, "sleep", stop_after_iteration)
    with pytest.raises(asyncio.CancelledError):
        await dispatcher._telegram_login_maintenance_loop()

    gc.assert_awaited_once_with(limit=100)
    assert delays == [1]
    assert not is_bypass()
