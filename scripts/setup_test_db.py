#!/usr/bin/env python3
"""Create, seed and reset the *test* database used by the pytest suite.

The suite must never touch the working database. Everything the tests need in
their own database is created here, so a run against ``TEST_POSTGRES_URL`` is
reproducible: same schema, same reference rows, no leftovers from the previous
run.

Two levels of isolation
-----------------------
The database and the schema are switched separately, because the two settings
are read at different moments:

* ``TEST_POSTGRES_URL`` — the database. Optional; by default the working one is
  reused and the schema alone isolates the suite, so no ``CREATEDB`` privilege
  is needed. Set it to a separate database for a second line of defence.
* ``DB_TEST_SCHEMA`` — the schema inside it. Defaults to ``test_schema``.

:func:`resolve_and_redirect` republishes both under the names the application
reads (``POSTGRES_URL`` and ``DB_SCHEMA``) and must therefore run before the
first ``app`` import: the engines are built at import time, and the models bake
``settings.DB_SCHEMA`` into their ``__table_args__`` while they load. The
working values are kept to reject the one combination that is never safe — a
test target resolving to the working database *and* the working schema.

Why the bootstrap looks like this
---------------------------------
``alembic upgrade head`` cannot build the schema from nothing. ``0001`` is a
no-op and ``0002`` was never committed, while ``0003`` immediately does
``ALTER TABLE <schema>.users`` — so the first real migration expects a
schema and a ``users`` table that no revision creates. A fresh deployment
therefore gets its schema from ``Base.metadata.create_all`` (see
``app.core.database.init_db``), and the migrations only carry it forward from
there. This module does the same: create the schema and the tables, stamp
``head`` so Alembic considers the database current, then insert the reference
rows the migrations would never create for us.

Seeded rows are the ones the suite *reads*:

* ``model_types`` + ``permissions`` — via the same ``register_model_types``
  the migration hook uses, so permission codenames are real, not fixtures;
* the ``roles`` of ``UserRoleType`` and their permission matrix
  (``scripts.setup.*``, the canonical role/permission source);
* the platforms the tests select by ``platform_type`` (``vk``, ``telegram``);
* the bootstrap tenant ``settings.DEFAULT_TENANT_SLUG``.

Everything else (sources, tasks, users, LLM rows) the tests create themselves.

Run directly to inspect or repair a test database::

    python -m scripts.setup_test_db            # create + seed if absent
    python -m scripts.setup_test_db --reset    # truncate, then re-seed
    python -m scripts.setup_test_db --check    # report the resolved URL only
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
    """The database the suite may use, derived from the environment.

    An explicit ``TEST_POSTGRES_URL`` wins. Otherwise the working database is
    reused and the suite is isolated by schema alone (``DB_TEST_SCHEMA``): that
    needs no ``CREATEDB`` privilege, and a separate database is a second line of
    defence rather than the only one. ``""`` is returned when neither variable is
    set — the caller decides whether that is fatal.
    """
    load_dotenv()
    explicit = os.environ.get(TEST_URL_ENV, "").strip()
    if explicit:
        return explicit
    return os.environ.get(WORKING_URL_ENV, "").strip()


def resolve_test_schema() -> str:
    """The schema the suite may use, derived from the environment.

    An explicit ``DB_TEST_SCHEMA`` wins. Otherwise ``test_schema`` is used, so
    tests never share a schema with the working database even when both live
    in the same one.
    """
    load_dotenv()
    return os.environ.get(TEST_SCHEMA_ENV, "").strip() or DEFAULT_TEST_SCHEMA


def redact(url: str) -> str:
    """A URL safe to print in an error message (password masked)."""
    parts = urlsplit(url)
    if "@" in parts.netloc:
        user, _, host = parts.netloc.rpartition("@")
        parts = parts._replace(netloc=f"{user.split(':', 1)[0]}:***@{host}")
    return urlunsplit(parts)


def suggest_create_database(url: str) -> str:
    """The ``createdb`` line a superuser can run when the role lacks CREATEDB."""
    return f'createdb -O {urlsplit(url).username or "postgres"} "{database_name(url)}"'


async def _create_database_if_missing(test_url: str) -> str:
    """Create the test database if the server does not have it yet.

    Needs the CREATEDB privilege. Without it asyncpg raises a bare
    ``InsufficientPrivilegeError``; the caller turns that into the
    ``createdb`` hint.
    """
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
    """Create every table from the models.

    This is the same path a fresh deployment takes (``init_db``); see the
    module docstring for why the migration chain cannot be used instead.
    """
    from app.core.database import async_engine
    from app.models import Base

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _stamp_head(url: str) -> None:
    """Mark the database as being at the latest revision.

    The tables are already the current shape, so the chain has nothing left to
    do; without the stamp the next ``alembic upgrade`` would replay
    ``0003``...``0064`` on top of them and fail.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.stamp(config, "head")


