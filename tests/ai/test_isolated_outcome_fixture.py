"""Pure routing and preparation-report regression checks; no DB or private settings."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
RESOLVERS = SimpleNamespace(
    resolve_test_url=lambda: None,
    resolve_test_schema=lambda: "test_schema",
    resolve_working_schema=lambda: "public",
)
F = load_isolated_source(
    "_isolated_fixture_test",
    ROOT / "scripts/isolated_outcome_fixture.py",
    imports={"scripts.setup_test_db": RESOLVERS},
)
V = load_isolated_source("_isolated_verifier_test", ROOT / "scripts/verify_llm_outcome_events.py")
WORKING = "postgresql://shared@localhost:5432/social_manager"
TEST = "postgresql://smai_pr168_test@localhost:5432/social_manager_pr168_evidence"
HEAD = "a" * 40


def report():
    return [
        {"phase": "intake", "head": HEAD, "target": list(F.TARGET)},
        {"phase": "preflight", "fixture_schema": "absent", "public_revision": None, "target": list(F.TARGET)},
        {"phase": "rehearsal", "status": "PASS", "rolled_back": True},
        {"phase": "application", "status": "COMMITTED", "definition_only": True},
        {
            "phase": "result",
            "status": "FIXTURE_PASS",
            "target": list(F.TARGET),
            "provenance": "isolated_synthetic_fixture",
            "public_revision": None,
            "outcome_rows": 0,
            "fixture_matches_rehearsal": True,
            "whole_gate_accepted": False,
            "synthetic_and_mutation_probes_run": False,
            "catalog": dict(relations=[], constraints=[], indexes=[], functions=[], triggers=[]),
        },
    ]


def accept(rows, isolated=True):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in rows))
        return V.application_evidence(path, HEAD, isolated=isolated)


class Routing(unittest.TestCase):
    def test_explicit_dedicated_url_accepts_without_app_import(self):
        self.assertEqual(F.validate_configuration(WORKING, "public", TEST, "test_schema"), TEST)

    def test_missing_explicit_test_url_never_falls_back(self):
        for url in ("", WORKING):
            with self.subTest(url=url), self.assertRaises(F.receipts.VerificationFailure):
                F.validate_configuration(WORKING, "public", url, "test_schema")

    def test_wrong_role_database_schema_host_port_reject(self):
        for url, schema in [
            (TEST.replace("smai_pr168_test", "starck"), "test_schema"),
            (TEST.replace("social_manager_pr168_evidence", "social_manager"), "test_schema"),
            (TEST, "public"),
            (TEST.replace("localhost", "remote"), "test_schema"),
            (TEST.replace("5432", "5433"), "test_schema"),
            (TEST + "?options=search_path%3Dpublic", "test_schema"),
            (TEST + "#fragment", "test_schema"),
            (TEST.replace("postgresql:", "http:"), "test_schema"),
        ]:
            with self.subTest(url=url, schema=schema), self.assertRaises(F.receipts.VerificationFailure):
                F.validate_configuration(WORKING, "public", url, schema)

    def test_wrong_working_origin_rejects(self):
        for url, schema in [(WORKING.replace("social_manager", "other"), "public"), (WORKING, "test_schema")]:
            with self.subTest(url=url), self.assertRaises(F.receipts.VerificationFailure):
                F.validate_configuration(url, schema, TEST, "test_schema")

    def test_resolve_requires_explicit_env_and_redirects_only_after_validation(self):
        env = {
            "POSTGRES_URL": WORKING,
            "TEST_POSTGRES_URL": TEST,
            "DB_SCHEMA": "public",
            "DB_TEST_SCHEMA": "test_schema",
        }
        with patch.dict(F.os.environ, env, clear=True):
            self.assertEqual(F.resolve_target(), (TEST, "test_schema"))
            self.assertEqual(F.os.environ["POSTGRES_URL"], TEST)
            self.assertEqual(F.os.environ["DB_SCHEMA"], "test_schema")
            self.assertEqual(F.os.environ["DEBUG"], "false")


class PreparationReport(unittest.TestCase):
    def test_isolated_report_accepts_absent_version_not_fake_shared_revision(self):
        self.assertEqual(set(accept(report())), {"relations", "constraints", "indexes", "functions", "triggers"})

    def test_isolated_report_cannot_authorize_shared_mode(self):
        with self.assertRaises(V.VerificationFailure):
            accept(report(), isolated=False)

    def test_wrong_profile_or_provenance_rejects(self):
        for field, value in [
            ("target", ["social_manager", "test_schema", "starck"]),
            ("provenance", "production_migration"),
            ("public_revision", "0092"),
            ("status", "APPLICATION_PASS"),
        ]:
            rows = report()
            rows[-1][field] = value
            with self.subTest(field=field), self.assertRaises(V.VerificationFailure):
                accept(rows)

    def test_missing_explicit_absent_version_rejects(self):
        rows = report()
        del rows[-1]["public_revision"]
        with self.assertRaises(V.VerificationFailure):
            accept(rows)

    def test_intake_target_and_final_target_must_agree(self):
        rows = report()
        rows[0]["target"][0] = "social_manager"
        with self.assertRaises(V.VerificationFailure):
            accept(rows)

    def test_commit_rollback_and_scope_fail_closed(self):
        for index, field, value in [
            (2, "rolled_back", False),
            (3, "status", "APPLICATION_OUTCOME_UNCERTAIN"),
            (-1, "outcome_rows", True),
            (-1, "synthetic_and_mutation_probes_run", True),
            (-1, "whole_gate_accepted", True),
        ]:
            rows = report()
            rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(V.VerificationFailure):
                accept(rows)


class SourceBoundaries(unittest.TestCase):
    def test_fixture_is_four_tables_only_and_no_version_fabrication(self):
        source = (ROOT / "scripts/isolated_outcome_fixture.py").read_text()
        self.assertEqual(F.TABLES, {"tenants", "llm_attempts", "llm_dispatch_receipts", "llm_outcome_events"})
        for forbidden in (
            "CREATE DATABASE",
            "CREATE ROLE",
            "DROP ",
            "TRUNCATE ",
            "command.stamp",
            "Base.metadata.create_all",
            "ensure_test_database(",
            "repair_unique_constraints(",
        ):
            self.assertNotIn(forbidden, source)

    def test_guard_and_role_checks_not_only_url_claim(self):
        source = (ROOT / "scripts/isolated_outcome_fixture.py").read_text()
        for marker in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolreplication",
            "rolbypassrls",
            "pg_auth_members",
            "aclexplode",
            "SET TRANSACTION READ ONLY",
            "Post-commit fixture differs",
            "Four-table fixture scope",
        ):
            self.assertIn(marker, source)

    def test_existing_mutation_scope_and_unknown_commit_stop_remain(self):
        source = (ROOT / "scripts/verify_llm_outcome_events.py").read_text()
        for marker in (
            "rollback_only",
            "before_commit_fault",
            "after_commit_fault",
            "do_not_rerun_automatically",
            "No automatic resume",
        ):
            self.assertIn(marker.lower(), source.lower())


class ColumnStorageSignature(unittest.TestCase):
    def test_float_and_double_precision_are_same_postgresql_storage(self):
        from sqlalchemy import Float
        from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION

        self.assertEqual(F.column_type_signature(Float()), F.column_type_signature(DOUBLE_PRECISION()))

    def test_real_is_not_default_double_precision(self):
        from sqlalchemy import Float
        from sqlalchemy.dialects.postgresql import REAL

        self.assertNotEqual(F.column_type_signature(Float()), F.column_type_signature(REAL()))
        self.assertEqual(F.column_type_signature(Float(24)), F.column_type_signature(REAL()))
        self.assertEqual(F.column_type_signature(Float(25)), F.column_type_signature(Float(53)))

    def test_invalid_float_precision_rejects(self):
        from sqlalchemy import Float

        for precision in (0, 54):
            with self.subTest(precision=precision), self.assertRaises(F.receipts.VerificationFailure):
                F.column_type_signature(Float(precision))

    def test_timestamp_rendering_does_not_hide_timezone(self):
        from sqlalchemy import DateTime
        from sqlalchemy.dialects.postgresql import TIMESTAMP

        self.assertEqual(
            F.column_type_signature(DateTime(timezone=True)), F.column_type_signature(TIMESTAMP(timezone=True))
        )
        self.assertNotEqual(
            F.column_type_signature(DateTime(timezone=True)), F.column_type_signature(TIMESTAMP(timezone=False))
        )

    def test_timestamp_precision_is_preserved(self):
        from sqlalchemy import DateTime
        from sqlalchemy.dialects.postgresql import TIMESTAMP

        self.assertEqual(
            F.column_type_signature(DateTime(timezone=True)),
            F.column_type_signature(TIMESTAMP(timezone=True, precision=6)),
        )
        self.assertNotEqual(
            F.column_type_signature(DateTime(timezone=True)),
            F.column_type_signature(TIMESTAMP(timezone=True, precision=3)),
        )

    def test_numeric_precision_and_scale_are_preserved(self):
        from sqlalchemy import Numeric
        from sqlalchemy.dialects.postgresql import NUMERIC

        expected = F.column_type_signature(Numeric(18, 9))
        self.assertEqual(expected, F.column_type_signature(NUMERIC(18, 9)))
        for other in (NUMERIC(17, 9), NUMERIC(18, 8), NUMERIC()):
            self.assertNotEqual(expected, F.column_type_signature(other))

    def test_length_collation_integer_width_and_json_kind_are_preserved(self):
        from sqlalchemy import JSON, BigInteger, Integer, String, Text
        from sqlalchemy.dialects.postgresql import JSONB, VARCHAR

        self.assertEqual(F.column_type_signature(String(100)), F.column_type_signature(VARCHAR(100)))
        for other in (String(50), String(100, collation="C"), Text()):
            self.assertNotEqual(F.column_type_signature(String(100)), F.column_type_signature(other))
        self.assertNotEqual(F.column_type_signature(Integer()), F.column_type_signature(BigInteger()))
        self.assertNotEqual(F.column_type_signature(JSON()), F.column_type_signature(JSONB()))

    def test_unreviewed_type_is_not_accepted_by_rendering(self):
        from sqlalchemy import LargeBinary

        with self.assertRaises(F.receipts.VerificationFailure):
            F.column_type_signature(LargeBinary())

    def shape(self, *, timezone=True, length=100, nullable=False, reverse=False):
        from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table
        from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, INTEGER, TIMESTAMP, VARCHAR

        table = Table(
            "fixture_shape",
            MetaData(schema=F.TARGET[1]),
            Column("id", Integer, primary_key=True),
            Column("cost", Float, nullable=False),
            Column("name", String(100), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
        columns = [
            {"name": "id", "type": INTEGER(), "nullable": False, "default": "nextval('fixture_id_seq')"},
            {"name": "cost", "type": DOUBLE_PRECISION(), "nullable": False, "default": "5.0"},
            {"name": "name", "type": VARCHAR(length), "nullable": nullable, "default": None},
            {"name": "created_at", "type": TIMESTAMP(timezone=timezone), "nullable": False, "default": "now()"},
        ]
        inspector = SimpleNamespace(
            get_columns=lambda *a, **kw: list(reversed(columns)) if reverse else columns,
            get_pk_constraint=lambda *a, **kw: {"constrained_columns": ["id"]},
            get_check_constraints=lambda *a, **kw: [],
            get_foreign_keys=lambda *a, **kw: [],
        )
        with (
            patch("sqlalchemy.inspect", return_value=inspector),
            patch.object(F.receipts, "reflected_keys", return_value={}),
        ):
            return F.base_shape(object(), table)

    def test_real_type_objects_pass_base_shape_without_string_equality(self):
        value = self.shape()
        self.assertEqual(value["columns"][1][1], ("float", 53))
        self.assertEqual(value["columns"][3][1], ("timestamp", True, 6))
        self.assertEqual(value["columns"][3][3], "now()")

    def test_base_shape_rejects_real_timezone_length_nullability_and_order_drift(self):
        for change in ({"timezone": False}, {"length": 50}, {"nullable": True}, {"reverse": True}):
            with self.subTest(change=change), self.assertRaises(F.receipts.VerificationFailure):
                self.shape(**change)


class Transaction:
    def __init__(self, engine):
        self.engine = engine
        self.is_active = True

    def __await__(self):
        async def value():
            return self

        return value().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *errors):
        if self.is_active:
            await self.rollback()

    async def rollback(self):
        self.engine.events.append("rollback")
        self.is_active = False

    async def commit(self):
        self.engine.events.append("commit")
        self.is_active = False
        if self.engine.fail_commit:
            raise OSError("synthetic unknown commit")


class Connection:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        return self

    async def __aexit__(self, *errors):
        pass

    async def execution_options(self, **options):
        return self

    def begin(self):
        return Transaction(self.engine)

    async def execute(self, statement):
        self.engine.events.append(str(statement))

    async def run_sync(self, callback):
        # Transaction-state test only; never execute the DDL callback.
        self.engine.events.append("bounded fixture callback")


class Engine:
    def __init__(self, fail_commit=False):
        self.fail_commit = fail_commit
        self.events = []

    def connect(self):
        return Connection(self)

    async def dispose(self):
        self.events.append("dispose")


class PreparationTransactions(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, engine, *, mismatch=False, unsafe=False):
        state = {"commit_attempted": False, "commit_acknowledged": False}
        models = SimpleNamespace(
            Tenant=object(), LLMAttempt=object(), LLMDispatchReceipt=object(), LLMOutcomeEvent=object()
        )
        runner = load_isolated_source(
            "_fixture_flow_test",
            ROOT / "scripts/isolated_outcome_fixture.py",
            imports={
                "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="test_schema", DEBUG=False)),
                "app.core.database": SimpleNamespace(async_engine=engine),
                "app.models": models,
            },
        )
        baseline = {"tables": {}}
        catalog = {"outcome": {}, "base": {}}
        values = [baseline, {"tables": {"unexpected": True}} if mismatch else baseline, baseline]
        empty = AsyncMock(side_effect=runner.receipts.VerificationFailure("unsafe") if unsafe else None)
        with (
            patch.object(runner, "empty", empty),
            patch.object(runner, "snapshot", AsyncMock(side_effect=values)),
            patch.object(runner, "prepared_catalog", AsyncMock(return_value=catalog)),
            patch.object(runner, "receipt_revision", return_value=object()),
            patch.object(runner.application, "load_revision", return_value=object()),
            patch.object(runner.application, "capture", return_value=object()),
            patch.object(runner.application, "idle_processes"),
            patch.object(runner, "emit"),
        ):
            error = None
            try:
                await runner.run(state)
            except (OSError, runner.receipts.VerificationFailure) as found:
                error = found
        return state, error

    async def test_rehearsal_rolls_back_before_single_commit_then_final_read(self):
        engine = Engine()
        state, error = await self.exercise(engine)
        self.assertIsNone(error)
        self.assertEqual(state, {"commit_attempted": True, "commit_acknowledged": True})
        self.assertEqual(engine.events.count("commit"), 1)
        self.assertLess(engine.events.index("rollback"), engine.events.index("commit"))
        self.assertEqual(engine.events[-1], "dispose")

    async def test_uncertain_commit_is_never_retried(self):
        engine = Engine(fail_commit=True)
        state, error = await self.exercise(engine)
        self.assertIsInstance(error, OSError)
        self.assertEqual(state, {"commit_attempted": True, "commit_acknowledged": False})
        self.assertEqual(engine.events.count("commit"), 1)
        self.assertEqual(F.application.failure_status(state), "APPLICATION_OUTCOME_UNCERTAIN")

    async def test_rehearsal_mismatch_rolls_back_without_commit(self):
        engine = Engine()
        state, error = await self.exercise(engine, mismatch=True)
        self.assertIsInstance(error, F.receipts.VerificationFailure)
        self.assertFalse(state["commit_attempted"])
        self.assertNotIn("commit", engine.events)

    async def test_unsafe_target_blocks_before_fixture_callback(self):
        engine = Engine()
        state, error = await self.exercise(engine, unsafe=True)
        self.assertIsInstance(error, F.receipts.VerificationFailure)
        self.assertFalse(state["commit_attempted"])
        self.assertNotIn("bounded fixture callback", engine.events)


if __name__ == "__main__":
    unittest.main()
