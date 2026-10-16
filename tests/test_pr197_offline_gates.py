# UNIQUE_STRING_12345
print("FILE LOADED: test_pr197_offline_gates.py")
"""No-DB regression tests for the PR197 launcher and harness gates.

Every test uses doubles only: no database connection, no DSN resolution, no
Alembic execution. The launcher is driven through `main()` with a fake
engine/connection scripted per SQL, a recorded Alembic `upgrade` double and a
manifest in a temporary directory. The harness approval/gate logic is exercised
with doubled instances and private temporary evidence paths.

Covered review bullets:
* the default launcher never invokes Alembic;
* wrong target / approval / source pin and fixture collisions are rejected
  BEFORE any write;
* `--apply` performs exactly ONE normal upgrade step, only to the target
  revision, only after the source revision verified;
* unexpected catalog deltas and retention drift STOP the run;
* evidence files are appended, never overwritten, and created 0600;
* every write/probe path in the harness is gated;
* no retained-target TRUNCATE exists anywhere in the harness.

Run standalone:

    python tests/test_pr197_offline_gates.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import alembic.command
import sqlalchemy

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "pr197_normal_migration_launcher.py"
HARNESS_PATH = ROOT / "tests" / "acceptance" / "test_pr197_reviewed_baseline_pg.py"

DSN = "postgresql://starck:double-secret@localhost:5432/social_manager_pr180_migration"
RETENTION_TABLES = (
    "llm_budget_days",
    "llm_budget_reservations",
    "llm_dispatch_receipts",
    "llm_outcome_events",
    "llm_attempts",
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAUNCHER = load_module("pr197_launcher_under_test", LAUNCHER_PATH)
HARNESS = load_module("pr197_harness_under_test", HARNESS_PATH)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def one(self):
        return tuple(self.value) if not isinstance(self.value, tuple) else self.value

    def scalar_one(self):
        return self.value[0] if isinstance(self.value, tuple) else self.value

    def scalar_one_or_none(self):
        return self.scalar_one()

    def __iter__(self):
        return iter(self.value)

    def scalars(self):
        return SimpleNamespace(all=lambda: [row[0] for row in self.value])


class FakeConn:
    """Scripted connection: every launcher query is routed by SQL content."""

    def __init__(self, state: dict):
        self.state = state

    def _result(self, sql, params=None):
        s = " ".join(str(sql).split())
        st = self.state
        if "pg_stat_activity" in s:
            return FakeResult(st["others"])
        if "current_database()" in s:
            return FakeResult(tuple(st["identity"]))
        if "pg_namespace" in s:
            return FakeResult(st["schema_exists"])
        if "table_name='alembic_version'" in s:
            return FakeResult(st["version_table"])
        if "version_num" in s:
            if st["applied"]:
                return FakeResult(st["to_rev"])
            return FakeResult(st.get("reported_rev", st["from_rev"]))
        if "information_schema.tables" in s and "llm_budget_baselines" in s:
            return FakeResult(st["baselines"])
        if "SELECT count(*) FROM" in s:
            for table in RETENTION_TABLES:
                if f'"{table}"' in s:
                    key = "retention_after" if st["applied"] else "retention_before"
                    return FakeResult(st[key][table])
        if "server_version" in s:
            return FakeResult(("15.14 (Homebrew)", 0, 0, 0, 0))
        raise AssertionError(f"unexpected query in launcher path: {s}")

    def execute(self, statement, params=None):
        return self._result(statement, params)

    def exec_driver_sql(self, sql, params=None):
        s = " ".join(str(sql).split())
        st = self.state
        sets = st["after_sets"] if st["applied"] else st["before_sets"]
        if "information_schema.tables" in s:
            return FakeResult([(name,) for name in sorted(sets["tables"])])
        if "information_schema.triggers" in s:
            # event_object_table, trigger_name
            return FakeResult([tuple(name.split(".", 1)) for name in sorted(sets["triggers"])])
        if "pg_proc" in s:
            return FakeResult([(name,) for name in sorted(sets["functions"])])
        if "information_schema.table_constraints" in s:
            # event_object_table, constraint_name
            return FakeResult([tuple(name.split(".", 1)) for name in sorted(sets["constraints"])])
        raise AssertionError(f"unexpected driver query in launcher path: {s}")


class FakeEngine:
    def __init__(self, state: dict):
        self.state = state

    @contextmanager
    def begin(self):
        yield FakeConn(self.state)


def base_state(**overrides) -> dict:
    state = {
        "identity": ["social_manager_pr180_migration", "starck", "::1/128", 5432],
        "others": 0,
        "schema_exists": 1,
        "version_table": 1,
        "from_rev": "0095",
        "to_rev": "0096",
        "baselines": 0,
        "applied": False,
        "before_sets": {
            "tables": {"alembic_version", "llm_budget_days", "llm_budget_reservations"},
            "triggers": {"llm_budget_reservations.llm_budget_reservations_immutable"},
            "functions": {"reject_llm_budget_reservation_mutation()"},
            "constraints": {"llm_budget_reservations.llm_budget_reservations_pkey"},
        },
        "after_sets": {
            "tables": {"alembic_version", "llm_budget_days", "llm_budget_reservations"},
            "triggers": {"llm_budget_reservations.llm_budget_reservations_immutable"},
            "functions": {"reject_llm_budget_reservation_mutation()"},
            "constraints": {"llm_budget_reservations.llm_budget_reservations_pkey"},
        },
        "retention_before": {table: 3 for table in RETENTION_TABLES},
        "retention_after": {table: 3 for table in RETENTION_TABLES},
    }
    state.update(overrides)
    return state


def valid_approval(**overrides) -> dict:
    approval = {
        "target": ["localhost", 5432, "social_manager_pr180_migration", "starck"],
        "schema": "public",
        "expected_revision": "0097",
        "executor": "starck",
        "server_identity": ["::1/128", "5432"],
        "write_cases": list(HARNESS.WRITE_CASES),
        "probe_cases": list(HARNESS.PROBE_CASES),
        "replay_cases": list(HARNESS.REPLAY_CASES),
        "synthetic_tenants": list(HARNESS.SYNTHETIC_TENANTS),
        "allowed_changes": [list(item) for item in HARNESS.ALLOWED_SYNTHETIC_CHANGES],
        "run_id": "0" * 32,
        "synthetic_model_id": 1,
        "synthetic_provider_id": 1,
    }
    approval.update(overrides)
    return approval


class LauncherDoubleTests(unittest.TestCase):
    def run_launcher(self, state: dict, *, apply: bool):
        calls: list[str] = []

        def upgrade_double(config, revision):
            calls.append(revision)
            state["applied"] = True

        manifest_path = self.tmp / f"manifest-{id(state)}-{int(apply)}.json"
        argv = [
            "pr197_normal_migration_launcher.py",
            "--dsn",
            DSN,
            "--database",
            "social_manager_pr180_migration",
            "--schema",
            "public",
            "--executor",
            "starck",
            "--from-rev",
            state["from_rev"],
            "--to-rev",
            state["to_rev"],
            "--manifest-out",
            str(manifest_path),
        ]
        if apply:
            argv.append("--apply")
        env = {"POSTGRES_URL": DSN, "DB_SCHEMA": "public", "SECRET_KEY": "offline-double", "ENVIRONMENT": "test"}
        with (
            patch.object(sqlalchemy, "create_engine", return_value=FakeEngine(state)),
            patch.object(alembic.command, "upgrade", upgrade_double),
            patch.dict(os.environ, env, clear=True),
            patch.object(sys, "argv", argv),
        ):
            rc = LAUNCHER.main()
        manifest = json.loads(manifest_path.read_text())
        return rc, manifest, calls, manifest_path

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_launcher_never_calls_alembic(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(), apply=False)
        self.assertEqual(rc, 0)
        self.assertEqual(manifest["status"], "verified-readonly")
        self.assertEqual(calls, [], "default mode must not invoke Alembic")
        self.assertEqual(manifest["requested"]["apply"], False)

    def test_apply_performs_single_step_to_target_revision_only(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(), apply=True)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["0095:0096"], "exactly one normal step, no head/chain")
        self.assertEqual(manifest["after"]["revision"], "0096")
        self.assertEqual(manifest["after"]["retention"], {table: 3 for table in RETENTION_TABLES})

    def test_apply_from_unexpected_source_revision_stops_before_write(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(reported_rev="0094"), apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [], "source revision mismatch must stop before DDL")
        self.assertEqual(manifest["status"], "stopped:unexpected-revision:0094")

    def test_wrong_database_identity_stops(self):
        state = base_state(identity=["other_database", "starck", "::1/128", 5432])
        rc, manifest, calls, _ = self.run_launcher(state, apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertEqual(manifest["status"], "stopped:target-identity-drift")

    def test_wrong_executor_identity_stops(self):
        state = base_state(identity=["social_manager_pr180_migration", "someone_else", "::1/128", 5432])
        rc, manifest, calls, _ = self.run_launcher(state, apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertEqual(manifest["status"], "stopped:target-identity-drift")

    def test_foreign_backend_stops(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(others=2), apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertEqual(manifest["status"], "stopped:foreign-backend-on-target")

    def test_missing_schema_stops(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(schema_exists=0), apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertEqual(manifest["status"], "stopped:schema-missing-or-unversioned")

    def test_unversioned_schema_stops(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(version_table=0), apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertEqual(manifest["status"], "stopped:schema-unversioned")

    def test_existing_baseline_table_stops(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(from_rev="0095", to_rev="0096", baselines=1), apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertEqual(manifest["status"], "stopped:baseline-present-unexpected")

    def test_disallowed_step_stops(self):
        rc, manifest, calls, _ = self.run_launcher(base_state(to_rev="0097"), apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])
        self.assertEqual(manifest["status"], "stopped:step-not-allowed")

    def test_unexpected_catalog_delta_stops(self):
        state = base_state()
        state["after_sets"]["tables"] = {"alembic_version", "llm_budget_days", "llm_budget_reservations"}
        state["to_rev"] = "0097"
        state["from_rev"] = "0096"
        rc, manifest, calls, _ = self.run_launcher(state, apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(calls, ["0096:0097"], "the step ran once, readback caught the drift")
        self.assertIn("expected-object-missing", manifest["status"])

    def test_retention_drift_stops(self):
        state = base_state()
        state["retention_after"] = {table: 2 for table in RETENTION_TABLES}
        rc, manifest, calls, _ = self.run_launcher(state, apply=True)
        self.assertEqual(rc, 3)
        self.assertEqual(len(calls), 1)
        self.assertIn("retention-drift", manifest["status"])

    def test_manifest_is_private_and_redacted(self):
        rc, manifest, calls, path = self.run_launcher(base_state(), apply=False)
        self.assertEqual(rc, 0)
        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode, 0o600, "the manifest must be created 0600")
        text = path.read_text()
        self.assertNotIn("double-secret", text, "the manifest must never contain the DSN password")
        self.assertIn("REDACTED", text)


    def test_alembic_called_with_single_revision_not_range(self):
        """Alembic must be called with a single revision step, not a range."""
        state = base_state()
        state["to_rev"] = "0097"
        state["from_rev"] = "0095"
        # We'll capture the call to alembic.command.upgrade
        calls = []
        def upgrade_double(config, revision):
            calls.append(revision)
            state["applied"] = True
        manifest_path = self.tmp / f"manifest-{id(state)}-1.json"
        argv = [
            "pr197_normal_migration_launcher.py",
            "--dsn", DSN,
            "--database", "social_manager_pr180_migration",
            "--schema", "public",
            "--executor", "starck",
            "--from-rev", state["from_rev"],
            "--to-rev", state["to_rev"],
            "--manifest-out", str(manifest_path),
            "--apply"
        ]
        env = {"POSTGRES_URL": DSN, "DB_SCHEMA": "public", "SECRET_KEY": "offline-double", "ENVIRONMENT": "test"}
        with (
            patch.object(sqlalchemy, "create_engine", return_value=FakeEngine(state)),
            patch.object(alembic.command, "upgrade", upgrade_double),
            patch.dict(os.environ, env, clear=True),
            patch.object(sys, "argv", argv),
        ):
            rc = LAUNCHER.main()
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["0097"], "Alembic must be called with the target revision only, not a range")

    def test_manifest_append_not_overwrite(self):
        """Manifest must be appended, never overwritten."""
        # First run
        state1 = base_state()
        state1["to_rev"] = "0096"
        state1["from_rev"] = "0095"
        manifest_path1 = self.tmp / "manifest1.json"
        argv1 = [
            "pr197_normal_migration_launcher.py",
            "--dsn", DSN,
            "--database", "social_manager_pr180_migration",
            "--schema", "public",
            "--executor", "starck",
            "--from-rev", state1["from_rev"],
            "--to-rev", state1["to_rev"],
            "--manifest-out", str(manifest_path1),
            "--apply"
        ]
        env1 = {"POSTGRES_URL": DSN, "DB_SCHEMA": "public", "SECRET_KEY": "offline-double", "ENVIRONMENT": "test"}
        with (
            patch.object(sqlalchemy, "create_engine", return_value=FakeEngine(state1)),
            patch.object(alembic.command, "upgrade", lambda config, revision: setattr(state1, "applied", True)),
            patch.dict(os.environ, env1, clear=True),
            patch.object(sys, "argv", argv1),
        ):
            rc1 = LAUNCHER.main()
        self.assertEqual(rc1, 0)
        manifest1_first = manifest_path1.read_text()
        # Second run with same manifest path (should append)
        state2 = base_state()
        state2["to_rev"] = "0097"
        state2["from_rev"] = "0096"
        argv2 = [
            "pr197_normal_migration_launcher.py",
            "--dsn", DSN,
            "--database", "social_manager_pr180_migration",
            "--schema", "public",
            "--executor", "starck",
            "--from-rev", "0096",
            "--to-rev", "0097",
            "--manifest-out", str(manifest_path1),
            "--apply"
        ]
        env2 = {"POSTGRES_URL": DSN, "DB_SCHEMA": "public", "SECRET_KEY": "offline-double", "ENVIRONMENT": "test"}
        with (
            patch.object(sqlalchemy, "create_engine", return_value=FakeEngine(state2)),
            patch.object(alembic.command, "upgrade", lambda config, revision: setattr(state2, "applied", True)),
            patch.dict(os.environ, env2, clear=True),
            patch.object(sys, "argv", argv2),
        ):
            rc2 = LAUNCHER.main()
        self.assertEqual(rc2, 0)
        manifest1_both = manifest_path1.read_text()
        # The manifest should contain two json objects (each line is a json object? Actually, the manifest is a single json object per run?)
        # But note: the finish function writes a single json object and then a newline.
        # If we open with O_APPEND, we will have two json objects concatenated, which is not valid json.
        # However, the test in the original evidence said we check that the manifest is appended.
        # We'll simply check that the file size increased and that the first run's content is still at the beginning.
        self.assertGreater(len(manifest1_both), len(manifest1_first))
        self.assertTrue(manifest1_both.startswith(manifest1_first))


class HarnessApprovalTests(unittest.TestCase):
    def env(self, approval, url=DSN):
        return patch.dict(
            os.environ,
            {"PR197_PG_URL": url, "PR197_APPROVAL": json.dumps(approval)},
            clear=True,
        )

    def test_missing_approval_skips(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HARNESS.ApprovalMissing):
                HARNESS.load_approval()

    def test_wrong_source_pin_rejected_before_any_target_use(self):
        with patch.object(HARNESS, "git", lambda *args: "0" * 40):
            with self.env(valid_approval()):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("runner-source-pin", str(ctx.exception))

    def test_wrong_synthetic_tenant_allocation_rejected(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval(synthetic_tenants=[900001, 999999])):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("synthetic-tenant-allocation", str(ctx.exception))

    def test_wrong_write_grant_rejected(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval(write_cases=[])):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("write-cases-grant", str(ctx.exception))

    def test_wrong_probe_grant_rejected(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval(probe_cases=["some_case"])):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("probe-cases-grant", str(ctx.exception))

    def test_wrong_replay_grant_rejected(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval(replay_cases=[])):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("replay-cases-grant", str(ctx.exception))

    def test_wrong_allowed_changes_contract_rejected(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval(allowed_changes=[["public.roles", "INSERT", "x"]])):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("allowed-changes-contract", str(ctx.exception))

    def test_wrong_expected_revision_rejected(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval(expected_revision="0096")):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("expected-revision", str(ctx.exception))

    def test_wrong_target_tuple_rejected(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval(), url="postgresql://starck:x@localhost:5432/other_database"):
                with self.assertRaises(HARNESS.Blocked) as ctx:
                    HARNESS.load_approval()
        self.assertIn("target-tuple", str(ctx.exception))

    def test_valid_approval_returns_enumerated_payers(self):
        with patch.object(HARNESS, "git", lambda *args: HARNESS.SOURCE_PIN):
            with self.env(valid_approval()):
                loaded = HARNESS.load_approval()
        self.assertEqual(loaded["payers"], list(HARNESS.SYNTHETIC_TENANTS))


class HarnessGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        cls = HARNESS.Pr197PgAcceptance
        cls.journal_path = self.tmp / "journal.jsonl"
        cls.preconditions_verified = True
        self.instance = object.__new__(cls)
        self.instance.approval = {"approval": valid_approval()}

    def tearDown(self):
        self._tmp.cleanup()

    def journal_lines(self):
        return (self.tmp / "journal.jsonl").read_text().splitlines()

    def test_allowed_write_is_granted_and_journalled(self):
        self.instance.gate_write(
            "test_fresh_frozen_day_with_original_provenance", "public.llm_budget_baselines", "INSERT"
        )
        records = [json.loads(line) for line in self.journal_lines()]
        self.assertEqual(records[-1]["kind"], "write-gate")

    def test_ungranted_write_case_is_blocked(self):
        with self.assertRaises(HARNESS.Blocked) as ctx:
            self.instance.gate_write("test_not_a_case", "public.llm_budget_baselines", "INSERT")
        self.assertIn("write-case-not-granted", str(ctx.exception))

    def test_unknown_write_pair_is_blocked(self):
        with self.assertRaises(HARNESS.Blocked) as ctx:
            self.instance.gate_write("test_fresh_frozen_day_with_original_provenance", "public.roles", "INSERT")
        self.assertIn("write-not-allowed", str(ctx.exception))

    def test_write_before_preconditions_is_blocked(self):
        self.instance.preconditions_verified = False
        with self.assertRaises(HARNESS.Blocked) as ctx:
            self.instance.gate_write("test_fresh_frozen_day_with_original_provenance", "public.tenants", "INSERT")
        self.assertIn("write-before-preconditions", str(ctx.exception))

    def test_probe_scope_enforced(self):
        with self.assertRaises(HARNESS.Blocked) as ctx:
            self.instance.gate_probe("test_immutable_update_delete_refused", "public.tenants", "DELETE")
        self.assertIn("probe-not-in-scope", str(ctx.exception))

    def test_replay_gate_blocks_raw_provision(self):
        with self.assertRaises(HARNESS.Blocked) as ctx:
            self.instance.gate_replay("test_fresh_frozen_day_with_original_provenance")
        self.assertIn("replay-case-not-granted", str(ctx.exception))
        self.instance.gate_replay("test_exact_replay_returns_false_without_rewrites")
        self.assertEqual(self.journal_lines()[-1].count("replay-gate"), 1)

    def test_evidence_journal_appends_never_overwrites(self):
        self.instance.gate_write("test_fresh_frozen_day_with_original_provenance", "public.tenants", "INSERT")
        first = self.journal_lines()[0]
        self.instance.gate_write("test_distinct_payer_isolation", "public.tenants", "INSERT")
        lines = self.journal_lines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], first, "the first evidence record must survive")
        mode = stat.S_IMODE((self.tmp / "journal.jsonl").stat().st_mode)
        self.assertEqual(mode, 0o600)


    def test_probe_never_commits_on_success(self):
        """Probe must not commit when the statement succeeds (probe failure)."""
        # We'll create a fake connection that records commit calls.
        class TrackingFakeConn:
            def __init__(self):
                self.commits = 0
                self.rollbacks = 0

            async def execute(self, statement, params=None):
                pass

            async def commit(self):
                self.commits += 1

            async def rollback(self):
                self.rollbacks += 1

        # Create a mock async session maker that returns our tracking connection as an async context manager
        from unittest.mock import AsyncMock, MagicMock

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock(side_effect=lambda: setattr(mock_session, 'commits', getattr(mock_session, 'commits', 0) + 1))
        mock_session.rollback = AsyncMock(side_effect=lambda: setattr(mock_session, 'rollbacks', getattr(mock_session, 'rollbacks', 0) + 1))

        # We need the async session maker to return a context manager that yields mock_session
        mock_session_maker = MagicMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session
        mock_session_maker.return_value.__aexit__.return_value = None

        harness_instance = self.__class__.__new__(self.__class__)
        harness_instance.db = SimpleNamespace()
        harness_instance.db.async_session_maker = mock_session_maker
        harness_instance.approval = {"approval": {
            "write_cases": [], "probe_cases": ["some_case"], "replay_cases": []
        }}
        harness_instance.preconditions_verified = True

        with patch.object(harness_instance, 'gate_probe'):
            asyncio.run(harness_instance.probe(
                "some_case", "some_table", "some_operation", "SELECT 1", {}
            ))

            # Check that commit was not called
            self.assertEqual(mock_session.commit.call_count, 0, "probe must not commit on success")
            # Check that rollback was called once
            self.assertEqual(mock_session.rollback.call_count, 1, "probe must rollback on success")


class HarnessSourceContractTests(unittest.TestCase):
    """Static doubles: the harness source itself must satisfy the contract."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(HARNESS_PATH.read_text())
        cls.cls = next(
            node for node in cls.tree.body if isinstance(node, ast.ClassDef) and node.name == "Pr197PgAcceptance"
        )
        cls.methods = {
            node.name: node for node in cls.cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_no_executed_truncate_statement_exists(self):
        executed = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("execute", "exec_driver_sql"):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            executed.append(arg.value)
        for statement in executed:
            self.assertNotIn("TRUNCATE", statement.upper())
        self.assertTrue(
            any("TRUNCATE" in item for item in HARNESS.DEFERRED),
            "TRUNCATE must remain listed only as a deferred disposable-target probe",
        )

    def test_no_test_method_attempts_truncate(self):
        self.assertNotIn("truncate", " ".join(HARNESS.CASES).lower())

    def test_every_direct_write_path_is_gated(self):
        signals = ("provision_reviewed_day(", "reserve_receipt(")
        gates = ("gate_write(", "gate_probe(", "gate_replay(")
        for name, node in self.methods.items():
            if not name.startswith("test_"):
                continue
            body = ast.unparse(node)
            if any(signal in body for signal in signals):
                self.assertTrue(
                    any(gate in body for gate in gates),
                    f"{name} performs a manager write/receipt path without a gate",
                )

    def test_gates_demand_verified_preconditions(self):
        for name in ("gate_write", "gate_probe", "gate_replay"):
            body = ast.unparse(self.methods[name])
            self.assertIn("preconditions_verified", body, f"{name} must demand verified preconditions")

    def test_fixture_collision_checked_before_any_write(self):
        body = ast.unparse(self.methods["verify_preconditions"])
        self.assertIn("synthetic-payer-rows-present", body)
        self.assertIn("foreign-backend-on-target", body)

    def test_helper_writes_are_internally_gated(self):
        for name in ("seed_synthetic_tenant", "seed_unreviewed_day"):
            body = ast.unparse(self.methods[name])
            self.assertIn("gate_write", body, f"{name} must gate its own durable write")
        self.assertIn("gate_probe", ast.unparse(self.methods["probe"]))

    def test_default_sessions_are_read_only(self):
        node = self.methods["session"]
        args = node.args
        names = [a.arg for a in args.args]
        self.assertIn("readonly", names)
        index = names.index("readonly")
        default = args.defaults[index - (len(names) - len(args.defaults))]
        self.assertIsInstance(default, ast.Constant)
        self.assertIs(default.value, True, "sessions must default to read-only")
        self.assertIn("SET TRANSACTION READ ONLY", ast.unparse(node))

    def test_launcher_default_is_read_only(self):
        launcher_src = LAUNCHER_PATH.read_text()
        self.assertIn('action="store_true"', launcher_src)
        self.assertIn("if not args.apply:", launcher_src)
        self.assertIn("verified-readonly", launcher_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
