"""Tests for the test-database bootstrap (``scripts.setup_test_db``).

The drift check compares the model metadata against what actually exists in the
schema, because ``create_all(checkfirst=True)`` adds missing *tables* but never
missing *columns* — a schema that predates a model change silently breaks every
test that writes the new column with a bare ``column "x" does not exist``.
"""

from __future__ import annotations

import secrets

from sqlalchemy import Column, Integer, MetaData, Table, Text, text

from app.core.database import async_engine
from scripts.setup_test_db import _missing_columns, _nullability_drift


def _schema_name() -> str:
    return f"drift_check_{secrets.token_hex(3)}"


async def test_missing_columns_reports_dropped_but_not_present():
    schema = _schema_name()
    metadata = MetaData()
    Table("items", metadata, Column("id", Integer, primary_key=True), Column("name", Text), schema=schema)

    async with async_engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text(f'create schema "{schema}"'))
            await conn.execute(text(f'create table "{schema}"."items" (id integer primary key, name text)'))

            # A schema that matches the metadata reports nothing missing...
            assert await _missing_columns(conn, metadata, schema) == []

            # ...and a column the model has but the schema lost is named exactly.
            await conn.execute(text(f'alter table "{schema}"."items" drop column name'))
            assert await _missing_columns(conn, metadata, schema) == ["items.name"]
        finally:
            await trans.rollback()


async def test_nullability_drift_names_both_directions_and_honours_the_recorded_contract():
    """A migrated schema is the authority; the metadata stays the model's view."""
    schema = _schema_name()
    metadata = MetaData()
    Table(
        "tenants",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("plan_holder_user_id", Integer, nullable=True),
        Column("name", Text, nullable=False),
        schema=schema,
    )

    async with async_engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text(f'create schema "{schema}"'))
            await conn.execute(
                text(
                    f'create table "{schema}"."tenants" '
                    "(id integer primary key, plan_holder_user_id integer not null, name text not null)"
                )
            )
            looser, unrecorded = await _nullability_drift(conn, metadata, schema, existing_tables={"tenants"})
            # ...the live schema requires a plan holder the model leaves optional,
            # and 0155 owns that difference, so it is recorded rather than refused...
            assert (looser, unrecorded) == ([], [])

            # ...an unrecorded stricter column is refused by name...
            await conn.execute(text(f'alter table "{schema}"."tenants" alter column name drop not null'))
            await conn.execute(text(f'alter table "{schema}"."tenants" alter column plan_holder_user_id drop not null'))
            looser, unrecorded = await _nullability_drift(conn, metadata, schema, existing_tables={"tenants"})
            assert looser == ["tenants.name"]
            assert unrecorded == []

            # ...and a stricter column nobody recorded is named, not silently accepted...
            await conn.execute(text(f'alter table "{schema}"."tenants" add column extra integer not null default 0'))
            extra_metadata = MetaData()
            Table(
                "tenants",
                extra_metadata,
                Column("id", Integer, primary_key=True),
                Column("plan_holder_user_id", Integer, nullable=True),
                Column("name", Text, nullable=True),
                Column("extra", Integer, nullable=True),
                schema=schema,
            )
            looser, unrecorded = await _nullability_drift(conn, extra_metadata, schema, existing_tables={"tenants"})
            assert looser == []
            assert unrecorded == ["tenants.extra"]
        finally:
            await trans.rollback()


def test_capability_registry_matches_the_catalog_and_the_migration():
    """A re-seed restores history's rows, and the catalog still owns their grants."""
    from app.core.access_catalog import RESOURCES
    from scripts.setup_test_db import _capability_registry

    registry = _capability_registry()
    keys = {key for key, _model, _table, _actions in registry}
    assert keys == {"onec.data", "onec.command"}
    assert keys <= {resource.key for resource in RESOURCES}


async def test_capability_rows_and_permissions_come_back_after_a_reset():
    """`--reset` truncates model_types; the re-seed must restore 0156's rows."""
    from sqlalchemy import create_engine, select
    from sqlalchemy import text as sql_text

    from app.models import ModelType, Permission
    from scripts.ci.schema_inventory import libpq_dsn
    from scripts.setup_test_db import _seed_capability_resources

    engine = create_engine(
        libpq_dsn(async_engine.url.render_as_string(hide_password=False)).replace(
            "postgresql://", "postgresql+psycopg2://", 1
        )
    )
    schema = ModelType.__table__.schema
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # Schema-qualified: an unqualified statement would target `public`.
            # Grant links go first, exactly like the migration's own downgrade.
            for link in ("role_permission", "workspace_role_permission"):
                conn.execute(
                    sql_text(
                        f'delete from "{schema}".{link} l using "{schema}".permissions p, "{schema}".model_types m '
                        "where l.permission_id = p.id and p.model_type_id = m.id and m.resource_key like 'onec.%'"
                    )
                )
            conn.execute(
                sql_text(
                    f'delete from "{schema}".permissions p using "{schema}".model_types m '
                    "where p.model_type_id = m.id and m.resource_key like 'onec.%'"
                )
            )
            conn.execute(sql_text(f"delete from \"{schema}\".model_types where resource_key like 'onec.%'"))
            assert (
                conn.execute(select(ModelType.id).where(ModelType.resource_key == "onec.data")).scalar_one_or_none()
                is None
            )

            _seed_capability_resources(conn)

            keys = set(
                conn.execute(select(ModelType.resource_key).where(ModelType.resource_key.like("onec.%"))).scalars()
            )
            assert keys == {"onec.data", "onec.command"}
            actions = set(
                conn.execute(
                    select(Permission.action_type)
                    .join(ModelType, ModelType.id == Permission.model_type_id)
                    .where(ModelType.resource_key == "onec.data")
                ).scalars()
            )
            assert {getattr(action, "db_value", action) for action in actions} == {"view", "export"}
        finally:
            trans.rollback()
