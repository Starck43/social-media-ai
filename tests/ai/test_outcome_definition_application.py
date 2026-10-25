"""Small isolated runner checks; no DB, pytest, DDL or app settings."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
M = load_isolated_source("_application_test", ROOT / "scripts/apply_llm_outcome_definition.py")
REV = load_isolated_source(
    "_revision_test",
    ROOT / "migrations/versions/0094_add_llm_outcome_events.py",
    imports={"app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="test_schema"))},
)
DEF = M.capture(REV, "test_schema")


class Helpers(unittest.TestCase):
    def test_capture_complete_and_unbound(self):
        self.assertEqual(len(DEF.table.columns), 17)
        self.assertEqual(len(DEF.sql), 5)
        self.assertEqual(set(M.function_bodies(DEF.sql)), M.FUNCTIONS)

    def test_capture_restores_operations(self):
        old = REV.op
        M.capture(REV, "test_schema")
        self.assertIs(REV.op, old)

    def test_wrong_schema_blocks(self):
        with self.assertRaises(M.VerificationFailure):
            M.capture(REV, "other")

    def test_process_guard_excludes_only_own_ancestors(self):
        self.assertEqual(M.competing_processes("1 0 init\n10 1 apply_llm\n20 10 apply_llm\n30 1 pytest", 20), [30])

    def test_partial_objects_block(self):
        M.absent({"objects": []})
        with self.assertRaises(M.VerificationFailure):
            M.absent({"objects": ["partial"]})

    def test_failure_states_distinguish_uncertain_commit(self):
        for flags, expected in [
            ((False, False), "BLOCKED_OR_FAILED"),
            ((True, False), "APPLICATION_OUTCOME_UNCERTAIN"),
            ((True, True), "COMMITTED_NEEDS_REVIEW"),
        ]:
            self.assertEqual(M.failure_status(dict(commit_attempted=flags[0], commit_acknowledged=flags[1])), expected)


def valid_catalog():
    bodies = M.function_bodies(DEF.sql)
    return {
        "functions": [
            dict(
                proname=n,
                prosrc=b,
                pronargs=0,
                prosecdef=False,
                proconfig=None,
                provolatile="v",
                proparallel="u",
                proleakproof=False,
                returns="trigger",
                lanname="plpgsql",
                owner="starck",
            )
            for n, b in bodies.items()
        ],
        "triggers": [
            dict(
                tgname=n,
                tgtype=p[0],
                proname=p[1],
                enabled="O",
                function_schema="test_schema",
                no_when=True,
                tgnargs=0,
                args="",
                columns="",
                tgconstraint=0,
            )
            for n, p in M.TRIGGERS.items()
        ],
    }


class Guards(unittest.TestCase):
    def test_expected_guards_accept(self):
        M.validate_guards(valid_catalog(), M.function_bodies(DEF.sql), "test_schema", "starck")

    def test_altered_function_or_security_blocks(self):
        for field, value in [
            ("prosrc", "RETURN NEW;"),
            ("prosecdef", True),
            ("owner", "other"),
            ("proconfig", ["search_path=public"]),
        ]:
            actual = valid_catalog()
            actual["functions"][0][field] = value
            with self.subTest(field=field), self.assertRaises(M.VerificationFailure):
                M.validate_guards(actual, M.function_bodies(DEF.sql), "test_schema", "starck")

    def test_disabled_conditional_or_rebound_trigger_blocks(self):
        for field, value in [
            ("enabled", "D"),
            ("tgtype", 19),
            ("no_when", False),
            ("function_schema", "public"),
            ("tgnargs", 1),
        ]:
            actual = valid_catalog()
            actual["triggers"][0][field] = value
            with self.subTest(field=field), self.assertRaises(M.VerificationFailure):
                M.validate_guards(actual, M.function_bodies(DEF.sql), "test_schema", "starck")


class Connection:
    def __init__(self, events, fail_commit=False):
        self.events = events
        self.fail_commit = fail_commit
        self.is_active = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execution_options(self, **options):
        self.events.append(("isolation", options))
        return self

    async def begin(self):
        return self

    async def commit(self):
        self.events.append("commit")
        self.is_active = False
        if self.fail_commit:
            raise RuntimeError("ack lost")

    async def rollback(self):
        self.events.append("rollback")
        self.is_active = False

    async def run_sync(self, function):
        return function(self)

    async def execute(self, statement):
        return SimpleNamespace(scalar_one=lambda: 0)


class Transactions(unittest.IsolatedAsyncioTestCase):
    async def exercise(
        self, commit=False, reference=None, drift=False, partial=False, parity_error=False, fail_commit=False
    ):
        self.events = []
        self.state = dict(commit_attempted=False, commit_acknowledged=False)
        conn = Connection(self.events, fail_commit)
        engine = SimpleNamespace(connect=lambda: conn)
        args = SimpleNamespace(expected_schema="test_schema", expected_role="starck")
        baseline = {"protected": "contents"}
        before = valid_catalog() if partial else dict(functions=[], triggers=[])

        def apply(*args):
            self.events.append("upgrade")

        def parity(*args):
            self.events.append("parity")
            if parity_error:
                raise RuntimeError("parity failed")

        with (
            patch.object(M, "idle_processes"),
            patch.object(M, "settings_and_idle", new=AsyncMock()),
            patch.object(M, "snapshot", new=AsyncMock(return_value={} if drift else baseline)),
            patch.object(M, "catalog", new=AsyncMock(side_effect=[before, valid_catalog()])),
            patch.object(M, "apply_revision", new=apply),
            patch.object(M, "reflected_shape", new=parity),
        ):
            return await M.application_transaction(
                engine, REV, None, DEF, args, self.state, commit=commit, baseline=baseline, reference=reference
            )

    async def test_rehearsal_rolls_back_without_commit(self):
        await self.exercise()
        self.assertEqual(self.events[1:], ["upgrade", "parity", "rollback"])
        self.assertFalse(self.state["commit_attempted"])

    async def test_commit_follows_parity_and_reference(self):
        await self.exercise(commit=True, reference=valid_catalog())
        self.assertEqual(self.events[1:], ["upgrade", "parity", "commit"])
        self.assertTrue(self.state["commit_acknowledged"])

    async def test_parity_failure_rolls_back(self):
        with self.assertRaises(RuntimeError):
            await self.exercise(commit=True, parity_error=True)
        self.assertEqual(self.events[-1], "rollback")
        self.assertNotIn("commit", self.events)

    async def test_reference_difference_denies_commit(self):
        with self.assertRaises(M.VerificationFailure):
            await self.exercise(commit=True, reference={"changed": True})
        self.assertEqual(self.events[-1], "rollback")
        self.assertFalse(self.state["commit_attempted"])

    async def test_drift_denies_before_upgrade(self):
        with self.assertRaises(M.VerificationFailure):
            await self.exercise(drift=True)
        self.assertNotIn("upgrade", self.events)

    async def test_partial_objects_deny_reapply(self):
        with self.assertRaises(M.VerificationFailure):
            await self.exercise(partial=True)
        self.assertNotIn("upgrade", self.events)

    async def test_commit_ack_loss_never_retries_ddl(self):
        with self.assertRaisesRegex(RuntimeError, "ack lost"):
            await self.exercise(commit=True, reference=valid_catalog(), fail_commit=True)
        self.assertEqual(self.events.count("upgrade"), 1)
        self.assertFalse(self.state["commit_acknowledged"])
        self.assertEqual(M.failure_status(self.state), "APPLICATION_OUTCOME_UNCERTAIN")


class Pipeline(unittest.IsolatedAsyncioTestCase):
    async def test_failed_rollback_absence_check_prevents_commit_stage(self):
        engine = SimpleNamespace(dispose=AsyncMock())
        dependencies = {
            "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="test_schema", DEBUG=False)),
            "app.core.database": SimpleNamespace(async_engine=engine),
            "app.models": SimpleNamespace(LLMOutcomeEvent=SimpleNamespace(__table__=DEF.table)),
        }
        application = AsyncMock(return_value=valid_catalog())
        inspections = AsyncMock(side_effect=[({"tables": {}}, {}), RuntimeError("rollback objects remain")])
        with (
            patch.dict(M.__isolated_imports__, dependencies),
            patch.object(M, "load_revision", return_value=REV),
            patch.object(M, "capture", return_value=DEF),
            patch.object(M, "application_transaction", new=application),
            patch.object(M, "inspect_state", new=inspections),
            patch.object(M, "emit"),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback objects remain"):
                await M.run(SimpleNamespace(expected_schema="test_schema"), {})
        self.assertEqual(application.await_count, 1)
        self.assertFalse(application.call_args.kwargs["commit"])
        engine.dispose.assert_awaited_once()

    async def test_postcommit_catalog_failure_does_not_retry_or_cleanup(self):
        engine = SimpleNamespace(dispose=AsyncMock())
        dependencies = {
            "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="test_schema", DEBUG=False)),
            "app.core.database": SimpleNamespace(async_engine=engine),
            "app.models": SimpleNamespace(LLMOutcomeEvent=SimpleNamespace(__table__=DEF.table)),
        }
        application = AsyncMock(return_value=valid_catalog())
        inspections = AsyncMock(side_effect=[({"tables": {}}, {}), ({}, {}), ({}, {"changed": True})])
        with (
            patch.dict(M.__isolated_imports__, dependencies),
            patch.object(M, "load_revision", return_value=REV),
            patch.object(M, "capture", return_value=DEF),
            patch.object(M, "application_transaction", new=application),
            patch.object(M, "inspect_state", new=inspections),
            patch.object(M, "emit"),
        ):
            with self.assertRaises(M.VerificationFailure):
                await M.run(SimpleNamespace(expected_schema="test_schema"), {})
        self.assertEqual([call.kwargs["commit"] for call in application.call_args_list], [False, True])
        engine.dispose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
