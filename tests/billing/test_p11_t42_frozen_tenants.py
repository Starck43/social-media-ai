"""No-DB parity and tamper checks for the owner-approved frozen budget fixture."""

import ast
import importlib.util
import subprocess
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, declarative_base, declared_attr, mapped_column
from sqlalchemy.schema import CreateIndex, CreateTable

ROOT = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preparation = load("scripts/prepare_llm_budget_fixture.py", "_p11t42_preparation")
runner = load("tests/acceptance/test_llm_budget_pg.py", "_p11t42_runner")
frozen = load(preparation.FROZEN_TENANTS_PATH, "_p11t42_frozen")


def historical_table(schema):
    """Evaluate only historical column/table declarations in an isolated ORM base.

    App imports, manager registration, current settings and model methods are
    excluded. Both sources come from the immutable reviewed Git revision.
    """

    def source(path):
        return ast.parse(
            subprocess.check_output(["git", "-C", str(ROOT), "show", f"{frozen.SOURCE_REVISION}:{path}"]).decode()
        )

    tenant = next(
        n for n in source("app/models/tenancy/tenant.py").body if isinstance(n, ast.ClassDef) and n.name == "Tenant"
    )
    mixin = next(
        n for n in source("app/models/base.py").body if isinstance(n, ast.ClassDef) and n.name == "TimestampMixin"
    )
    tenant.decorator_list = []
    tenant.body = [
        n
        for n in tenant.body
        if (
            isinstance(n, ast.AnnAssign)
            and (
                getattr(n.target, "id", None) == "DEFAULT_PLAN"
                or (isinstance(n.value, ast.Call) and getattr(n.value.func, "id", None) == "Column")
            )
        )
        or (
            isinstance(n, ast.Assign)
            and any(getattr(t, "id", None) in {"__tablename__", "__table_args__"} for t in n.targets)
        )
    ]
    namespace = {
        name: getattr(sa, name)
        for name in (
            "JSON",
            "Boolean",
            "CheckConstraint",
            "Column",
            "Float",
            "Index",
            "Integer",
            "String",
            "Text",
            "UniqueConstraint",
            "DateTime",
            "func",
        )
    }
    namespace.update(
        Base=declarative_base(metadata=sa.MetaData(schema=schema)),
        Mapped=Mapped,
        mapped_column=mapped_column,
        declared_attr=declared_attr,
        datetime=datetime,
        Any=Any,
        ClassVar=ClassVar,
        settings=SimpleNamespace(DB_SCHEMA=schema),
    )
    exec(compile(ast.Module(body=[mixin, tenant], type_ignores=[]), "historical-tenant-ddl", "exec"), namespace)
    return namespace["Tenant"].__table__


def ddl(table):
    dialect = postgresql.dialect()
    return (
        str(CreateTable(table).compile(dialect=dialect)),
        sorted(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes),
    )


def default_value(value):
    return None if value is None else str(value.arg)


class FrozenTenantTests(unittest.TestCase):
    def test_postgresql_table_and_indexes_match_reviewed_historical_model(self):
        self.assertEqual(ddl(frozen.tenant_table("budget_parity")), ddl(historical_table("budget_parity")))

    def test_column_metadata_and_python_defaults_match(self):
        historical = historical_table("budget_parity")
        actual = frozen.tenant_table("budget_parity")
        self.assertEqual(list(actual.c.keys()), list(historical.c.keys()))
        for column in actual.c:
            with self.subTest(column=column.name):
                old = historical.c[column.name]
                self.assertEqual(
                    (
                        column.nullable,
                        column.comment,
                        default_value(column.default),
                        default_value(column.server_default),
                        default_value(column.onupdate),
                    ),
                    (
                        old.nullable,
                        old.comment,
                        default_value(old.default),
                        default_value(old.server_default),
                        default_value(old.onupdate),
                    ),
                )

    def test_frozen_table_has_no_current_model_dependencies(self):
        table = preparation.frozen_tenant_table()
        self.assertEqual(table.schema, preparation.TARGET_SCHEMA)
        self.assertEqual(set(table.metadata.tables), {f"{preparation.TARGET_SCHEMA}.tenants"})
        self.assertFalse(table.foreign_keys)
        self.assertFalse({"plan_holder_user_id", "agent_model_id"} & set(table.c.keys()))
        tree = ast.parse((ROOT / preparation.FROZEN_TENANTS_PATH).read_text())
        self.assertFalse(
            any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app") for n in ast.walk(tree))
        )

    def test_source_manifest_pins_frozen_definition_instead_of_live_models(self):
        paths = set(preparation.source_manifest())
        self.assertIn(preparation.FROZEN_TENANTS_PATH, paths)
        self.assertFalse({"app/models/tenancy/tenant.py", "app/models/base.py"} & paths)
        self.assertEqual(len(preparation.DEFINED_RELATIONS), 5)

    def test_modified_frozen_definition_fails_byte_pin(self):
        original = preparation.git_bytes

        def tamper(*args):
            if args == ("show", f"{runner.PREPARATION_PIN}:{preparation.FROZEN_TENANTS_PATH}"):
                return b"modified historical definition"
            return original(*args)

        with patch.object(preparation, "git_bytes", side_effect=tamper):
            with self.assertRaisesRegex(preparation.VerificationFailure, "definition-content-drift"):
                preparation.verify_script_pin(runner.PREPARATION_PIN)

    def test_modified_preparation_still_fails_byte_pin(self):
        original = preparation.git_bytes

        def tamper(*args):
            if args == ("show", f"{runner.PREPARATION_PIN}:{preparation.SCRIPT_PATH}"):
                return b"modified preparation"
            return original(*args)

        with patch.object(preparation, "git_bytes", side_effect=tamper):
            with self.assertRaisesRegex(preparation.VerificationFailure, "preparation-content-drift"):
                preparation.verify_script_pin(runner.PREPARATION_PIN)


if __name__ == "__main__":
    unittest.main()
