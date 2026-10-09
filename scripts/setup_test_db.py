#!/usr/bin/env python3
"""Create, seed and reset the isolated test schema used by pytest.

The common POSTGRES_URL is supported with a separate DB_TEST_SCHEMA; a second
TEST_POSTGRES_URL database is optional. Never target the working schema.
resolve_and_redirect must run before app imports: engines and model schemas
are configured at import time. Shared-database bootstrap leaves the working
Alembic version table alone. Schema separation is not a privilege sandbox.

Fresh tables come from Base.metadata.create_all because the early migration
chain expects existing tables. Reference roles, permissions, platforms and the
bootstrap tenant are seeded with the canonical setup scripts. Existing drift
is reported, not automatically migrated.

    python -m scripts.setup_test_db            # create + seed if absent
    python -m scripts.setup_test_db --reset    # truncate, then re-seed (destructive)
    python -m scripts.setup_test_db --check    # redacted diagnostics; no DB calls
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKING_URL_ENV = "POSTGRES_URL"
TEST_URL_ENV = "TEST_POSTGRES_URL"
WORKING_SCHEMA_ENV = "DB_SCHEMA"
TEST_SCHEMA_ENV = "DB_TEST_SCHEMA"
# Keep aligned with Settings without importing app before the redirect.
DEFAULT_WORKING_SCHEMA = "public"
DEFAULT_TEST_SCHEMA = "test_schema"

# Platform rows the suite selects by platform_type. Keep in sync with
# app.types.PlatformType.
_PLATFORM_SEED = (
    ("vk", "ВКонтакте", "https://vk.com"),
    ("telegram", "Телеграм", "https://t.me"),
)


def database_name(url: str) -> str:
    """The database part of a postgres URL."""
    return urlsplit(url).path.lstrip("/")


def resolve_test_url() -> str:
    """Return optional TEST_POSTGRES_URL or the common POSTGRES_URL."""
    load_dotenv()
    explicit = os.environ.get(TEST_URL_ENV, "").strip()
    if explicit:
        return explicit
    return os.environ.get(WORKING_URL_ENV, "").strip()


def resolve_test_schema() -> str:
    """Return DB_TEST_SCHEMA or test_schema; redirect validates isolation."""
    load_dotenv()
    return os.environ.get(TEST_SCHEMA_ENV, "").strip() or DEFAULT_TEST_SCHEMA


def resolve_working_schema() -> str:
    """Effective canonical working schema, without importing application settings."""
    return os.environ.get(WORKING_SCHEMA_ENV, "").strip() or DEFAULT_WORKING_SCHEMA


def redact(url: str) -> str:
    """Display only endpoint/path; remove userinfo, query and fragment secrets."""
    if not url:
        return "(unset)"
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.hostname:
            return "(invalid URL)"
        host = parts.hostname
        if ":" in host:
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port is not None else ""
        credentials = "***@" if "@" in parts.netloc else ""
        return urlunsplit((parts.scheme, f"{credentials}{host}{port}", parts.path, "", ""))
    except ValueError:
        return "(invalid URL)"


def suggest_create_database(url: str) -> str:
    """The createdb line a superuser can run when the role lacks CREATEDB."""
    return f'createdb -O {urlsplit(url).username or "postgres"} "{database_name(url)}"'


async def _create_database_if_missing(test_url: str) -> str:
    """Create the test database if absent; an existing common DB needs no CREATEDB."""
    import asyncpg

    dsn = test_url.replace("postgresql+asyncpg://", "postgresql://")
    maintenance = urlunsplit(urlsplit(dsn)._replace(path="/postgres"))
    conn = await asyncpg.connect(maintenance)
    try:
        exists = await conn.fetchval("select 1 from pg_database where datname = $1", database_name(test_url))
        if exists:
            return "exists"
        await conn.execute(f'create database "{database_name(test_url)}"')
        return "created"
    finally:
        await conn.close()


async def _table_count() -> int:
    from app.core.config import settings
    from app.core.database import async_engine

    async with async_engine.connect() as conn:
        result = await conn.execute(
            text(
                "select count(*) from information_schema.tables "
                "where table_schema = :schema and table_type = 'BASE TABLE'"
            ),
            {"schema": settings.DB_SCHEMA},
        )
        return result.scalar_one()


async def _create_schema() -> None:
    """Create the application schema if the database does not have it yet."""
    from app.core.config import settings
    from app.core.database import async_engine

    async with async_engine.begin() as conn:
        await conn.execute(text(f'create schema if not exists "{settings.DB_SCHEMA}"'))


async def _create_tables() -> None:
    """Create tables via the same metadata path as a fresh deployment."""
    from app.core.database import async_engine
    from app.models import Base

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _missing_columns(conn, metadata, schema: str) -> list[str]:
    """Model columns missing in the schema; create_all only adds tables."""
    result = await conn.execute(
        text("select table_name, column_name from information_schema.columns " "where table_schema = :schema"),
        {"schema": schema},
    )
    present = {(table_name, column_name) for table_name, column_name in result}

    missing: list[str] = []
    for table in metadata.sorted_tables:
        if table.schema not in (None, schema):
            continue
        for column in table.columns:
            if (table.name, column.name) not in present:
                missing.append(f"{table.name}.{column.name}")
    return missing


def _stamp_head(url: str) -> None:
    """Stamp an independently isolated database after metadata table creation."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.stamp(config, "head")


