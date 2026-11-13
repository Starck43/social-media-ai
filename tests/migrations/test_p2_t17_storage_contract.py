"""Offline/mock DDL contract only, not PostgreSQL migration acceptance."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models import ServicePlanBackfillResolution, ServicePlanPolicy, Tenant, UserServicePlan

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def _migration():
    spec = importlib.util.spec_from_file_location(
        "p2t17_storage_test", ROOT / "migrations/versions/p2t17_service_plan_storage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Operations:
    def __init__(self, retained=None):
        self.tables = {}
        self.sql = []
        self.drops = []
        self.retained = retained
        self.dialect = postgresql.dialect()

    def create_table(self, name, *elements, schema):
        self.tables[name] = sa.Table(name, sa.MetaData(), *elements, schema=schema)

    def add_column(self, table, column, **kwargs):
        assert (table, column.name, column.nullable) == ("tenants", "plan_holder_user_id", True)

    def create_foreign_key(self, name, source, target, columns, referred, **kwargs):
        assert (name, source, target, columns, referred, kwargs["ondelete"]) == (
            "fk_tenants_plan_holder_user",
            "tenants",
            "users",
            ["plan_holder_user_id"],
            ["id"],
            "RESTRICT",
        )

    def create_index(self, *args, **kwargs):
        assert args == ("ix_tenants_plan_holder_user_id", "tenants", ["plan_holder_user_id"])

    def get_context(self):
        return SimpleNamespace(dialect=self.dialect)

    def get_bind(self):
        return self

    def execute(self, sql):
        self.sql.append(str(sql))

    def scalar(self, sql):
        self.sql.append(str(sql))
        return self.retained is not None and self.retained in str(sql)

    def drop_table(self, name, **kwargs):
        self.drops.append(name)

    def drop_index(self, *args, **kwargs):
        pass

    def drop_constraint(self, *args, **kwargs):
        pass

    def drop_column(self, *args, **kwargs):
        pass


def _checks(table):
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _fks(table):
    return {
        (tuple(fk.column_keys), tuple(element.target_fullname for element in fk.elements), fk.ondelete)
        for fk in table.foreign_key_constraints
    }


def test_migration_and_orm_match_columns_checks_and_restrictive_keys(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    for model in (ServicePlanPolicy, UserServicePlan, ServicePlanBackfillResolution):
        created, mapped = operations.tables[model.__tablename__], model.__table__
        assert set(created.c.keys()) == set(mapped.c.keys())
        for name in mapped.c.keys():
            assert created.c[name].nullable == mapped.c[name].nullable
            assert str(created.c[name].type.compile(dialect=operations.dialect)) == str(
                mapped.c[name].type.compile(dialect=operations.dialect)
            )
        assert tuple(created.primary_key.columns.keys()) == tuple(mapped.primary_key.columns.keys())
        assert _checks(created) == _checks(mapped)
        assert _fks(created) == _fks(mapped)
    assert "INSERT INTO" not in " ".join(operations.sql).upper()
    assert "UPDATE OR DELETE" in " ".join(operations.sql)
    assert "BEFORE TRUNCATE" in " ".join(operations.sql)
    assert all(model.objects is None for model in (ServicePlanPolicy, UserServicePlan, ServicePlanBackfillResolution))


def test_policies_cannot_confuse_unconfigured_with_explicit_unlimited():
    table = ServicePlanPolicy.__table__
    assert isinstance(table.c.token_limit.type, sa.BigInteger)
    assert isinstance(table.c.max_owned_workspaces.type, sa.BigInteger)
    assert table.c.token_limit.nullable
    assert str(table.c.token_policy_configured.server_default.arg) == "false"
    assert str(table.c.owned_workspace_limit_configured.server_default.arg) == "false"
    assert "NOT token_policy_configured AND token_unit IS NULL" in _checks(table)["ck_service_plan_policy_tokens"]
    assert (
        "owned_workspace_limit_configured OR max_owned_workspaces IS NULL"
        in _checks(table)["ck_service_plan_policy_workspaces"]
    )
    assert UserServicePlan.__table__.primary_key.columns.keys() == ["user_id"]
    assert "tenant_id" not in UserServicePlan.__table__.c
    assert next(iter(Tenant.__table__.c.plan_holder_user_id.foreign_keys)).ondelete == "RESTRICT"


@pytest.mark.parametrize(
    "retained", ["service_plan_policies", "user_service_plans", "service_plan_backfill_resolutions", "tenants"]
)
def test_retained_data_or_sponsorship_refuses_downgrade_before_any_drop(monkeypatch, retained):
    migration, operations = _migration(), Operations(retained)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: False))
    with pytest.raises(RuntimeError, match="preserved"):
        migration.downgrade()
    assert operations.drops == []
    assert "ACCESS EXCLUSIVE MODE" in operations.sql[0]


def test_empty_downgrade_removes_only_target_storage(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: False))
    migration.downgrade()
    assert operations.drops == ["service_plan_backfill_resolutions", "user_service_plans", "service_plan_policies"]
    assert "DROP FUNCTION" in operations.sql[-1]


def test_offline_downgrade_is_refused_without_attempting_ddl(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: True))
    with pytest.raises(RuntimeError, match="offline"):
        migration.downgrade()
    assert operations.sql == []
