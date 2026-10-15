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

The suite only validates the configured test schema read-only. Existing
unknown tables, missing columns or UNIQUE drift stop before any reference
writes. Schema creation, reset, migration and stamping are separate maintenance
operations and are never part of the pytest path.

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

from sqlalchemy import select, text

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
    """Validate the configured test schema and reference rows without writes."""
    from app.core.config import settings
    from app.core.database import async_engine
    from app.core.access_catalog import RESOURCES
    from app.models import Base, ModelType, Permission, Platform, PlatformRole, WorkspaceRole
    from app.types import ActionType
    from scripts.setup_test_db import check_existing_schema

    async with async_engine.connect() as conn:
        await conn.execute(text("set transaction isolation level repeatable read, read only"))
        await check_existing_schema(conn, Base.metadata, settings.DB_SCHEMA, require_all_tables=True)
        platform_roles = set((await conn.execute(select(PlatformRole.codename))).scalars())
        workspace_roles = set((await conn.execute(select(WorkspaceRole.codename))).scalars())
        active_platforms = {
            value.db_value if hasattr(value, "db_value") else value
            for value in (
                await conn.execute(select(Platform.platform_type).where(Platform.is_active.is_(True)))
            ).scalars()
        }
        registered_resources = set((await conn.execute(select(ModelType.resource_key))).scalars())
        permission_rows = await conn.execute(
            select(ModelType.resource_key, Permission.action_type).join(
                Permission, Permission.model_type_id == ModelType.id
            )
        )
        registered_permissions = {
            (resource_key, action.db_value if isinstance(action, ActionType) else str(action).lower())
            for resource_key, action in permission_rows
        }

    missing_platform_roles = {"user", "operator", "admin"} - platform_roles
    missing_workspace_roles = {"owner", "admin", "manager", "analyst", "viewer"} - workspace_roles
    missing_platforms = {"vk", "telegram"} - active_platforms
    missing_resources = {resource.key for resource in RESOURCES} - registered_resources
    expected_permissions = {(resource.key, action.db_value) for resource in RESOURCES for action in resource.actions}
    missing_permissions = expected_permissions - registered_permissions
    if (
        missing_platform_roles
        or missing_workspace_roles
        or missing_platforms
        or missing_resources
        or missing_permissions
    ):
        missing = []
        if missing_platform_roles:
            missing.append("platform roles: " + ", ".join(sorted(missing_platform_roles)))
        if missing_workspace_roles:
            missing.append("workspace roles: " + ", ".join(sorted(missing_workspace_roles)))
        if missing_platforms:
            missing.append("active platforms: " + ", ".join(sorted(missing_platforms)))
        if missing_resources:
            missing.append("access resources: " + ", ".join(sorted(missing_resources)))
        if missing_permissions:
            missing.append(
                "access permissions: "
                + ", ".join(f"{resource}.{action}" for resource, action in sorted(missing_permissions))
            )
        raise RuntimeError("The test schema is missing required reference rows: " + "; ".join(missing))


@pytest.fixture(scope="session", autouse=True)
async def _seed_reference_data(_test_database):
    """Keep the ordinary test startup read-only; the approved bootstrap seeds once."""
    yield


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
