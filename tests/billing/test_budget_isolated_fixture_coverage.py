"""No-DB regressions for the changed safety boundaries; no old suites invoked."""

import asyncio
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load("scripts/prepare_llm_budget_fixture.py", "_coverage_preparation")
r = load("tests/acceptance/test_llm_budget_pg.py", "_coverage_runner")


class FakeRows:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class Savepoint:
    def __init__(self):
        self.is_active = True
        self.rollbacks = 0

    def __enter__(self):
        return self

    def rollback(self):
        self.is_active = False
        self.rollbacks += 1

    def __exit__(self, *_args):
        if self.is_active:
            self.rollback()


class NoDBConnection:
    def __init__(self):
        self.savepoints = []

    def begin_nested(self):
        point = Savepoint()
        self.savepoints.append(point)
        return point


class NoNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A regression that accidentally reaches a real DB/network must fail.
        cls.connection_guards = [
            patch("socket.socket.connect", side_effect=AssertionError("no-DB scope: socket connection forbidden")),
            patch("socket.socket.connect_ex", side_effect=AssertionError("no-DB scope: socket connection forbidden")),
            patch(
                "sqlalchemy.engine.Engine.connect", side_effect=AssertionError("no-DB scope: DB connection forbidden")
            ),
        ]
        for guard in cls.connection_guards:
            guard.start()

    @classmethod
    def tearDownClass(cls):
        for guard in reversed(cls.connection_guards):
            guard.stop()


class ModeDispatch(NoNetworkTests):
    def test_both_modes_are_awaited(self):
        for isolated in (True, False):
            package = r.BudgetPackage()
            package.isolated = isolated
            package.verify_target, package.verify_isolated_target = AsyncMock(), AsyncMock()
            asyncio.run(package.verify_configured_target())
            (package.verify_isolated_target if isolated else package.verify_target).assert_awaited_once()
            (package.verify_target if isolated else package.verify_isolated_target).assert_not_awaited()

    def test_invalid_script_pin_fails_before_git(self):
        for value in (None, "PLACEHOLDER", "a" * 64, "g" * 40):
            with patch.object(p, "git") as git:
                with self.assertRaisesRegex(p.VerificationFailure, "preparation-commit-pin"):
                    p.verify_script_pin(value)
                git.assert_not_called()

    def test_pin_must_be_a_commit(self):
        with patch.object(p, "git", return_value="blob"):
            with self.assertRaisesRegex(p.VerificationFailure, "preparation-pin-not-commit"):
                p.verify_script_pin("a" * 40)

    def test_script_content_not_just_pin_is_checked(self):
        with patch.object(p, "git", return_value="commit"), patch.object(p, "git_bytes", return_value=b"wrong"):
            with self.assertRaisesRegex(p.VerificationFailure, "preparation-content-drift"):
                p.verify_script_pin("a" * 40)


class VerifiedHelperLoading(NoNetworkTests):
    def test_unpinned_helper_is_not_executed(self):
        with patch.object(r, "git") as git:
            with self.assertRaisesRegex(r.Blocked, "preparation-commit-pin"):
                r.load_preparation_contract("PLACEHOLDER")
            git.assert_not_called()

    def test_mismatched_module_bytes_refused_before_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts/prepare_llm_budget_fixture.py").write_text('raise RuntimeError("must not execute")')
            with (
                patch.object(r, "ROOT", root),
                patch.object(r, "git", return_value="commit"),
                patch.object(r.subprocess, "check_output", return_value=b"reviewed version"),
            ):
                with self.assertRaisesRegex(r.Blocked, "preparation-content-drift"):
                    r.load_preparation_contract("a" * 40)


class NormalRevisionContract(NoNetworkTests):
    def target(self, revision):
        from contextlib import asynccontextmanager

        package = r.BudgetPackage()
        package.cfg = {"revision_schema": "budget_review", "revision_rows": ["0095"], "target_label": "fixture"}
        package.text = lambda value: value
        package.emit = Mock()
        session = SimpleNamespace(execute=AsyncMock(return_value=FakeRows(revision)))

        @asynccontextmanager
        async def context():
            yield session

        package.session = context
        package._verify_shared_target = AsyncMock(return_value="signature")
        return package, session

    def test_normal_mode_still_executes_qualified_revision_query(self):
        package, session = self.target(["0095"])
        asyncio.run(package.verify_target())
        package._verify_shared_target.assert_awaited_once_with(session)
        self.assertIn('"budget_review".alembic_version', session.execute.call_args.args[0])
        package.emit.assert_called_once()

    def test_revision_mismatch_remains_a_refusal(self):
        package, _session = self.target(["0094"])
        with self.assertRaisesRegex(r.Blocked, "revision-mismatch"):
            asyncio.run(package.verify_target())
        package.emit.assert_not_called()


