"""Real PostgreSQL migration/rollback regressions; root test-schema guards apply."""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.config import settings
from app.core.database import engine


@pytest.fixture
def migration():
    assert settings.DB_SCHEMA == "test_schema"
    spec = importlib.util.spec_from_file_location(
        "q16_migration", Path("migrations/versions/0120_shared_platform_config.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text("SELECT pg_advisory_xact_lock(160120)"))
            module.op = Operations(MigrationContext.configure(connection))
            module.op.drop_constraint("uq_platforms_type", "platforms", schema=settings.DB_SCHEMA, type_="unique")
            table = sa.Table("platforms", sa.MetaData(), schema=settings.DB_SCHEMA, autoload_with=connection)
            # Fresh owned schema has only reference platforms; never erase another test's rows.
            assert connection.scalar(sa.text("SELECT count(*) FROM test_schema.sources")) == 0
            connection.execute(table.delete())
            yield module, connection, table
        finally:
            transaction.rollback()


def seed(connection, table, *, conflict=False):
    rows = [
        dict(
            id=-101,
            name="VK first",
            platform_type="vk",
            base_url="https://vk.com",
            is_active=True,
            params={"api_version": "custom", "nested": {"first": 1}},
        ),
        dict(
            id=-100,
            name="VK second",
            platform_type="vk",
            base_url="https://vk.com",
            is_active=True,
            params={
                "custom": "preserved",
                "nested": {"second": 2},
                **({"api_version": "conflict"} if conflict else {}),
            },
        ),
        dict(id=-99, name="Telegram", platform_type="telegram", base_url="https://t.me", is_active=True, params={}),
    ]
    connection.execute(table.insert(), rows)
    return rows


def select_rows(connection, table):
    return [dict(r) for r in connection.execute(sa.select(table).order_by(table.c.id)).mappings()]


def test_upgrade_preserves_custom_values_references_and_exact_downgrade(migration):
    module, connection, table = migration
    original = seed(connection, table)
    sources = sa.Table("sources", sa.MetaData(), schema=settings.DB_SCHEMA, autoload_with=connection)
    tenant = connection.scalar(sa.text("SELECT id FROM test_schema.tenants ORDER BY id LIMIT 1"))
    connection.execute(
        sources.insert().values(
            id=-16120,
            tenant_id=tenant,
            platform_id=-100,
            name="synthetic Q16",
            external_id="q16",
            source_type="GROUP",
            params={},
            is_active=True,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    module.upgrade()
    rows = select_rows(connection, table)
    assert len(rows) == 2
    vk = rows[0]
    assert vk["id"] == -101 and vk["params"]["api_version"] == "custom"
    assert vk["params"]["custom"] == "preserved"
    assert vk["params"]["nested"] == {"first": 1, "second": 2}
    assert connection.scalar(sa.select(sources.c.platform_id).where(sources.c.id == -16120)) == -101
    assert len(vk["params"][module.ARCHIVE]["original"]) == 3
    with connection.begin_nested() as savepoint:
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                table.insert().values(id=-98, name="duplicate", platform_type="vk", base_url="x", params={})
            )
        savepoint.rollback()
    module.downgrade()
    assert select_rows(connection, table) == original
    assert connection.scalar(sa.select(sources.c.platform_id).where(sources.c.id == -16120)) == -100
    module.upgrade()
    assert len(select_rows(connection, table)) == 2


def test_conflicting_configuration_aborts_before_changes(migration):
    module, connection, table = migration
    original = seed(connection, table, conflict=True)
    with pytest.raises(ValueError, match="platform_config_conflict") as error:
        module.upgrade()
    assert "custom" not in str(error.value) and 'conflict"' not in str(error.value)
    assert select_rows(connection, table) == original


def test_downgrade_refuses_to_overwrite_new_configuration(migration):
    module, connection, table = migration
    seed(connection, table)
    module.upgrade()
    row = select_rows(connection, table)[0]
    changed = deepcopy(row["params"])
    changed["api_version"] = "newer"
    connection.execute(table.update().where(table.c.id == row["id"]).values(params=changed))
    before = select_rows(connection, table)
    with pytest.raises(ValueError, match="platform_rollback_changed_data"):
        module.downgrade()
    assert select_rows(connection, table) == before


def test_source_collision_is_not_silently_merged(migration):
    module, connection, table = migration
    original = seed(connection, table)
    sources = sa.Table("sources", sa.MetaData(), schema=settings.DB_SCHEMA, autoload_with=connection)
    tenant = connection.scalar(sa.text("SELECT id FROM test_schema.tenants ORDER BY id LIMIT 1"))
    for index, platform_id in enumerate([-101, -100]):
        connection.execute(
            sources.insert().values(
                id=-16200 - index,
                tenant_id=tenant,
                platform_id=platform_id,
                name="synthetic Q16",
                external_id="same",
                source_type="GROUP",
                params={},
                is_active=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with pytest.raises(ValueError, match="platform_source_identity_conflict"):
        module.upgrade()
    assert select_rows(connection, table) == original


def test_unknown_platform_reference_fails_without_cascade(migration):
    module, connection, table = migration
    original = seed(connection, table)
    module.op.create_table(
        "q16_unknown_reference",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform_id", sa.Integer(), sa.ForeignKey("test_schema.platforms.id")),
        schema="test_schema",
    )
    with pytest.raises(ValueError, match="platform_unknown_reference"):
        module.upgrade()
    assert select_rows(connection, table) == original


def test_mutable_json_edits_are_persisted_by_the_orm(migration):
    from app.models import Platform

    module, connection, table = migration
    seed(connection, table)
    module.upgrade()
    with sa.orm.Session(bind=connection) as session:
        platform = session.get(Platform, -101)
        platform.params["api_version"] = "persisted-change"
        session.flush()
        assert select_rows(connection, table)[0]["params"]["api_version"] == "persisted-change"


def test_construct_then_assign_creation_materializes_json_defaults(migration):
    from app.models import Platform
    from app.types import PlatformType

    _, connection, _ = migration
    with sa.orm.Session(bind=connection) as session:
        platform = Platform()
        platform.id, platform.name = -16300, "SQLAdmin-style Q16"
        platform.platform_type, platform.base_url = PlatformType.VK, "https://vk.com"
        platform.params = {"custom": "keep"}
        session.add(platform)
        session.flush()
        assert platform.params["api_version"] == "5.199"
        assert platform.params["custom"] == "keep"
