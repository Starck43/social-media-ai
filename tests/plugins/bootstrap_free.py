"""Bootstrap-free pytest plugin for social-media-ai.

Provides minimal test environment setup WITHOUT schema repair or reference data seeding.
- Runs resolve_and_redirect BEFORE any app import
- Keeps platform_scope with tenancy opt-out
- Temporary vault key with restoration
- Session cleanup fixtures
- NO ensure_test_database, schema repair, or seed_reference_data
- Secrets from existing private environment (env vars), not code
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# CRITICAL: Redirect BEFORE any app import
from scripts.setup_test_db import resolve_and_redirect  # noqa: E402

WORKING_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA = resolve_and_redirect()

import pytest  # noqa: E402

import app.models.managers.base_manager as base_manager_module  # noqa: E402


@pytest.fixture(autouse=True)
def _platform_scope(request, monkeypatch):
    """Run legacy tests inside the bootstrap workspace (superuser bypass).

    Tests marked @pytest.mark.tenancy opt out and exercise the real guard.
    """
    if request.node.get_closest_marker("tenancy"):
        yield
        return
    monkeypatch.setattr(base_manager_module, "is_bypass", lambda: True)
    from app.core.permissions import operator_permission_scope

    with operator_permission_scope():
        yield


@pytest.fixture(scope="session", autouse=True)
def _local_vault_key():
    """Ephemeral Fernet key for credential encryption in tests."""
    from cryptography.fernet import Fernet

    from app.core.config import settings

    previous = settings.CREDENTIALS_KEY
    try:
        settings.CREDENTIALS_KEY = Fernet.generate_key().decode()
        yield
    finally:
        settings.CREDENTIALS_KEY = previous


@pytest.fixture(scope="session", autouse=True)
async def _close_db_engines():
    """Dispose SQLAlchemy engines at session end to avoid connection leaks."""
    from app.core.database import async_engine, engine

    try:
        yield
    finally:
        try:
            await async_engine.dispose()
        finally:
            engine.dispose()


def pytest_configure(config):
    config.addinivalue_line("markers", "bootstrap_free: test uses bootstrap-free plugin fixtures")