class ConnectionOrdering(NoNetworkTests):
    def test_connect_has_no_premature_select_and_closes_on_exception(self):
        connection = Mock()
        manager = Mock()
        manager.__enter__ = Mock(return_value=connection)
        manager.__exit__ = Mock(return_value=False)
        engine = Mock()
        engine.connect.return_value = manager
        url = f"postgresql://{p.DDL_OWNER}@localhost:5432/{p.TARGET_DATABASE}"
        with patch("sqlalchemy.create_engine", return_value=engine) as create:
            with self.assertRaisesRegex(RuntimeError, "stop"):
                with p.connect(p.DDL_OWNER, readonly=True, url_input=url):
                    connection.execute.assert_not_called()
                    raise RuntimeError("stop")
            options = create.call_args.kwargs["connect_args"]
            self.assertIn("default_transaction_read_only=on", options["options"])
            self.assertIn("statement_timeout=10000", options["options"])
            self.assertEqual(options["connect_timeout"], 5)
            manager.__exit__.assert_called_once()
            engine.dispose.assert_called_once()

    def test_working_url_rejected_before_engine(self):
        with patch("sqlalchemy.create_engine") as create:
            with self.assertRaisesRegex(p.VerificationFailure, "preparation-url"):
                with p.connect(p.DDL_OWNER, readonly=False, url_input="postgresql://starck@localhost/social_manager"):
                    self.fail("entered")
            create.assert_not_called()

    def test_preflight_first_explicit_statement_is_read_only(self):
        statements = []

        def sql(_conn, statement, _params=None):
            statements.append(statement)
            return FakeRows([])

        with (
            patch.object(p, "sql", side_effect=sql),
            patch.object(p, "identity_and_exclusivity", return_value=["db", "::1", 5432]),
            patch.object(p, "no_alembic"),
            patch.object(p, "user_objects", return_value=[]),
        ):
            state = p.empty_preflight(object())
        self.assertEqual(statements[0], "SET TRANSACTION READ ONLY")
        self.assertEqual(state["schema"], "absent")
        self.assertFalse(any("has_table_privilege" in statement for statement in statements))

    def test_preflight_rejects_populated_database(self):
        with (
            patch.object(p, "sql", return_value=FakeRows([])),
            patch.object(p, "identity_and_exclusivity"),
            patch.object(p, "no_alembic"),
            patch.object(p, "user_objects", return_value=[["public", "private_rows", "r", "starck"]]),
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "database-not-empty"):
                p.empty_preflight(object())