async def _truncate_all() -> None:
    """Empty every table, so a run starts from a known state.

    A safety net, not the main mechanism: tests clean up after themselves. It
    only matters when an interrupted run left rows behind, and it is what
    ``--reset`` uses. Seed rows are restored right after.
    """
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
    """Insert the rows the suite reads. Assumes the tables are empty."""
    from app.core.tenant_context import tenant_scope
    from scripts.setup.assign_roles_permissions import assign_roles_permissions
    from scripts.setup.roles import seed_roles

    # The canonical role/permission source, shared with a real deployment:
    # the 7 UserRoleType roles and their matrix.
    seed_roles()
    with tenant_scope(bypass=True):
        await assign_roles_permissions()

    await _seed_platforms()
    await _seed_bootstrap_tenant()
    await _seed_permissions()


async def _seed_platforms() -> None:
    """One row per platform, so tests can select them by ``platform_type``."""
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
    """The workspace ``settings.DEFAULT_TENANT_SLUG`` names.

    ``BaseManager._default_tenant_id`` looks it up by slug and raises without
    it, so every tenant-scoped write fails closed on an empty database.
    """
    from app.core.config import settings
    from app.models.managers.tenant_manager import tenants

    await tenants.get_or_create_owner(settings.DEFAULT_TENANT_SLUG)


async def _seed_permissions() -> None:
    """Register the models and their permissions.

    Normally written by the ``on_version_apply`` hook in ``migrations/env.py``,
    which ``stamp`` never reaches. Running ``register_model_types`` by hand
    keeps the codenames real (``social.Source.view`` and friends) instead of
    inventing a fixture set that would drift from production.
    """
    from app.core.database import engine
    from scripts.migrations.register_model_types import register_model_types

    with engine.begin() as conn:
        register_model_types(conn, is_upgrade=True)


def resolve_and_redirect() -> tuple[str, str, str]:
    """Decide which database and schema the suite may use, then point the process at them.

    Returns ``(working_url, test_url, test_schema)``. Splitting this from
    :func:`ensure_test_database` matters for two reasons:

    * the comparison against the working database has to read ``POSTGRES_URL``
      *before* the redirect overwrites it;
    * the redirect has to happen before anything imports ``app.core.database``,
      because the engines are built at import time and a later change is
      ignored. The same holds for the schema: the models bake
      ``settings.DB_SCHEMA`` into their ``__table_args__`` when they are
      imported, so the test schema is published under ``DB_SCHEMA`` — the name
      the application itself reads.

    Isolation only needs the schema, so a shared database is allowed: the suite
    may run in ``test_schema`` next to the working schema. What is refused is
    the one combination that reaches the real data — the working database *and*
    the working schema at the same time.
    """
    test_url = resolve_test_url()
    if not test_url:
        raise RuntimeError(
            f"Neither {TEST_URL_ENV} nor {WORKING_URL_ENV} is set — cannot tell which database "
            f"the test suite may write to. Add {TEST_URL_ENV} to .env and try again."
        )

    working_url = os.environ.get(WORKING_URL_ENV, "").strip()
    test_schema = resolve_test_schema()
    working_schema = os.environ.get(WORKING_SCHEMA_ENV, "").strip()

    same_database = bool(working_url) and database_name(working_url) == database_name(test_url)
    same_schema = bool(working_schema) and test_schema == working_schema
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
    """Create the test database, give it a schema and the reference rows.

    Safe to call on every run: an existing database is left alone unless
    ``reset`` is set. ``test_url`` must come from :func:`resolve_and_redirect`,
    which has to run before the first ``app`` import.
    """
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
            # Alembic's version table is not schema-qualified, so it always lands
            # in `public` — the one table a shared database cannot spare. That one
            # records the *working* schema's revision, and stamping it from here
            # would mark real data as migrated when it is not. The test schema is
            # built from the models above and no migration ever targets it, so it
            # needs no stamp.
            say("schema created from the models (shared database: version table left alone)")
        else:
            _stamp_head(test_url)
            say(f"schema created from the models, stamped at head ({await _table_count()} tables)")
    elif reset:
        await _truncate_all()
        say("existing schema truncated (--reset)")

    # Create any tables the models gained since the schema was first built
    # (`create_all` is idempotent/checkfirst, so this only adds missing tables).
    await _create_schema()
    await _create_tables()

    await _seed_reference_rows()
    say("reference rows seeded (roles, permissions, platforms, bootstrap tenant)")

    await async_engine.dispose()


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Create/seed/reset the test database.")
    parser.add_argument("--reset", action="store_true", help="truncate every table before seeding")
    parser.add_argument("--check", action="store_true", help="only print the resolved URL, change nothing")
    args = parser.parse_args()

    if args.check:
        print(f"working: {os.environ.get(WORKING_URL_ENV, '(unset)')}")
        print(f"         schema={os.environ.get(WORKING_SCHEMA_ENV, '(unset)')}")
        print(f"test:    {resolve_test_url() or '(unset)'}")
        print(f"         schema={resolve_test_schema()}")
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
