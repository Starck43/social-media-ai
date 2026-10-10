"""Small isolated first-contact recovery checks; no app/DB/network imports.

Run: python test_agent_session_first_contact.py /path/to/source-copy
Extract actual get_or_create and its optional conflict classifier. A uniqueness
storage double models a committed competing winner and adapter diagnostics.
This does not certify PostgreSQL transactions; DB checks remain owner-only.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__" and len(sys.argv) > 1:
    ROOT = Path(sys.argv.pop(1))
CHAT = {"channel": "web", "chat_id": "synthetic-chat"}


class IsolatedIntegrityError(Exception):
    def __init__(self, original):
        super().__init__("synthetic database constraint error")
        self.orig = original


def conflict(original=None):
    if original is None:
        original = SimpleNamespace(sqlstate="23505", constraint_name="uq_agent_session_chat")
    return IsolatedIntegrityError(original)


def load_method():
    path = ROOT / "app/models/managers/agent_session_manager.py"
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AgentSessionManager")
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_or_create")
    helpers = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_is_session_chat_conflict"
    ]
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *helpers, method],
        type_ignores=[],
    )
    namespace = {"IntegrityError": IsolatedIntegrityError}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["get_or_create"]


class Storage:
    def __init__(self, existing=None, *, race=False, error=None, winner=None, visible=True):
        self.row = deepcopy(existing)
        self.race = race
        self.error = error
        self.winner = deepcopy(winner)
        self.visible = visible
        self.reads = []
        self.creates = []
        self.updates = []
        self.both_read = asyncio.Event()

    async def get(self, **filters):
        assert filters == CHAT, "recovery must use the original scoped lookup shape"
        snapshot = deepcopy(self.row) if self.visible else None
        self.reads.append(filters)
        if self.race and len(self.reads) <= 2:
            if len(self.reads) == 2:
                self.both_read.set()
            await self.both_read.wait()
        return snapshot

    async def create(self, **values):
        self.creates.append(values)
        if self.error is not None:
            self.row = deepcopy(self.winner)
            raise self.error
        if self.row is not None:
            raise conflict()
        self.row = SimpleNamespace(id=1, **values)
        return deepcopy(self.row)

    async def update_by_id(self, ident, **values):
        assert ident == self.row.id
        self.updates.append((ident, values))
        for key, value in values.items():
            setattr(self.row, key, value)
        return deepcopy(self.row)


class FirstContactRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.method = load_method()

    async def recovered(self, store, **extra):
        try:
            return await self.method(store, **CHAT, **extra)
        except IsolatedIntegrityError:
            self.fail("named chat conflict was not recovered from its visible committed winner")

    async def test_concurrent_first_contact_returns_same_session_to_both(self):
        store = Storage(race=True)
        results = await asyncio.wait_for(
            asyncio.gather(self.method(store, **CHAT), self.method(store, **CHAT), return_exceptions=True), 2
        )
        self.assertEqual([result for result in results if isinstance(result, BaseException)], [])
        self.assertEqual([result.id for result in results], [1, 1])
        self.assertEqual(len(store.creates), 2)
        self.assertEqual(len(store.reads), 3)

    async def test_named_conflict_recovers_visible_winner_and_upgrades_owner(self):
        winner = SimpleNamespace(id=7, is_owner=False)
        error = conflict(SimpleNamespace(pgcode="23505", diag=SimpleNamespace(constraint_name="uq_agent_session_chat")))
        store = Storage(error=error, winner=winner)
        result = await self.recovered(store, is_owner=True)
        self.assertEqual(result.id, 7)
        self.assertTrue(result.is_owner)
        self.assertEqual(store.updates, [(7, {"is_owner": True})])
        self.assertEqual(store.reads, [CHAT, CHAT])

    async def test_asyncpg_cause_diagnostics_recover_without_error_text(self):
        original = SimpleNamespace(sqlstate="23505", __cause__=SimpleNamespace(constraint_name="uq_agent_session_chat"))
        store = Storage(error=conflict(original), winner=SimpleNamespace(id=7, is_owner=True))
        result = await self.recovered(store)
        self.assertTrue(result.is_owner)
        self.assertEqual(store.updates, [])
        self.assertEqual(store.reads, [CHAT, CHAT])

    async def test_other_or_unidentified_integrity_errors_are_not_swallowed(self):
        cases = [
            SimpleNamespace(sqlstate="23505", constraint_name="different_constraint"),
            SimpleNamespace(sqlstate="23502", constraint_name="uq_agent_session_chat"),
            SimpleNamespace(sqlstate="23505"),
            SimpleNamespace(constraint_name="uq_agent_session_chat"),
        ]
        for original in cases:
            with self.subTest(original=vars(original)):
                error = conflict(original)
                store = Storage(error=error, winner=SimpleNamespace(id=7, is_owner=False))
                with self.assertRaises(IsolatedIntegrityError) as caught:
                    await self.method(store, **CHAT)
                self.assertIs(caught.exception, error)
                self.assertEqual(store.reads, [CHAT])
                self.assertEqual(store.updates, [])

    async def test_invisible_or_deleted_winner_is_not_adopted_or_retried(self):
        error = conflict()
        store = Storage(error=error, winner=SimpleNamespace(id=7, is_owner=False), visible=False)
        with self.assertRaises(IsolatedIntegrityError) as caught:
            await self.method(store, **CHAT, is_owner=True)
        self.assertIs(caught.exception, error)
        self.assertEqual(store.reads, [CHAT, CHAT])
        self.assertEqual(len(store.creates), 1)
        self.assertEqual(store.updates, [])

    async def test_non_integrity_failure_propagates_without_recovery_read(self):
        error = RuntimeError("synthetic unrelated failure")
        store = Storage(error=error)
        with self.assertRaises(RuntimeError) as caught:
            await self.method(store, **CHAT)
        self.assertIs(caught.exception, error)
        self.assertEqual(store.reads, [CHAT])

    async def test_existing_owner_is_never_downgraded(self):
        store = Storage(SimpleNamespace(id=7, is_owner=True))
        self.assertTrue((await self.method(store, **CHAT, is_owner=False)).is_owner)
        self.assertEqual(store.creates, [])
        self.assertEqual(store.updates, [])

    async def test_normal_create_defaults_and_existing_owner_upgrade_are_preserved(self):
        store = Storage()
        result = await self.method(store, **CHAT)
        self.assertEqual(
            store.creates, [{**CHAT, "kind": "private", "is_owner": False, "is_active": True, "state": {}}]
        )
        self.assertEqual(result.id, 1)
        store = Storage(SimpleNamespace(id=7, is_owner=False))
        self.assertTrue((await self.method(store, **CHAT, is_owner=True)).is_owner)
        self.assertEqual(store.creates, [])
        self.assertEqual(store.updates, [(7, {"is_owner": True})])


if __name__ == "__main__":
    unittest.main(verbosity=2)