class QuotingAndACL(NoNetworkTests):
    def connection(self):
        from sqlalchemy.dialects.postgresql import dialect

        return SimpleNamespace(dialect=dialect())

    def test_sequence_resolves_namespace_and_relation_separately(self):
        with patch.object(p, "sql", return_value=FakeRows((p.TARGET_SCHEMA, "tenants_id_seq"))):
            name = p.sequence_binding(self.connection(), "tenants")
        self.assertEqual(name, f"{p.TARGET_SCHEMA}.tenants_id_seq")
        self.assertNotIn(f'."{p.TARGET_SCHEMA}.', name)

    def test_sequence_in_other_schema_refused(self):
        with patch.object(p, "sql", return_value=FakeRows(("public", "tenants_id_seq"))):
            with self.assertRaisesRegex(p.VerificationFailure, "sequence-binding"):
                p.sequence_binding(self.connection(), "tenants")

    def test_identifier_injection_refused(self):
        with self.assertRaisesRegex(p.VerificationFailure, "qualified-name"):
            p.qualified(self.connection(), p.TARGET_SCHEMA, 'tenants"; DROP TABLE x')

    def measure(self, extra=False):
        statements = []
        required = set(p.RUNNER_PRIVILEGES)

        def execute(_conn, statement, params):
            statements.append((statement, params))
            privilege = params["privilege"]
            if "has_table_privilege" in statement:
                table = params["table"].split(".")[1]
                value = (table, privilege) in required or (
                    extra and table == "llm_dispatch_receipts" and privilege == "DELETE"
                )
            elif "has_sequence_privilege" in statement:
                value = privilege in ("USAGE", "SELECT")
            elif "has_schema_privilege" in statement:
                value = privilege == "USAGE"
            else:
                value = privilege == "CONNECT"
            return FakeRows(value)

        with patch.object(p, "sql", side_effect=execute):
            actual = p.measure_acl(object())
        return actual, statements

    def test_acl_checks_database_and_schema_separately(self):
        actual, statements = self.measure()
        self.assertTrue(actual["database:CONNECT"])
        self.assertFalse(actual["database:TEMP"])
        self.assertFalse(actual["schema:CREATE"])
        self.assertEqual(len([key for key in actual if key.startswith("sequence:")]), 15)
        self.assertFalse(any(params["privilege"] == "SCHEMA" for _stmt, params in statements))

    def test_extra_privilege_is_not_accepted(self):
        with self.assertRaisesRegex(p.VerificationFailure, "runner-effective-acl"):
            self.measure(extra=True)

    def test_extra_view_refused(self):
        objects = [[p.TARGET_SCHEMA, table, "r", p.DDL_OWNER] for table in p.DEFINED_RELATIONS]
        objects += [[p.TARGET_SCHEMA, table + "_id_seq", "S", p.DDL_OWNER] for table in p.SEQUENCE_TABLES]
        objects.append([p.TARGET_SCHEMA, "unexpected", "v", p.DDL_OWNER])
        with (
            patch.object(p, "no_alembic"),
            patch.object(
                p,
                "sql",
                side_effect=[
                    FakeRows([p.TARGET_SCHEMA]),
                    FakeRows(
                        sorted(
                            [
                                [p.TARGET_SCHEMA, name, kind, p.DDL_OWNER]
                                for table in p.DEFINED_RELATIONS
                                for name, kind in ((table, "c"), ("_" + table, "b"))
                            ]
                        )
                    ),
                ],
            ),
            patch.object(p, "user_objects", return_value=objects),
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "extra-object-kind"):
                p.inspect_prepared(object())

    def test_extra_schema_refused(self):
        with (
            patch.object(p, "no_alembic"),
            patch.object(p, "sql", return_value=FakeRows([p.TARGET_SCHEMA, "other"])),
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "schema-scope"):
                p.inspect_prepared(object())

    def test_extra_enum_refused(self):
        with (
            patch.object(p, "no_alembic"),
            patch.object(
                p,
                "sql",
                side_effect=[
                    FakeRows([p.TARGET_SCHEMA]),
                    FakeRows([[p.TARGET_SCHEMA, "unapproved_enum", "e", p.DDL_OWNER]]),
                ],
            ),
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "type-scope-or-owner"):
                p.inspect_prepared(object())

    def test_wrong_owner_refused(self):
        types = sorted(
            [
                [p.TARGET_SCHEMA, name, kind, p.DDL_OWNER]
                for table in p.DEFINED_RELATIONS
                for name, kind in ((table, "c"), ("_" + table, "b"))
            ]
        )
        with (
            patch.object(p, "no_alembic"),
            patch.object(p, "sql", side_effect=[FakeRows([p.TARGET_SCHEMA]), FakeRows(types)]),
            patch.object(p, "user_objects", return_value=[[p.TARGET_SCHEMA, "tenants", "r", p.RUNNER_ROLE]]),
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "object-schema-or-owner"):
                p.inspect_prepared(object())


