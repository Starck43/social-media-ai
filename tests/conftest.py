"""Shared fixtures for the test suite.

Async tests share one event loop (see `asyncio_default_test_loop_scope` in
pyproject.toml), so here we only make sure the pooled DB connections are
closed from inside that same loop before the process exits.

The suite runs against its own database
---------------------------------------
Tests must never write into the working database, so the whole process is
redirected to ``TEST_POSTGRES_URL`` and ``DB_TEST_SCHEMA`` *before* anything
under ``app`` is imported. That ordering is not incidental:
``app.core.database`` builds its engines at import time from ``POSTGRES_URL``,
and the models bake ``settings.DB_SCHEMA`` into their ``__table_args__`` while
they load, so a redirect applied later would leave both the pool and the tables
on the working ones. ``resolve_and_redirect`` republishes both under the names
the application reads and refuses to continue when they resolve to the working
database *and* the working schema.

``scripts.setup_test_db`` then makes sure that database exists, carries the
schema and holds the reference rows the suite reads (roles, permissions,
platforms, the bootstrap workspace). It is idempotent, so the first `pytest`
after a clone sets everything up on its own. Its ``--reset`` truncates every
table, which is only needed when a run was interrupted mid-way.

Two escape hatches remain and are deliberate:

* ``tests/test_base_manager.py`` builds its own SQLite engine (the models are
  PostgreSQL-shaped, so that file never touches the database at all);
* ``tests/test_vk_collection.py`` builds its own engine from
  ``settings.POSTGRES_URL`` — which this module has already pointed at the
  test database — and only issues reads.

Tenancy: every business table is tenant-owned and `BaseManager` is fail-closed,
so the suite has to say *whose* data a test touches. Legacy tests describe the
single-workspace era and therefore run as the platform owner (bypass); tests
marked `@pytest.mark.tenancy` opt out and exercise the real guard.
"""

import sys
from pathlib import Path

# Make `scripts` importable when pytest is started from another directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Has to run before the first `app` import below — see the module docstring.
from scripts.setup_test_db import resolve_and_redirect  # noqa: E402

WORKING_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA = resolve_and_redirect()

import pytest  # noqa: E402

import app.models.managers.base_manager as base_manager_module  # noqa: E402


@pytest.fixture(autouse=True)
def _platform_scope(request, monkeypatch):
    """Run legacy tests inside the bootstrap workspace (superuser bypass)."""
    if request.node.get_closest_marker("tenancy"):
        yield
        return
    monkeypatch.setattr(base_manager_module, "is_bypass", lambda: True)
    # Legacy tests already model the operator surface. Make its authorization
    # explicit; security/tenancy tests opt out above and remain fail-closed.
    from app.core.permissions import operator_permission_scope

    with operator_permission_scope():
        yield


@pytest.fixture(scope="session", autouse=True)
async def _test_database():
    """Create and seed the test database before the first test runs."""
    from scripts.setup_test_db import database_name, ensure_test_database

    shares = bool(WORKING_DATABASE_URL) and database_name(WORKING_DATABASE_URL) == database_name(TEST_DATABASE_URL)
    await ensure_test_database(TEST_DATABASE_URL, shares_working_database=shares)


@pytest.fixture(scope="session", autouse=True)
async def _seed_reference_data(_test_database):
    """Idempotent seed of roles/permissions at session start.

    Runs on every test session (not only first deploy) so an interrupted run
    leaves no stale reference rows. The seed scripts are idempotent by design.
    """
    from app.core.tenant_context import tenant_scope
    from scripts.setup.assign_roles_permissions import assign_roles_permissions
    from scripts.setup.roles import seed_roles

    seed_roles()
    with tenant_scope(bypass=True):
        await assign_roles_permissions()
    yield
    # Restore reference rows in case a test mutated them
    seed_roles()
    with tenant_scope(bypass=True):
        await assign_roles_permissions()


@pytest.fixture(scope="session", autouse=True)
def _local_vault_key():
    """Tests encrypt fake credentials without needing a deployment secret.

    Function-level fixtures may still override this setting, including tests
    that deliberately exercise the missing-key error. The key is ephemeral.
    """
    from cryptography.fernet import Fernet

    from app.core.config import settings

    previous = settings.CREDENTIALS_KEY
    settings.CREDENTIALS_KEY = Fernet.generate_key().decode()
    yield
    settings.CREDENTIALS_KEY = previous