async def _truncate_all() -> None:
    """Destructive reset of the redirected test schema; never a normal test step."""
    from app.core.config import settings
    from app.core.database import async_engine

    schema = settings.DB_SCHEMA
    async with async_engine.begin() as conn:
        result = await conn.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema = :schema and table_type = 'BASE TABLE' "
                "and table_name <> 'alembic_version'"
            ),
            {"schema": schema},
        )
        tables = [f'"{schema}"."{name}"' for name in result.scalars().all()]
        if tables:
            await conn.execute(text(f"truncate table {', '.join(tables)} restart identity cascade"))


async def _seed_reference_rows() -> None:
    """Insert canonical reference rows the suite reads."""
    from app.core.tenant_context import tenant_scope
    from scripts.setup.assign_roles_permissions import assign_roles_permissions
    from scripts.setup.roles import seed_roles

    seed_roles()
    with tenant_scope(bypass=True):
        await assign_roles_permissions()

    await _seed_platforms()
    await _seed_bootstrap_tenant()
    await _seed_permissions()


async def _seed_platforms() -> None:
    """One row per platform, so tests can select them by platform_type."""
    from app.models import Platform
    from app.types import PlatformType

    for platform_type, name, base_url in _PLATFORM_SEED:
        if await Platform.objects.filter(platform_type=platform_type).first() is None:
            await Platform.objects.create(
                name=name,
                platform_type=PlatformType[platform_type.upper()].db_value,
                base_url=base_url,
                params={},
            )


async def _seed_bootstrap_tenant() -> None:
    """Create the workspace used by tenant-scoped legacy fixtures."""
    from app.core.config import settings
    from app.models.managers.tenant_manager import tenants

    await tenants.get_or_create_owner(settings.DEFAULT_TENANT_SLUG)


async def _seed_permissions() -> None:
    """Use the canonical migration-hook permissions, not fixture codenames."""
    from app.core.database import engine
    from scripts.migrations.register_model_types import register_model_types

    with engine.begin() as conn:
        register_model_types(conn, is_upgrade=True)