class ProbeIsolation(NoNetworkTests):
    def test_seeds_and_each_negative_probe_roll_back(self):
        from sqlalchemy.exc import DBAPIError

        connection = NoDBConnection()
        statements = []

        def execute(_conn, statement, params):
            statements.append((statement, dict(params)))
            if statement.startswith(("UPDATE", "DELETE", "TRUNCATE")):
                raise DBAPIError(None, None, SimpleNamespace(sqlstate="55000"))
            if params["dispatch"] == "1" * 32:
                raise DBAPIError(None, None, SimpleNamespace(sqlstate="23503"))

        with (
            patch.object(p, "qualified", side_effect=lambda _conn, schema, name: schema + "." + name),
            patch.object(p, "sql", side_effect=execute),
        ):
            result = p.run_guard_probes(connection)
        self.assertEqual(len(connection.savepoints), 5)
        self.assertTrue(all(point.rollbacks == 1 for point in connection.savepoints))
        self.assertEqual(result["seed_rows_retained"], 0)
        self.assertIn("prepared_at", statements[0][0])
        self.assertIn("pricing_policy_version", statements[0][0])
        self.assertIn("bound_version", statements[2][0])
        self.assertNotEqual(statements[0][1]["dispatch"], statements[-1][1]["dispatch"])

    def test_wrong_sqlstate_rolls_back_then_refuses(self):
        from sqlalchemy.exc import DBAPIError

        connection = NoDBConnection()
        with patch.object(p, "sql", side_effect=DBAPIError(None, None, SimpleNamespace(sqlstate="42501"))):
            with self.assertRaisesRegex(p.VerificationFailure, "guard-sqlstate"):
                p.expect_sqlstate(connection, "DELETE", {}, "55000")
        self.assertEqual(connection.savepoints[0].rollbacks, 1)

    def test_no_exception_is_not_a_guard_pass(self):
        connection = NoDBConnection()
        with patch.object(p, "sql"):
            with self.assertRaisesRegex(p.VerificationFailure, "guard-not-enforced"):
                p.expect_sqlstate(connection, "DELETE", {}, "55000")
        self.assertFalse(connection.savepoints[0].is_active)


class EnvironmentBoundary(NoNetworkTests):
    def test_private_environment_and_dotenv_are_suppressed_then_restored(self):
        """Run in a fresh interpreter, because that is the guard's subject.

        `application_environment` refuses an interpreter that has already
        imported the application — and under pytest this one has. The check
        therefore runs where it can be true, instead of asserting a clean
        process it cannot produce.
        """
        import subprocess
        import sys
        import textwrap

        script = textwrap.dedent(
            f"""
            import json, os, sys
            sys.path.insert(0, {str(ROOT)!r})
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_prepare_env_check", {str(ROOT / 'scripts/prepare_llm_budget_fixture.py')!r}
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            from unittest.mock import patch
            from pydantic_settings.sources import DotEnvSettingsSource

            url = "postgresql://starck@localhost:5432/social_manager_budget_pr180"
            with patch.dict(os.environ, {{"UNRELATED_PRIVATE_KEY": "never-forward"}}):
                with module.application_environment(url):
                    import dotenv
                    assert "UNRELATED_PRIVATE_KEY" not in os.environ
                    assert os.environ["POSTGRES_URL"] == url
                    assert os.environ["DB_SCHEMA"] == module.TARGET_SCHEMA
                    assert os.environ["AGENT_DAILY_COST_LIMIT"] == "0"
                    assert DotEnvSettingsSource._read_env_files() == {{}}
                    assert dotenv.load_dotenv() is False
                assert os.environ["UNRELATED_PRIVATE_KEY"] == "never-forward"
            print("ok")
            """
        )
        completed = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertIn("ok", completed.stdout)

    def test_preimported_app_refused(self):
        import sys

        with patch.dict(sys.modules, {"app": SimpleNamespace()}):
            with self.assertRaisesRegex(p.VerificationFailure, "app-already-imported"):
                with p.application_environment("dummy"):
                    self.fail("entered")


