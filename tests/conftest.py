"""Shared fixtures for the test suite.

Async tests share one event loop (see `asyncio_default_test_loop_scope` in
pyproject.toml), so here we only make sure the pooled DB connections are
closed from inside that same loop before the process exits.

Tenancy: every business table is tenant-owned and `BaseManager` is fail-closed,
so the suite has to say *whose* data a test touches. Legacy tests describe the
single-workspace era and therefore run as the platform owner (bypass); tests
marked `@pytest.mark.tenancy` opt out and exercise the real guard.
"""

import pytest

import app.models.managers.base_manager as base_manager_module
from app.core.database import async_engine

# Captured before any fixture patches the module attribute.
REAL_IS_BYPASS = base_manager_module.is_bypass


@pytest.fixture(autouse=True)
def _platform_scope(request, monkeypatch):
    """Run legacy tests inside the bootstrap workspace (superuser bypass)."""
    if request.node.get_closest_marker("tenancy"):
        yield
        return
    monkeypatch.setattr(base_manager_module, "is_bypass", lambda: True)
    yield


@pytest.fixture(scope="session", autouse=True)
async def _dispose_db_engine():
    yield
    await async_engine.dispose()
