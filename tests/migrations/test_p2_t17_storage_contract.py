"""P2-T17 increment A storage DDL contract: offline/mock only, not PostgreSQL acceptance.

These tests load the two Alembic revisions and the ORM tables and compare their
*declared* shape. They prove nothing about real PostgreSQL behaviour - only the
local runner's retained-data cycle, constraint and trigger enforcement do. The
new finalization revision is exercised against an explicit fake connection so
its refusals are observable without a database.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models import (
    EntitlementUsage,
    ServicePlanBackfillResolution,
    ServicePlanPolicy,
    Tenant,
    UserServicePlan,
)
from app.models.identity.user import User

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
TENANCY_MODELS = (ServicePlanPolicy, UserServicePlan, ServicePlanBackfillResolution)


def _load(name):
    spec = importlib.util.spec_from_file_location(f"p2t17_{name}_test", ROOT / f"migrations/versions/p2t17_{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    return _load("service_plan_storage")


def _finalization():
    return _load("service_plan_finalize")


class Operations:
    """Records DDL and replays the storage migration's column additions."""

    def __init__(self, retained=None):
        self.tables = {}
        self.sql = []
        self.drops = []
        self.retained = retained
        self.columns = {}
        self.constraints = []
        self.indexes = []
        self.foreign_keys = []
        self.dialect = postgresql.dialect()

    def create_table(self, name, *elements, schema):
        self.tables[name] = sa.Table(name, sa.MetaData(), *elements, schema=schema)

    def add_column(self, table, column, **kwargs):
        if table == "tenants":
            assert (column.name, column.nullable) == ("plan_holder_user_id", True)
        else:
            assert table == "entitlement_usage"
            assert column.nullable is True
        self.columns.setdefault(table, []).append(column)

    def create_foreign_key(self, name, source, target, columns, referred, **kwargs):
        self.foreign_keys.append((name, source, target, tuple(columns), tuple(referred), kwargs.get("ondelete")))
        assert (name, source, target, columns, referred, kwargs["ondelete"]) == (
            "fk_tenants_plan_holder_user",
            "tenants",
            "users",
            ["plan_holder_user_id"],
            ["id"],
            "RESTRICT",
        )
        assert kwargs["source_schema"] == kwargs["referent_schema"]

    def create_index(self, *args, **kwargs):
        self.indexes.append(args)
        assert args == ("ix_tenants_plan_holder_user_id", "tenants", ["plan_holder_user_id"])

    def create_check_constraint(self, name, table, condition, **kwargs):
        self.constraints.append((name, table, condition))

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


def _script(operations):
    return " ".join(operations.sql)


def _single(sql):
    """Normalised single statement text, for substring checks on DDL."""
    return " ".join(sql.split())


# ── storage migration vs ORM ────────────────────────────────────────────────