class Artifacts(NoNetworkTests):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workdir = self.root / ".agent/private-budget-review" / p.RUN_ID
        self.patches = [patch.object(p, "ROOT", self.root), patch.object(p, "WORKDIR", self.workdir)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def test_exclusive_durable_artifact_and_digest(self):
        value = {"phase": "rehearsal", "facts": [1, 2]}
        with patch.object(p.os, "fsync", wraps=os.fsync) as fsync:
            digest = p.write_artifact("rehearsal", value)
            self.assertEqual(fsync.call_count, 2)
        actual, actual_hash = p.read_private(self.workdir / "rehearsal.json")
        self.assertEqual(actual, value)
        self.assertEqual(actual_hash, digest)
        self.assertEqual((self.workdir / "rehearsal.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            p.write_artifact("rehearsal", value)

    def test_artifact_symlink_not_followed(self):
        p.ensure_private_dir()
        victim = self.root / "victim"
        victim.write_text("unchanged")
        (self.workdir / "apply.json").symlink_to(victim)
        with self.assertRaises(FileExistsError):
            p.write_artifact("apply", {"phase": "apply"})
        with self.assertRaisesRegex(p.VerificationFailure, "artifact-path"):
            p.read_private(self.workdir / "apply.json")
        self.assertEqual(victim.read_text(), "unchanged")

    def test_unprivate_artifact_refused(self):
        p.write_artifact("apply", {})
        file = self.workdir / "apply.json"
        file.chmod(0o644)
        with self.assertRaisesRegex(p.VerificationFailure, "artifact-mode"):
            p.read_private(file)

    def test_directory_symlink_refused(self):
        target = self.root / "elsewhere"
        target.mkdir()
        self.workdir.parent.mkdir(parents=True)
        self.workdir.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(p.VerificationFailure, "artifact-directory-symlink"):
            p.write_artifact("apply", {})


class PhaseAndManifestBinding(NoNetworkTests):
    def plan(self):
        now = datetime.now(timezone.utc).replace(hour=12)
        return {
            "kind": "isolated-budget-preparation-approval",
            "run_id": p.RUN_ID,
            "target": [p.TARGET_HOST, p.TARGET_PORT, p.TARGET_DATABASE, p.DDL_OWNER],
            "schema": p.TARGET_SCHEMA,
            "runner_role": p.RUNNER_ROLE,
            "operations": list(p.PREPARATION_OPERATIONS),
            "owner_approval": dict(p.APPROVALS),
            "nonproduction": True,
            "exclusive_window": True,
            "execution_sha": "a" * 40,
            "preparation_sha": "b" * 40,
            "sources": {"script": "hash"},
            "endpoint": [p.TARGET_DATABASE, "::1", 5432],
            "utc_day": now.date().isoformat(),
            "window_start": now.isoformat(),
            "window_end": (now + timedelta(hours=1)).isoformat(),
        }

    def test_distinct_phase_approval_required(self):
        plan = self.plan()
        plan["owner_approval"]["apply"] = p.APPROVALS["preflight"]
        with self.assertRaisesRegex(p.VerificationFailure, "phase-approval"):
            p.validate_plan(plan, "apply")

    def test_unapproved_operation_list_refused(self):
        plan = self.plan()
        plan["operations"].append("stamp")
        with self.assertRaisesRegex(p.VerificationFailure, "preparation-operations"):
            p.validate_plan(plan, "rehearsal")

    def test_phase_artifact_binds_endpoint_and_sources(self):
        plan = self.plan()
        record = {
            "phase": "preflight",
            "run_id": p.RUN_ID,
            "execution_sha": plan["execution_sha"],
            "preparation_sha": plan["preparation_sha"],
            "sources": plan["sources"],
            "target": plan["target"],
            "endpoint": plan["endpoint"],
        }
        with patch.object(p, "source_manifest", return_value=plan["sources"]):
            p.verify_phase_record(record, plan, "preflight")
            record["endpoint"] = ["social_manager", "::1", 5432]
            with self.assertRaisesRegex(p.VerificationFailure, "phase-endpoint"):
                p.verify_phase_record(record, plan, "preflight")

    def test_apply_requires_rehearsal_artifact_before_connection(self):
        plan = self.plan()
        plan["preflight_sha256"] = "c" * 64
        args = SimpleNamespace(
            command="apply", approval_file="plan", allow_definition_application=True, allow_commit=True
        )
        preflight = {"phase": "preflight"}
        with (
            patch.object(
                p,
                "read_private",
                side_effect=[(plan, "planhash"), (preflight, plan["preflight_sha256"]), FileNotFoundError()],
            ),
            patch.object(p, "validate_plan"),
            patch.object(p, "idle_processes"),
            patch.object(p, "verify_phase_record"),
            patch.object(p, "connect") as connect,
        ):
            with self.assertRaises(FileNotFoundError):
                p.prepare(args, {"commit_attempted": False})
            connect.assert_not_called()

    def test_apply_requires_commit_flag_before_connection(self):
        args = SimpleNamespace(
            command="apply", approval_file="plan", allow_definition_application=True, allow_commit=False
        )
        with (
            patch.object(p, "read_private", return_value=(self.plan(), "hash")),
            patch.object(p, "validate_plan"),
            patch.object(p, "idle_processes"),
            patch.object(p, "connect") as connect,
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "commit-approval"):
                p.prepare(args, {"commit_attempted": False})
            connect.assert_not_called()

    def manifest_pair(self):
        c = {
            "runner_sha": "a" * 40,
            "target": [p.TARGET_HOST, p.TARGET_PORT, p.TARGET_DATABASE, p.RUNNER_ROLE],
            "schema": p.TARGET_SCHEMA,
            "server_identity": [p.TARGET_DATABASE, p.RUNNER_ROLE, "::1", 5432],
            "run_id": p.RUN_ID,
            "fixtures": p.FIXTURE_TENANT_IDS,
            "catalog_sha256": "catalog",
            "acl_manifest_sha256": "acl",
        }
        m = {
            "phase": "apply",
            "commit_acknowledged": True,
            "alembic": "absent",
            "run_id": p.RUN_ID,
            "sources": {"script": "hash"},
            "execution_sha": c["runner_sha"],
            "preparation_sha": "b" * 40,
            "target": [p.TARGET_HOST, p.TARGET_PORT, p.TARGET_DATABASE, p.DDL_OWNER],
            "endpoint": [p.TARGET_DATABASE, "::1", 5432],
            "catalog_parity_reviewed": True,
            "fixtures": [
                [p.FIXTURE_TENANT_IDS[label], slug, plan, cap, active] for label, slug, plan, cap, active in p.FIXTURES
            ],
            "prepared": {"catalog_sha256": "catalog", "acl_manifest_sha256": "acl"},
        }
        provenance = {"preparation_script": "b" * 40, "manifest_file": "apply.json", "manifest_sha256": "hash"}
        return m, c, provenance

    def test_real_manifest_hash_and_fields_are_checked(self):
        m, c, provenance = self.manifest_pair()
        with (
            patch.object(p, "verify_script_pin"),
            patch.object(p, "source_manifest", return_value=m["sources"]),
            patch.object(p, "read_private", return_value=(m, "different")),
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "preparation-manifest-hash"):
                p.verify_runner_manifest(provenance, c)

    def test_manifest_target_tamper_refused(self):
        m, c, provenance = self.manifest_pair()
        m["target"][2] = "social_manager"
        with (
            patch.object(p, "verify_script_pin"),
            patch.object(p, "source_manifest", return_value=m["sources"]),
            patch.object(p, "read_private", return_value=(m, "hash")),
        ):
            with self.assertRaisesRegex(p.VerificationFailure, "manifest-target"):
                p.verify_runner_manifest(provenance, c)

    def test_successful_manifest_binding(self):
        m, c, provenance = self.manifest_pair()
        with (
            patch.object(p, "verify_script_pin"),
            patch.object(p, "source_manifest", return_value=m["sources"]),
            patch.object(p, "read_private", return_value=(m, "hash")),
        ):
            self.assertEqual(p.verify_runner_manifest(provenance, c), m)

    def test_exception_after_commit_requires_reconciliation(self):
        args = SimpleNamespace(command="apply")

        def committed_then_failed(_args, status):
            status.update(commit_attempted=True, commit_acknowledged=True)
            raise p.VerificationFailure("persistence-failed")

        with (
            patch("argparse.ArgumentParser.parse_args", return_value=args),
            patch.object(p, "prepare", side_effect=committed_then_failed),
            patch.object(p, "emit") as emit,
        ):
            self.assertEqual(p.main(), 1)
        stop = emit.call_args.kwargs
        self.assertTrue(stop["commit_acknowledged"])
        self.assertTrue(stop["reconciliation_required"])


if __name__ == "__main__":
    unittest.main()


class InetAddressTests(unittest.TestCase):
    def test_postgresql_inet_host_masks(self):
        for value in ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128"):
            with self.subTest(value=value):
                self.assertTrue(p.loopback_server_address(value))
        for value in ("127.0.0.1/24", "::1/64", "192.0.2.1/32", "localhost", "invalid"):
            with self.subTest(value=value):
                self.assertFalse(p.loopback_server_address(value))


class DriverCatalogTypeTests(unittest.TestCase):
    def test_internal_char_columns_are_driver_independent(self):
        import inspect

        self.assertIn("c.relkind::text", inspect.getsource(p.user_objects))
        source = inspect.getsource(p.inspect_prepared)
        self.assertIn("t.typtype::text", source)
        self.assertIn("t.tgenabled::text", source)


class BuiltinPluginTests(unittest.TestCase):
    def test_builtin_modules_classes_and_instances_only(self):
        import types

        builtin = type("LegacyTmpdirPlugin", (), {"__module__": "_pytest.legacypath"})
        foreign = type("InjectedPlugin", (), {"__module__": "external_plugin"})
        for plugin in (types.ModuleType("_pytest.capture"), builtin, builtin()):
            self.assertTrue(r.builtin_pytest_plugin(plugin))
        for plugin in (types.ModuleType("third_party"), foreign, foreign(), types.ModuleType("_pytest_spoof")):
            self.assertFalse(r.builtin_pytest_plugin(plugin))


class RetainedPreparationEvidenceTests(NoNetworkTests):
    def records(self):
        import hashlib
        import subprocess

        head = p.git("rev-parse", "HEAD")
        sources = {
            path: hashlib.sha256(
                subprocess.check_output(["git", "-C", str(ROOT), "show", head + ":" + path])
            ).hexdigest()
            for path in p.source_manifest()
        }
        manifest = {
            "phase": "apply",
            "commit_acknowledged": True,
            "alembic": "absent",
            "preparation_sha": r.PREPARATION_PIN,
            "execution_sha": head,
            "sources": sources,
            "target": [p.TARGET_HOST, p.TARGET_PORT, p.TARGET_DATABASE, p.DDL_OWNER],
            "endpoint": [p.TARGET_DATABASE, "127.0.0.1/32", 5432],
            "catalog_parity_reviewed": True,
            "run_id": p.RUN_ID,
            "fixtures": [
                [p.FIXTURE_TENANT_IDS[label], slug, plan, cap, active] for label, slug, plan, cap, active in p.FIXTURES
            ],
            "prepared": {"catalog_sha256": "c" * 64, "acl_manifest_sha256": "d" * 64},
        }
        provenance = {
            "manifest_file": "apply.json",
            "manifest_sha256": "e" * 64,
            "preparation_script": r.PREPARATION_PIN,
        }
        approval = {
            "runner_sha": head,
            "target": [p.TARGET_HOST, p.TARGET_PORT, p.TARGET_DATABASE, p.RUNNER_ROLE],
            "schema": p.TARGET_SCHEMA,
            "server_identity": [p.TARGET_DATABASE, p.RUNNER_ROLE, "127.0.0.1/32", 5432],
            "run_id": p.RUN_ID,
            "fixtures": p.FIXTURE_TENANT_IDS,
            **manifest["prepared"],
        }
        return manifest, provenance, approval

    def test_reader_only_change_retains_original_evidence(self):
        manifest, provenance, approval = self.records()
        with patch.object(p, "read_private", return_value=(manifest, "e" * 64)):
            self.assertIs(r.verify_preparation_evidence(p, provenance, approval), manifest)

    def test_definition_or_preparation_drift_stops(self):
        manifest, provenance, approval = self.records()
        current = p.source_manifest()
        current[p.SCRIPT_PATH] = "0" * 64
        with (
            patch.object(p, "read_private", return_value=(manifest, "e" * 64)),
            patch.object(p, "source_manifest", return_value=current),
        ):
            with self.assertRaisesRegex(r.Blocked, "manifest-definition-or-preparation-drift"):
                r.verify_preparation_evidence(p, provenance, approval)

    def test_changed_catalog_query_stops(self):
        manifest, provenance, approval = self.records()
        with (
            patch.object(p, "read_private", return_value=(manifest, "e" * 64)),
            patch.object(r, "CATALOG_SQL", r.CATALOG_SQL + " "),
        ):
            with self.assertRaisesRegex(r.Blocked, "manifest-catalog-query-drift"):
                r.verify_preparation_evidence(p, provenance, approval)

    def test_unrelated_reader_history_stops(self):
        manifest, provenance, approval = self.records()
        real_run = r.subprocess.run

        def only_ancestry_fails(args, **kwargs):
            if "merge-base" in args:
                return SimpleNamespace(returncode=1)
            return real_run(args, **kwargs)

        with (
            patch.object(p, "read_private", return_value=(manifest, "e" * 64)),
            patch.object(r.subprocess, "run", side_effect=only_ancestry_fails),
        ):
            with self.assertRaisesRegex(r.Blocked, "preparation-not-runner-ancestor"):
                r.verify_preparation_evidence(p, provenance, approval)
