"""Actual manager methods with transaction doubles; no app/DB imports.

Run from the repository root with .venv/bin/python tests/test_confirmation_consumption.py.
These checks model serialization and commit acknowledgement, not PostgreSQL acceptance.
"""

from __future__ import annotations

import ast
import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name, *, class_name=None, scope=None):
    path = ROOT / "app/models/managers/agent_session_manager.py"
    nodes = ast.parse(path.read_text()).body
    if class_name:
        nodes = next(node.body for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next(
        node for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    node.decorator_list = []
    tree = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    namespace = dict(scope or {})
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), namespace)
    return namespace[name]


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return self.name, value

    def is_(self, value):
        return self.name, value


class Statement:
    def __init__(self, kind):
        self.kind, self.criteria, self.locked, self.payload, self.options = kind, (), False, {}, {}

    def where(self, *criteria):
        self.criteria = criteria
        return self

    def with_for_update(self):
        self.locked = True
        return self

    def values(self, **payload):
        self.payload = payload
        return self

    def execution_options(self, **options):
        self.options = options
        return self


class Store:
    def __init__(self, state, *, missing=False, active=True):
        self.state, self.missing, self.active = deepcopy(state), missing, active
        self.model = SimpleNamespace(id=Field("id"), state=Field("state"), is_active=Field("is_active"))
        self.lock, self.statements = asyncio.Lock(), []
        self.scope = 7
        self.commit_error = None
        self.commit_entered, self.commit_release = asyncio.Event(), asyncio.Event()
        self.commit_release.set()
        self.rollbacks = 0
        self.snapshot = load("_confirmation_snapshot", scope={"json": json})
        self.consume_method = load(
            "consume_pending_confirmation",
            class_name="AgentSessionManager",
            scope={
                "_confirmation_snapshot": self.snapshot,
                "select": lambda *args: Statement("select"),
                "update": lambda *args: Statement("update"),
            },
        )

    def _tenant_criterion(self):
        if self.scope is None:
            raise RuntimeError("missing tenant")
        return [("tenant_id", self.scope)]

    @asynccontextmanager
    async def _get_owned_session(self):
        held, tentative = False, None

        async def execute(statement):
            nonlocal held, tentative
            self.statements.append(statement)
            if statement.criteria != (("id", 1), ("tenant_id", self.scope), ("is_active", True)):
                raise AssertionError("session/tenant/active predicates changed")
            if statement.kind == "select":
                if not statement.locked:
                    raise AssertionError("read must lock")
                await self.lock.acquire()
                held = True
                await asyncio.sleep(0)
                row = None if self.missing or not self.active or self.scope != 7 else (deepcopy(self.state),)
                return SimpleNamespace(first=lambda: row)
            if not held or statement.options.get("synchronize_session") is not False:
                raise AssertionError("unlocked or stale identity-map update")
            tentative = deepcopy(statement.payload["state"])

        try:
            yield SimpleNamespace(execute=execute)
            self.commit_entered.set()
            await self.commit_release.wait()
            if self.commit_error is not None:
                raise self.commit_error
            if tentative is not None:
                self.state = tentative
        except BaseException:
            self.rollbacks += 1
            raise
        finally:
            if held:
                self.lock.release()

    async def consume(self, expected):
        return await self.consume_method(self, 1, expected)


class ConfirmationConsumptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pending = {"intent_id": "a" * 32, "args": {"source_id": 9}}

    async def test_two_consumers_have_one_winner_and_preserve_unrelated_keys(self):
        store = Store({"pending_confirmation": self.pending, "keep": "fresh", "update_offset": 0})
        results = await asyncio.wait_for(
            asyncio.gather(
                store.consume(deepcopy(self.pending)),
                store.consume(deepcopy(self.pending)),
            ),
            2,
        )
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(store.state, {"keep": "fresh", "update_offset": 0})
        self.assertEqual([statement.kind for statement in store.statements].count("update"), 1)

    async def test_result_is_not_returned_before_owned_commit(self):
        store = Store({"pending_confirmation": self.pending})
        store.commit_release.clear()
        task = asyncio.create_task(store.consume(self.pending))
        try:
            await asyncio.wait_for(store.commit_entered.wait(), 1)
            self.assertFalse(task.done())
            self.assertIn("pending_confirmation", store.state)
            store.commit_release.set()
            self.assertEqual(await asyncio.wait_for(task, 1), {})
        finally:
            store.commit_release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_commit_failure_propagates_and_never_returns_authority(self):
        store = Store({"pending_confirmation": self.pending})
        store.commit_error = RuntimeError("synthetic commit failure")
        with self.assertRaises(RuntimeError):
            await store.consume(self.pending)
        self.assertEqual(store.state, {"pending_confirmation": self.pending})
        self.assertEqual(store.rollbacks, 1)

    async def test_cancelled_commit_does_not_return_authority(self):
        store = Store({"pending_confirmation": self.pending})
        store.commit_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await store.consume(self.pending)
        self.assertEqual(store.state, {"pending_confirmation": self.pending})
        self.assertEqual(store.rollbacks, 1)

    async def test_replacement_with_identical_arguments_is_not_consumed(self):
        replacement = {**self.pending, "intent_id": "b" * 32}
        store = Store({"pending_confirmation": replacement, "keep": 1})
        self.assertIsNone(await store.consume(self.pending))
        self.assertEqual(store.state["pending_confirmation"], replacement)
        self.assertEqual([statement.kind for statement in store.statements], ["select"])

    async def test_changed_arguments_or_binding_refuse_exact_snapshot(self):
        for change in ({"args": {"source_id": 10}}, {"actor": 12}, {"expires_at": "changed"}):
            with self.subTest(change=change):
                store = Store({"pending_confirmation": {**self.pending, **change}})
                self.assertIsNone(await store.consume(self.pending))
                self.assertEqual([statement.kind for statement in store.statements], ["select"])

    async def test_replay_after_success_cannot_consume_again(self):
        store = Store({"pending_confirmation": self.pending})
        self.assertEqual(await store.consume(self.pending), {})
        self.assertIsNone(await store.consume(self.pending))

    async def test_missing_inactive_null_and_absent_pending_rows_do_not_write(self):
        for state, options in (
            ({"pending_confirmation": self.pending}, {"missing": True}),
            ({"pending_confirmation": self.pending}, {"active": False}),
            (None, {}),
            ({}, {}),
            ({"pending_confirmation": "malformed"}, {}),
        ):
            with self.subTest(state=state, options=options):
                store = Store(state, **options)
                self.assertIsNone(await store.consume(self.pending))
                self.assertEqual([statement.kind for statement in store.statements], ["select"])

    async def test_missing_and_foreign_tenant_cannot_consume(self):
        store = Store({"pending_confirmation": self.pending})
        store.scope = None
        with self.assertRaises(RuntimeError):
            await store.consume(self.pending)
        self.assertEqual(store.statements, [])
        store.scope = 8
        self.assertIsNone(await store.consume(self.pending))
        self.assertEqual(store.state, {"pending_confirmation": self.pending})

    async def test_malformed_expected_snapshot_refuses_before_io(self):
        store = Store({"pending_confirmation": self.pending})
        for expected in (None, [], {"nan": float("nan")}, {"object": object()}):
            self.assertIsNone(await store.consume(expected))
        self.assertEqual(store.statements, [])

    async def test_json_snapshot_does_not_equate_true_and_one(self):
        store = Store({"pending_confirmation": {**self.pending, "actor": True}})
        self.assertIsNone(await store.consume({**self.pending, "actor": 1}))

    async def test_external_transaction_argument_is_not_supported(self):
        store = Store({"pending_confirmation": self.pending})
        with self.assertRaises(TypeError):
            await store.consume_method(store, 1, self.pending, session=object())
        self.assertEqual(store.statements, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)