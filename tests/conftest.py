"""Shared fixtures for the test suite.

Async tests share one event loop (see `asyncio_default_test_loop_scope` in
pyproject.toml), so here we only make sure the pooled DB connections are
closed from inside that same loop before the process exits.
"""

import pytest

from app.core.database import async_engine


@pytest.fixture(scope="session", autouse=True)
async def _dispose_db_engine():
    yield
    await async_engine.dispose()