def test_migration_and_orm_match_columns_checks_and_restrictive_keys(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    for model in TENANCY_MODELS:
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
    assert "INSERT INTO" not in _script(operations).upper()
    assert "UPDATE OR DELETE" in _script(operations)
    assert "BEFORE TRUNCATE" in _script(operations)
    assert all(model.objects is None for model in TENANCY_MODELS)


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


def test_no_policy_seeds_and_no_commercial_terms(monkeypatch):
    """Absence of a policy is not unlimited: the migration seeds nothing."""
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert "INSERT INTO" not in _script(operations).upper()
    policies = operations.tables["service_plan_policies"]
    assert {"plan_code", "policy_version"} == set(policies.primary_key.columns.keys())
    assert "ck_service_plan_policy_version" in _checks(policies)
    # No price/currency/checkout column: paid amounts stay a checkout snapshot.
    assert not {"price", "price_usd", "currency", "amount"} & set(policies.c.keys())


# ── admission history: holder/version attribution ───────────────────────────


def test_entitlement_usage_holder_columns_are_nullable_paired_with_a_check(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()

    added = {column.name: column for column in operations.columns["entitlement_usage"]}
    assert set(added) == {"plan_holder_user_id", "plan_policy_version"}
    mapped = EntitlementUsage.__table__
    for name, column in added.items():
        assert column.nullable and mapped.c[name].nullable
        assert str(column.type.compile(dialect=operations.dialect)) == str(
            mapped.c[name].type.compile(dialect=operations.dialect)
        )

    check = dict((name, condition) for name, table, condition in operations.constraints)
    assert check["ck_entitlement_usage_holder_policy"] == _checks(mapped)["ck_entitlement_usage_holder_policy"]
    assert check["ck_entitlement_usage_holder_policy"].startswith(
        "(plan_holder_user_id IS NULL AND plan_policy_version IS NULL) OR"
    )

    # Historical attribution stays scalar evidence: pruning a user or workspace
    # must not erase it, so neither new column may carry a foreign key, and the
    # migration must not add one either (it only creates the tenants holder FK).
    for name in ("plan_holder_user_id", "plan_policy_version"):
        assert mapped.c[name].foreign_keys == set()
    assert [entry[1] for entry in operations.foreign_keys] == ["tenants"]


def test_historical_backfill_evidence_has_no_tenant_or_user_foreign_keys():
    table = ServicePlanBackfillResolution.__table__
    for name in ("tenant_id", "holder_user_id", "operator_user_id", "owner_user_ids"):
        assert table.c[name].foreign_keys == set(), name
    # The selected policy is the only FK, and it must refuse deletion.
    assert {fk.ondelete for fk in table.foreign_key_constraints} == {"RESTRICT"}
    assert all(tuple(fk.column_keys) == ("plan_code", "policy_version") for fk in table.foreign_key_constraints)


def test_backfill_evidence_migration_table_matches_the_orm(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    created = operations.tables["service_plan_backfill_resolutions"]
    mapped = ServicePlanBackfillResolution.__table__
    assert {fk.column_keys[0] for fk in created.foreign_key_constraints} == {"plan_code"}
    assert {fk.column_keys[0] for fk in mapped.foreign_key_constraints} == {"plan_code"}


# ── immutable policy/evidence triggers ──────────────────────────────────────


def test_immutable_triggers_cover_update_delete_and_truncate_on_both_tables(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    statements = [_single(sql) for sql in operations.sql]

    function = next(sql for sql in statements if "CREATE FUNCTION" in sql)
    assert "service_plan_policy_immutable" in function
    assert "RAISE EXCEPTION" in function

    expected = {
        ("service_plan_policies", "service_plan_policy_immutable", "UPDATE OR DELETE", "ROW"),
        ("service_plan_policies", "service_plan_policy_no_truncate", "TRUNCATE", "STATEMENT"),
        ("service_plan_backfill_resolutions", "service_plan_backfill_immutable", "UPDATE OR DELETE", "ROW"),
        ("service_plan_backfill_resolutions", "service_plan_backfill_no_truncate", "TRUNCATE", "STATEMENT"),
    }
    found = set()
    for sql in statements:
        if "CREATE TRIGGER" not in sql:
            continue
        for table, trigger, event, level in expected:
            if f"CREATE TRIGGER {trigger} BEFORE {event} ON" in sql:
                assert f".{table} FOR EACH {level}" in sql
                assert "EXECUTE FUNCTION" in sql
                found.add((table, trigger, event, level))
    assert found == expected


def test_storage_migration_never_writes_rows_or_runs_unquoted_sql(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    script = _script(operations).upper()
    for mutation in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE"):
        assert mutation not in script
    assert all("$$" in sql for sql in operations.sql if "CREATE" in sql)


# ── downgrade: empty-only, refuses evidence ─────────────────────────────────


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


@pytest.mark.parametrize("retained", ["plan_holder_user_id IS NOT NULL"])
def test_admission_policy_evidence_refuses_downgrade(monkeypatch, retained):
    """A non-empty `entitlement_usage` with holder attribution is liability evidence."""
    migration, operations = _migration(), Operations(retained)
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: False))
    with pytest.raises(RuntimeError, match="admission policy evidence"):
        migration.downgrade()
    assert operations.drops == []


def test_sponsorship_alone_refuses_downgrade(monkeypatch):
    migration, operations = _migration(), Operations("tenants WHERE plan_holder_user_id IS NOT NULL")
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: False))
    with pytest.raises(RuntimeError, match="sponsorship must be preserved"):
        migration.downgrade()
    assert operations.drops == []
    assert not any(sql.startswith("DROP") for sql in operations.sql)


def test_downgrade_checks_all_evidence_before_dropping_anything(monkeypatch):
    migration, operations = _migration(), Operations()
    monkeypatch.setattr(migration, "op", operations)
    monkeypatch.setattr(migration, "context", SimpleNamespace(is_offline_mode=lambda: False))
    migration.downgrade()
    lock = next(index for index, sql in enumerate(operations.sql) if "ACCESS EXCLUSIVE MODE" in sql)
    probes = [sql for sql in operations.sql if sql.startswith("SELECT EXISTS")]
    assert operations.sql.index(probes[0]) > lock
    assert len(probes) == 5
    assert operations.drops == ["service_plan_backfill_resolutions", "user_service_plans", "service_plan_policies"]
    assert "DROP FUNCTION" in operations.sql[-1]


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


# ── finalization revision (p2t17_service_plan_finalize) ─────────────────────


class FinalizationOps:
    """Fake Alembic op bound to one recorded connection."""

    def __init__(self, connection):
        self.connection = connection

    def get_bind(self):
        return self.connection

    def alter_column(self, table, column, **kwargs):
        self.connection.alterations.append((table, column, kwargs))


class FinalizationConnection:
    def __init__(self, unresolved=0, fail_on_lock=False):
        self.unresolved = unresolved
        self.fail_on_lock = fail_on_lock
        self.sql = []
        self.alterations = []
        self.dialect = postgresql.dialect()

    def execute(self, statement):
        text = str(statement)
        if "ACCESS EXCLUSIVE MODE" in text and self.fail_on_lock:
            raise RuntimeError("lock unavailable")
        self.sql.append(text)

    def scalar(self, statement):
        self.sql.append(str(statement))
        return self.unresolved


def _finalize(connection, offline=False):
    module = _finalization()
    module.op = FinalizationOps(connection)
    module.context = SimpleNamespace(is_offline_mode=lambda: offline)
    return module


def test_finalization_chains_after_the_storage_revision():
    assert _finalization().down_revision == _migration().revision
    assert _finalization().revision and _finalization().revision != _finalization().down_revision


def test_finalization_refuses_offline_mode_before_any_statement():
    connection = FinalizationConnection()
    with pytest.raises(RuntimeError, match="offline"):
        _finalize(connection, offline=True).upgrade()
    assert connection.sql == []
    assert connection.alterations == []


@pytest.mark.parametrize("unresolved", [1, 5])
def test_finalization_refuses_unresolved_sponsorship_or_missing_evidence(unresolved):
    connection = FinalizationConnection(unresolved=unresolved)
    with pytest.raises(RuntimeError, match="unresolved sponsorship or missing backfill evidence"):
        _finalize(connection).upgrade()
    assert connection.alterations == []


def test_finalization_verifies_holder_plan_and_matching_backfill_evidence():
    connection = FinalizationConnection(unresolved=0)
    _finalize(connection).upgrade()
    resolution_query = next(sql for sql in connection.sql if sql.strip().startswith("SELECT count(*)"))
    normalised = " ".join(resolution_query.split())
    assert "LEFT JOIN" in normalised
    assert "user_service_plans p ON p.user_id = t.plan_holder_user_id" in normalised
    assert "service_plan_backfill_resolutions r ON r.tenant_id = t.id" in normalised
    assert "t.plan_holder_user_id IS NULL OR p.user_id IS NULL" in normalised
    assert "r.tenant_id IS NULL OR r.holder_user_id <> t.plan_holder_user_id" in normalised
    assert connection.alterations == [
        ("tenants", "plan_holder_user_id", {"existing_type": sa.Integer, "nullable": False})
    ]


def test_finalization_does_not_ddl_when_locking_the_tenants_table_fails():
    connection = FinalizationConnection(unresolved=0, fail_on_lock=True)
    with pytest.raises(RuntimeError, match="lock unavailable"):
        _finalize(connection).upgrade()
    assert connection.alterations == []


def test_finalization_downgrade_is_nullable_again():
    connection = FinalizationConnection()
    _finalize(connection).downgrade()
    assert connection.alterations == [
        ("tenants", "plan_holder_user_id", {"existing_type": sa.Integer, "nullable": True})
    ]


def test_finalization_never_assigns_holders_or_plans_itself():
    """No first/highest/current-actor inference: the revision only verifies and relaxes/raises."""
    connection = FinalizationConnection(unresolved=0)
    _finalize(connection).upgrade()
    script = " ".join(connection.sql).upper()
    for mutation in ("UPDATE ", "INSERT INTO", "DELETE FROM", "CREATE TABLE", "ALTER COLUMN PLAN"):
        assert mutation not in script


# ── ORM-side anchor for the finalization contract ───────────────────────────


def test_orm_holder_column_still_nullable_until_finalization_maps_it_required():
    """Finalization raises nullability in the database; the ORM stays nullable on purpose.

    The dormant ORM ships to environments before the operator finalizes, so a
    mapped `nullable=False` would contradict the still-nullable interim schema.
    """
    assert Tenant.__table__.c.plan_holder_user_id.nullable
    assert Tenant.__table__.c.plan_holder_user_id.foreign_keys
    assert next(iter(Tenant.__table__.c.plan_holder_user_id.foreign_keys)).target_fullname == "public.users.id"
    assert User.__tablename__ == "users"
    assert UserServicePlan.__table__.c.plan_expires_at.type.timezone
    assert UserServicePlan.__table__.c.plan_expires_at.nullable
    assert ServicePlanBackfillResolution.__table__.c.resolved_at.type.timezone
    assert ServicePlanBackfillResolution.__table__.c.resolved_at.nullable is False
    assert NOW.utcoffset() is not None
