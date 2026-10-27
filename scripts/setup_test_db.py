#!/usr/bin/env python3
"""Create, seed and reset the isolated test schema used by pytest.

The common POSTGRES_URL is supported with a separate DB_TEST_SCHEMA; a second
TEST_POSTGRES_URL database is optional. Never target the working schema.
resolve_and_redirect must run before app imports: engines and model schemas
are configured at import time. Shared-database bootstrap leaves the working
Alembic version table alone. Schema separation is not a privilege sandbox.

Fresh tables come from Base.metadata.create_all because the early migration
chain expects existing tables. Reference roles, permissions, platforms and the
bootstrap tenant are seeded with the canonical setup scripts; the committed
legacy catalog owner (migration 0109) is restored directly because the
create_all path never executes migration data changes. Existing drift
is checked before schema creation, reset or seeding and is never automatically
repaired. A branch missing live table metadata must stop, not remove evidence.

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
# The pre-redirect working target, captured by the first `resolve_and_redirect`
# call so a second caller can never compare the test schema against itself.
_REDIRECTED_WORKING: list[str] = []
# Keep aligned with Settings without importing app before the redirect.
DEFAULT_WORKING_SCHEMA = "public"
DEFAULT_TEST_SCHEMA = "test_schema"

# Platform rows the suite selects by platform_type. Keep in sync with
# app.types.PlatformType.
_PLATFORM_SEED = (
    ("vk", "ВКонтакте", "https://vk.com"),
    ("telegram", "Телеграм", "https://t.me"),
)

# Committed legacy catalog owner: migration 0109 assigns the personal catalog
# to this stable user id and refuses to run unless the user pre-exists and
# is active. The create_all + stamp head path never executes that data
# change, so the documented re-seed restores the FK target.
LEGACY_CATALOG_OWNER_ID = 2


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


async def _missing_columns(conn, metadata, schema: str, *, existing_tables: set[str] | None = None) -> list[str]:
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
        if existing_tables is not None and table.name not in existing_tables:
            continue
        for column in table.columns:
            if (table.name, column.name) not in present:
                missing.append(f"{table.name}.{column.name}")
    return missing


def _model_uniques(metadata, schema: str) -> dict:
    """UNIQUE constraints the models declare, keyed by name (or columns)."""
    from sqlalchemy import UniqueConstraint

    out: dict = {}
    for table in metadata.sorted_tables:
        if table.schema not in (None, schema):
            continue
        uniques: dict = {}
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            cols = tuple(sorted(column.name for column in constraint.columns))
            uniques[constraint.name or f"cols:{','.join(cols)}"] = cols
        if uniques:
            out[table.name] = uniques
    return out


async def _db_uniques(conn, schema: str) -> dict:
    """UNIQUE constraints the live schema has, with their column sets."""
    result = await conn.execute(
        text(
            "select tc.table_name, tc.constraint_name, "
            "string_agg(kcu.column_name, ',' order by kcu.column_name) "
            "from information_schema.table_constraints tc "
            "join information_schema.key_column_usage kcu "
            "  on kcu.constraint_name = tc.constraint_name and kcu.table_schema = tc.table_schema "
            "  and kcu.table_name = tc.table_name "
            "where tc.table_schema = :schema and tc.constraint_type = 'UNIQUE' "
            "group by tc.table_name, tc.constraint_name"
        ),
        {"schema": schema},
    )
    out: dict = {}
    for table, name, columns in result:
        out.setdefault(table, {})[name] = tuple(columns.split(","))
    return out


def unique_constraint_drift(model_uniques: dict, db_uniques: dict) -> tuple[list, list]:
    """Diff model-declared UNIQUE constraints against the live ones.

    Returns (stale, missing): `stale` are live constraints whose column set no
    model declares, `missing` are model constraints the schema does not have.

    `create_all` creates missing tables with their constraints, but a model
    that narrows or widens an *existing* UNIQUE constraint keeps the stale one
    — a regression can then pass or fail against a schema nobody chose (a
    session constraint that was never widened to
    (tenant_id, channel, chat_id) still rejects the second workspace's row).
    Drift is diagnostic only: bootstrap must never apply this diff as DDL.

    Column sets, not names, decide staleness: `Column(unique=True)` declares no
    name, and the database calls it `<table>_<column>_key`.
    """
    stale: list[tuple[str, str]] = []
    missing: list[tuple[str, tuple, str]] = []
    for table in sorted(set(model_uniques) | set(db_uniques)):
        model = model_uniques.get(table, {})
        db = db_uniques.get(table, {})
        model_columns = {frozenset(cols) for cols in model.values()}
        for name, columns in sorted(db.items()):
            if frozenset(columns) not in model_columns:
                stale.append((table, name))
        for name, columns in sorted(model.items()):
            if any(frozenset(db[k]) == frozenset(columns) for k in db):
                continue
            missing.append((table, columns, name))
    return stale, missing


async def check_existing_schema(conn, metadata, schema: str, *, require_all_tables: bool = False) -> None:
    """Refuse incompatible existing tables using catalog reads only."""
    result = await conn.execute(
        text(
            "select table_name from information_schema.tables "
            "where table_schema = :schema and table_type = 'BASE TABLE'"
        ),
        {"schema": schema},
    )
    existing_tables = {row[0] for row in result}
    model_tables = {table.name for table in metadata.sorted_tables if table.schema in (None, schema)}
    # Alembic owns its version table, not ORM metadata. It is never altered here.
    unknown = existing_tables - model_tables - {"alembic_version"}
    if unknown:
        raise RuntimeError(
            "Test bootstrap refused: live tables absent from this branch's model metadata: "
            + ", ".join(sorted(unknown))
            + ". Preserve their constraints and evidence; coordinate with the schema owner. "
            "Do not reset, drop or repair the schema to make this branch pass."
        )

    if require_all_tables and model_tables - existing_tables:
        raise RuntimeError(
            "Test bootstrap refused: model tables still absent after table creation: "
            + ", ".join(sorted(model_tables - existing_tables))
            + ". Stop; no reference rows were seeded."
        )

    missing_columns = await _missing_columns(conn, metadata, schema, existing_tables=existing_tables)
    if missing_columns:
        raise RuntimeError(
            "Test bootstrap refused: existing tables have missing model columns: "
            + ", ".join(sorted(missing_columns))
            + ". Stop and request separately approved, schema-qualified reconciliation; no automatic rebuild."
        )

    # Absent model tables may be created by the existing fresh-table path.
    model_uniques = {
        name: uniques for name, uniques in _model_uniques(metadata, schema).items() if name in existing_tables
    }
    stale, missing = unique_constraint_drift(model_uniques, await _db_uniques(conn, schema))
    if stale or missing:
        details = [f"unexpected {table}.{name}" for table, name in stale]
        details.extend(f"missing {table}.{name}" for table, _, name in missing)
        raise RuntimeError(
            "Test bootstrap refused: UNIQUE constraint drift: "
            + ", ".join(details)
            + ". No constraints were changed. Coordinate a separately approved reconciliation; "
            "do not reset, drop or repair the schema automatically."
        )


def _stamp_head(url: str) -> None:
    """Stamp an independently isolated database after metadata table creation."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.stamp(config, "head")


async def _truncate_all(schema: str | None = None) -> None:
    """Destructive reset of the redirected test schema; never a normal test step.

    A schema aligned through migrations carries guards that refuse
    truncation (``*_no_truncate`` on the immutable evidence tables, 0093–0100).
    The reset is this script's own operation, so those ``BEFORE TRUNCATE``
    triggers are disabled around the ``TRUNCATE`` and re-enabled before the
    commit; the whole reset runs in one transaction, so a failure anywhere on
    the path rolls back both, leaving the guards installed as found.
    """
    from app.core.config import settings
    from app.core.database import async_engine

    schema = schema or settings.DB_SCHEMA
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
            # TRUNCATE-firing user triggers (pg_trigger.tgtype bit 5); system
            # triggers and constraints stay untouched.
            guards = (
                await conn.execute(
                    text(
                        "select c.relname, t.tgname from pg_catalog.pg_trigger t "
                        "join pg_catalog.pg_class c on c.oid = t.tgrelid "
                        "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                        "where n.nspname = :schema and not t.tgisinternal and (t.tgtype & 32) > 0"
                    ),
                    {"schema": schema},
                )
            ).all()
            for table, trigger in guards:
                await conn.execute(text(f'alter table "{schema}"."{table}" disable trigger "{trigger}"'))
            try:
                await conn.execute(text(f"truncate table {', '.join(tables)} restart identity cascade"))
            finally:
                for table, trigger in guards:
                    await conn.execute(text(f'alter table "{schema}"."{table}" enable trigger "{trigger}"'))


async def _seed_reference_rows() -> None:
    """Insert canonical reference rows the suite reads."""
    from app.core.tenant_context import tenant_scope
    from scripts.setup.assign_roles_permissions import assign_roles_permissions
    from scripts.setup.roles import seed_roles_async

    await seed_roles_async()
    with tenant_scope(bypass=True):
        await assign_roles_permissions()

    await _seed_platforms()
    await _seed_bootstrap_tenant()
    await _seed_permissions()
    await _seed_role_grants()
    await _seed_legacy_catalog_owner()


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


def _insert_ignoring_conflicts(table):
    """An insert that leaves existing links alone (the link tables have a key)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    return pg_insert(table).on_conflict_do_nothing()


async def _seed_role_grants() -> None:
    """Give the roles their canonical grants.

    The platform administrator holds every registered permission, and each
    workspace role holds the grants its preset defines. A test schema created
    from the model metadata never runs the migrations, so without this step
    the administrator holds nothing — and every administrator-gated request
    answers 403 for a role that is supposed to pass.
    """
    from app.core.access_catalog import workspace_grants
    from app.core.database import engine
    from app.models import ModelType, Permission, PlatformRole, WorkspaceRole
    from app.models.identity.role import role_permission
    from app.models.tenancy.workspace_role import workspace_role_permission
    from sqlalchemy import select

    with engine.begin() as conn:
        permission_rows = conn.execute(
            select(Permission.id, ModelType.resource_key, ModelType.model_name, Permission.action_type)
            .join(ModelType, Permission.model_type_id == ModelType.id)
        ).all()
        by_resource = {
            (resource_key, str(action).lower()): permission_id
            for permission_id, resource_key, _model_name, action in permission_rows
        }
        # The workspace presets name the model ("source"), the platform
        # catalogue names the resource ("collection.source"); both point at the
        # same permission rows.
        by_model = {
            (model_name, str(action).lower()): permission_id
            for permission_id, _resource_key, model_name, action in permission_rows
        }
        admin_id = conn.execute(
            select(PlatformRole.id).where(PlatformRole.codename == "admin")
        ).scalar_one_or_none()
        if admin_id is not None:
            conn.execute(
                _insert_ignoring_conflicts(role_permission),
                [
                    {"role_id": admin_id, "permission_id": permission_id}
                    for permission_id in sorted(set(by_resource.values()))
                ],
            )
        for role_id, codename in conn.execute(select(WorkspaceRole.id, WorkspaceRole.codename)).all():
            pairs = {
                (model_name, str(action).lower())
                for model_name, action in workspace_grants(codename)
            }
            rows = [
                {"role_id": role_id, "permission_id": by_model[pair]}
                for pair in sorted(pairs)
                if pair in by_model
            ]
            if rows:
                conn.execute(_insert_ignoring_conflicts(workspace_role_permission), rows)


async def _seed_legacy_catalog_owner() -> None:
    """Restore the committed legacy catalog owner after a reset.

    Migration 0109 assigns the personal catalog rows to the active user with
    this stable id and refuses to run unless the user pre-exists, but the
    create_all + stamp head test path never executes that data change. The
    FK-pinned suites insert providers and models owned by this id, so the
    row must be a documented committed fixture of the re-seed. It carries a
    synthetic placeholder hash: no credential, no platform permission, no
    tenant membership.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import PlatformRole, User
    from app.types import PlatformRoleType

    with tenant_scope(bypass=True):
        if await User.objects.get(id=LEGACY_CATALOG_OWNER_ID) is not None:
            return
        role = await PlatformRole.objects.get_by_codename(PlatformRoleType.USER)
        if role is None:
            raise RuntimeError("Legacy catalog owner seeding requires the 'user' platform role row.")
        await User.objects.create(
            id=LEGACY_CATALOG_OWNER_ID,
            username="legacy_catalog_owner",
            email="legacy-catalog-owner@localhost.invalid",
            hashed_password="synthetic-hash-never-a-credential",
            role_id=role.id,
            is_active=True,
        )


def resolve_and_redirect() -> tuple[str, str, str]:
    """Validate isolation before app imports or changing URL/schema variables.

    Return (working_url, test_url, test_schema). Common POSTGRES_URL is allowed
    only with a different test schema. Database-name comparison is deliberately
    conservative; this is not live endpoint/alias identity verification.

    Idempotent on purpose: `tests/conftest.py` and the DB suites' fixtures both
    call this before importing app modules, and the first call has already
    repointed `DB_SCHEMA` at the test schema. Reading that redirected value
    back would make the second call compare the test schema against itself and
    refuse a target the first call had just approved, so the pre-redirect
    values are remembered and reused.
    """
    remembered = _REDIRECTED_WORKING
    test_url = resolve_test_url()
    if not test_url:
        raise RuntimeError(
            f"Neither {TEST_URL_ENV} nor {WORKING_URL_ENV} is set — cannot tell which database "
            f"the test suite may write to. Set {WORKING_URL_ENV} and a separate {TEST_SCHEMA_ENV}; "
            f"{TEST_URL_ENV} is optional."
        )

    working_url = remembered[0] if remembered else os.environ.get(WORKING_URL_ENV, "").strip()
    test_schema = resolve_test_schema()
    working_schema = remembered[1] if remembered else resolve_working_schema()

    same_database = bool(working_url) and database_name(working_url) == database_name(test_url)
    same_schema = test_schema == working_schema
    if same_database and same_schema:
        raise RuntimeError(
            f"{TEST_URL_ENV} and {TEST_SCHEMA_ENV} both resolve to the working target "
            f"({database_name(test_url)!r}, schema {test_schema!r}). The suite would read and write "
            f"the working data — point {TEST_SCHEMA_ENV} at a separate schema."
        )

    _REDIRECTED_WORKING[:] = [working_url, working_schema]
    os.environ[WORKING_URL_ENV] = test_url
    os.environ[TEST_URL_ENV] = test_url
    os.environ[WORKING_SCHEMA_ENV] = test_schema
    return working_url, test_url, test_schema


async def ensure_test_database(test_url: str, *, shares_working_database: bool = False, reset: bool = False) -> None:
    """Ensure redirected test tables/reference rows; reset is explicitly destructive."""
    from app.core.config import settings
    from app.core.database import async_engine
    from app.models import Base

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

    # Check before create_all, reset or reference writes. Read-only preflight is
    # not a writer-exclusion lock: shared-schema coordination is still required.
    async with async_engine.connect() as conn:
        await conn.execute(text("set transaction isolation level repeatable read, read only"))
        await check_existing_schema(conn, Base.metadata, settings.DB_SCHEMA)

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

    # Verify the resulting catalog before seeding; never repair it in bootstrap.
    async with async_engine.connect() as conn:
        await conn.execute(text("set transaction isolation level repeatable read, read only"))
        await check_existing_schema(conn, Base.metadata, settings.DB_SCHEMA, require_all_tables=True)

    await _seed_reference_rows()
    say("reference rows seeded (roles, permissions, platforms, bootstrap tenant, legacy catalog owner)")

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
