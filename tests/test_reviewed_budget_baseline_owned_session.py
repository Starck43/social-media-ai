"""Source-review follow-up: real owned helper + fake sessions; offline SQL only.

No app import, DBAPI/engine, migrations/env.py, configuration or real PG call.
The original 36 checks are imported as harness, not collected or rerun here.
"""

import ast
import asyncio
import io
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

import test_reviewed_budget_baselines as harness
from alembic.migration import MigrationContext
from alembic.operations import Operations
from source_import_isolation import load_isolated_source
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

FACTORY = {"session": None, "calls": 0}


def fake_session_factory():
    FACTORY["calls"] += 1
    return FACTORY["session"]


# Extract and compile the exact helper AST; never import BaseManager's DB graph.
source = ast.parse((harness.ROOT / "app/models/managers/base_manager.py").read_text())
helper_node = next(
    n for n in ast.walk(source) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_get_owned_session"
)
namespace = {
    "asynccontextmanager": asynccontextmanager,
    "AsyncSession": object,
    "async_session_maker": fake_session_factory,
}
exec(compile(ast.Module(body=[helper_node], type_ignores=[]), "<actual-owned-session-helper>", "exec"), namespace)
actual_owned_helper = namespace["_get_owned_session"]


class ProtocolSession(harness.OwnedDouble):
    def __init__(self):
        super().__init__(harness.MODELS.LLMBudgetBaseline)
        self.entered = 0
        self.exited = 0
        self.rollback_calls = 0
        self.commit_calls = 0
        self.commit_started = None
        self.ack_gate = None

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *args):
        self.exited += 1
        self.pending.clear()  # fake resource close, not a claim of PG rollback

    async def commit(self):
        self.commit_calls += 1
        if self.commit_started is not None:
            self.commit_started.set()
            await self.ack_gate.wait()
        if self.commit_error == "before":
            raise RuntimeError("synthetic failure before commit")
        for name, value in self.pending:
            if name == "llm_budget_days":
                self.day = SimpleNamespace(**value)
            else:
                self.baseline = SimpleNamespace(**value)
        self.durable.extend(self.pending)
        self.pending.clear()
        self.commits += 1
        if self.commit_error == "after":
            raise RuntimeError("synthetic lost acknowledgement")

    async def rollback(self):
        self.rollback_calls += 1
        self.pending.clear()
        if self.rollback_error:
            raise RuntimeError("synthetic rollback failure")


class ActualOwnedManager(harness.MANAGER.LLMBudgetManager):
    _get_owned_session = actual_owned_helper


class ActualOwnedSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        harness.SCOPE["id"] = 7
        self.session = ProtocolSession()
        FACTORY.update(session=self.session, calls=0)
        self.manager = ActualOwnedManager()
        self.value = harness.packet()

    async def provision(self):
        return await self.manager.provision_reviewed_day(self.value)

    async def test_actual_helper_owns_factory_commit_and_resource_exit(self):
        self.assertTrue(await self.provision())
        self.assertEqual(FACTORY["calls"], 1)
        self.assertEqual(self.session.entered, 1)
        self.assertEqual(self.session.exited, 1)
        self.assertEqual(self.session.commit_calls, 1)
        self.assertEqual(self.session.rollback_calls, 0)
        self.assertEqual([name for name, _ in self.session.durable], ["llm_budget_days", "llm_budget_baselines"])

    async def test_no_true_return_before_owned_commit_acknowledgement(self):
        self.session.commit_started = asyncio.Event()
        self.session.ack_gate = asyncio.Event()
        task = asyncio.create_task(self.provision())
        await self.session.commit_started.wait()
        try:
            self.assertFalse(task.done())
            self.assertEqual(self.session.durable, [])
        finally:
            self.session.ack_gate.set()
        self.assertTrue(await task)
        self.assertEqual(self.session.commit_calls, 1)

    async def test_second_insert_failure_actual_helper_rolls_back_first(self):
        self.session.fail_at = "llm_budget_baselines"
        with self.assertRaises(IntegrityError):
            await self.provision()
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.exited, 1)
        self.assertEqual(self.session.durable, [])

    async def test_pre_commit_failure_is_not_success_and_calls_rollback(self):
        self.session.commit_error = "before"
        with self.assertRaisesRegex(RuntimeError, "before commit"):
            await self.provision()
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.durable, [])

    async def test_post_commit_ack_loss_never_true_and_exact_replay_never_reinserts(self):
        self.session.commit_error = "after"
        with self.assertRaisesRegex(RuntimeError, "lost acknowledgement"):
            await self.provision()
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(len(self.session.durable), 2)
        self.session.commit_error = None
        self.assertFalse(await self.provision())
        self.assertEqual(FACTORY["calls"], 2)
        self.assertEqual(len(self.session.durable), 2)

    async def test_exact_replay_commit_failure_does_not_return_false(self):
        self.assertTrue(await self.provision())
        self.session.commit_error = "before"
        with self.assertRaisesRegex(RuntimeError, "before commit"):
            await self.provision()
        self.assertEqual(len(self.session.durable), 2)

    async def test_rollback_failure_propagates_resource_still_exits(self):
        self.session.fail_at = "llm_budget_baselines"
        self.session.rollback_error = True
        with self.assertRaisesRegex(RuntimeError, "rollback failure"):
            await self.provision()
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.exited, 1)
        self.assertEqual(self.session.commit_calls, 0)

    async def test_cancel_does_not_commit_and_exits_owned_resource(self):
        self.session.fail_at = "cancel"
        with self.assertRaises(asyncio.CancelledError):
            await self.provision()
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.exited, 1)
        # Real AsyncSession close/rollback and server lock release remain PG acceptance.

    async def test_caller_cannot_supply_ambient_session(self):
        with self.assertRaises(TypeError):
            await self.manager.provision_reviewed_day(self.value, session=self.session)
        self.assertEqual(FACTORY["calls"], 0)


class OfflineDefinitionTests(unittest.TestCase):
    def render(self, schema):
        buffer = io.StringIO()
        context = MigrationContext.configure(
            dialect=postgresql.dialect(), opts={"as_sql": True, "output_buffer": buffer}
        )
        module = load_isolated_source(
            "_baseline_offline_ddl",
            harness.ROOT / "migrations/versions/0096_add_reviewed_budget_baselines.py",
            imports={
                "alembic": SimpleNamespace(op=Operations(context)),
                "app.core.config": SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA=schema)),
            },
        )
        module.upgrade()  # offline emitter only; no engine/DBAPI/env.py or online upgrade
        return buffer.getvalue()

    def test_real_alembic_offline_emitter_renders_only_new_schema_objects(self):
        sql = self.render("baseline_review")
        self.assertIn("CREATE TABLE baseline_review.llm_budget_baselines", sql)
        self.assertEqual(sql.count("CREATE TABLE "), 1)
        self.assertEqual(sql.count("CREATE FUNCTION "), 2)
        self.assertEqual(sql.count("CREATE TRIGGER "), 3)
        self.assertIn("REFERENCES baseline_review.llm_budget_days (tenant_id, utc_day) ON DELETE RESTRICT", sql)
        self.assertIn("uq_llm_budget_baselines_payer_day UNIQUE (tenant_id, utc_day)", sql)
        self.assertIn("NUMERIC(18, 9)", sql)
        self.assertIn("TIMESTAMP WITH TIME ZONE", sql)
        self.assertIn("CHECK (frozen)", sql)
        self.assertNotIn("CREATE TABLE baseline_review.llm_budget_days", sql)
        self.assertNotIn("INSERT INTO", sql)
        self.assertNotIn("ALTER TABLE", sql)
        self.assertNotIn("DROP ", sql)
        self.assertNotIn("alembic_version", sql)  # definition only, not normal env execution

    def test_nondefault_quoted_schema_applies_to_table_functions_triggers_and_fk(self):
        sql = self.render('review"lane')
        prefix = '"review""lane".'
        for name in (
            "llm_budget_baselines",
            "llm_budget_days",
            "reject_llm_budget_baseline_mutation",
            "check_llm_budget_baseline_day",
        ):
            self.assertIn(prefix + name, sql)
        self.assertNotIn("public.", sql)
        self.assertNotIn("baseline_review.", sql)