def resolve_and_redirect() -> tuple[str, str, str]:
    """Validate isolation before app imports or changing URL/schema variables.

    Return (working_url, test_url, test_schema). Common POSTGRES_URL is allowed
    only with a different test schema. Database-name comparison is deliberately
    conservative; this is not live endpoint/alias identity verification.
    """
    test_url = resolve_test_url()
    if not test_url:
        raise RuntimeError(
            f"Neither {TEST_URL_ENV} nor {WORKING_URL_ENV} is set — cannot tell which database "
            f"the test suite may write to. Set {WORKING_URL_ENV} and a separate {TEST_SCHEMA_ENV}; "
            f"{TEST_URL_ENV} is optional."
        )

    working_url = os.environ.get(WORKING_URL_ENV, "").strip()
    test_schema = resolve_test_schema()
    working_schema = resolve_working_schema()

    same_database = bool(working_url) and database_name(working_url) == database_name(test_url)
    same_schema = test_schema == working_schema
    if same_database and same_schema:
        raise RuntimeError(
            f"{TEST_URL_ENV} and {TEST_SCHEMA_ENV} both resolve to the working target "
            f"({database_name(test_url)!r}, schema {test_schema!r}). The suite would read and write "
            f"the working data — point {TEST_SCHEMA_ENV} at a separate schema."
        )

    os.environ[WORKING_URL_ENV] = test_url
    os.environ[TEST_URL_ENV] = test_url
    os.environ[WORKING_SCHEMA_ENV] = test_schema
    return working_url, test_url, test_schema


async def ensure_test_database(test_url: str, *, shares_working_database: bool = False, reset: bool = False) -> None:
    """Ensure redirected test tables/reference rows; reset is explicitly destructive."""
    from app.core.database import async_engine

    def say(message: str) -> None:
        print(f"[test-db] {message}")

    try:
        state = await _create_database_if_missing(test_url)
    except Exception as exc:  # noqa: BLE001 - re-raised with an actionable hint below
        if "must be superuser" in str(exc) or "permission denied" in str(exc).lower():
            raise RuntimeError(
                f"Cannot create the database {database_name(test_url)!r} as the current role. "
                f"Create it once with a superuser:\n"
                f"    {suggest_create_database(test_url)}\n"
                f"then re-run pytest."
            ) from exc
        raise

    say(f"database {database_name(test_url)}: {state}")

    if state == "created" or await _table_count() == 0:
        await _create_schema()
        await _create_tables()
        if shares_working_database:
            # Shared bootstrap must not stamp the working public version table.
            say("schema created from the models (shared database: version table left alone)")
        else:
            _stamp_head(test_url)
            say(f"schema created from the models, stamped at head ({await _table_count()} tables)")
    elif reset:
        await _truncate_all()
        say("existing schema truncated (--reset)")

    await _create_schema()
    await _create_tables()

    from app.core.config import settings
    from app.core.database import async_engine
    from app.models import Base

    async with async_engine.connect() as conn:
        missing = await _missing_columns(conn, Base.metadata, settings.DB_SCHEMA)
    if missing:
        raise RuntimeError(
            "The test schema is behind the models (missing columns): "
            + ", ".join(sorted(missing))
            + ". `create_all` only adds tables, never columns — rebuild the schema, e.g.:\n"
            f"    psql -c 'drop schema \"{settings.DB_SCHEMA}\" cascade' && pytest"
        )

    await _seed_reference_rows()
    say("reference rows seeded (roles, permissions, platforms, bootstrap tenant)")

    await async_engine.dispose()


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Create/seed/reset the isolated test schema.")
    parser.add_argument("--reset", action="store_true", help="truncate every test table before seeding")
    parser.add_argument("--check", action="store_true", help="print redacted targets only, change nothing")
    args = parser.parse_args()

    if args.check:
        # Resolve/load dotenv before displaying the working target. No redirect,
        # app import, connection, schema creation, stamp or reset on this path.
        test_url = resolve_test_url()
        test_schema = resolve_test_schema()
        print(f"working: {redact(os.environ.get(WORKING_URL_ENV, ''))}")
        print(f"         schema={resolve_working_schema()}")
        print(f"test:    {redact(test_url)}")
        print(f"         schema={test_schema}")
        return 0

    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        working_url, test_url, _ = resolve_and_redirect()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    shares = bool(working_url) and database_name(working_url) == database_name(test_url)
    await ensure_test_database(test_url, shares_working_database=shares, reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
