"""Prepared helper/statement probes for session-state-key-patch.patch.

Run: python test_session_state_key_patch.py /path/to/patched-source
NOT executed remotely. No app, SQLAlchemy, DB or network imports.
Storage and transaction doubles validate algorithm/statement intent only;
PostgreSQL lock/commit behavior requires separate owner-run DB evidence.
The original three red probes' evidence does not transfer to these checks.
"""
from __future__ import annotations
import ast
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path.cwd()
MANAGER = 'app/models/managers/agent_session_manager.py'
RUNTIME = 'app/agent/runtime.py'


def load(path, name, cls=None, scope=None):
    nodes = ast.parse((ROOT/path).read_text()).body
    if cls:
        nodes = next(n.body for n in nodes if isinstance(n, ast.ClassDef) and n.name == cls)
    node = next(n for n in nodes if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name)
    node.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    namespace = dict(scope or {})
    exec(compile(ast.fix_missing_locations(module), str(ROOT/path), 'exec'), namespace)
    return namespace[name]


class Field:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return (self.name, value)


class Statement:
    def __init__(self, kind):
        self.kind, self.criteria, self.locked, self.payload, self.options = kind, (), False, {}, {}
    def where(self, *criteria): self.criteria = criteria; return self
    def with_for_update(self): self.locked = True; return self
    def values(self, **payload): self.payload = payload; return self
    def execution_options(self, **options): self.options = options; return self


class Transaction:
    def __init__(self, store): self.store, self.held = store, False
    async def execute(self, stmt):
        self.store.statements.append(stmt)
        expected = (('id', 1), ('tenant_id', 7))
        if stmt.criteria != expected:
            raise AssertionError('tenant/id predicate not preserved')
        if stmt.kind == 'select':
            if not stmt.locked: raise AssertionError('read must acquire row lock')
            await self.store.lock.acquire(); self.held = True
            await asyncio.sleep(0)  # Permit competing writer to enter the lock wait.
            row = None if self.store.missing else (deepcopy(self.store.state),)
            return SimpleNamespace(first=lambda: row)
        if not self.held: raise AssertionError('write happened without locked read')
        if stmt.options.get('synchronize_session') is not False:
            raise AssertionError('do not synchronize a stale ORM identity snapshot')
        self.store.state = deepcopy(stmt.payload['state'])
        return SimpleNamespace()
    def close(self):
        if self.held: self.store.lock.release(); self.held = False


class Store:
    def __init__(self, state=None, *, missing=False):
        self.state, self.missing = deepcopy(state), missing
        self.lock, self.statements = asyncio.Lock(), []
        self.model = SimpleNamespace(id=Field('id'), state=Field('state'))
        self.fail_scope = False
        self.implementation = load(MANAGER, 'patch_state', 'AgentSessionManager', {
            'select': lambda *args: Statement('select'), 'update': lambda *args: Statement('update')})
    def _tenant_criterion(self):
        if self.fail_scope: raise RuntimeError('tenant context absent')
        return [('tenant_id', 7)]
    async def patch_state(self, session_id, updates):
        transaction = Transaction(self)
        try: return await self.implementation(self, session_id, updates, session=transaction)
        finally: transaction.close()  # Simulates transaction end, not real DB evidence.


class KeyPatchProbes(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_manager_key_updates_preserve_both_keys(self):
        store = Store({'keep': 'existing'})
        merge = load(MANAGER, 'set_state', 'AgentSessionManager')
        await asyncio.wait_for(asyncio.gather(merge(store, 1, left=1), merge(store, 1, right=2)), 2)
        self.assertEqual(store.state, {'keep': 'existing', 'left': 1, 'right': 2})
        self.assertEqual(len(store.statements), 4)

    async def change_pending(self, *, clear):
        store = Store({'keep': 'newer unrelated value', 'pending_confirmation': {'id': 'old'}})
        snapshot = SimpleNamespace(id=1, state={'keep': 'stale value', 'pending_confirmation': {'id': 'old'}})
        helper = load(RUNTIME, '_patch_session_state')
        change = load(RUNTIME, '_clear_pending' if clear else '_set_pending', scope={'_patch_session_state': helper})
        module = ModuleType('app.models.managers.agent_session_manager'); module.agent_sessions = store
        with patch.dict(sys.modules, {'app.models.managers.agent_session_manager': module}):
            if clear: await change(snapshot)
            else: await change(snapshot, {'id': 'replacement'})
        self.assertEqual(store.state['keep'], 'newer unrelated value')
        self.assertEqual(store.state.get('pending_confirmation'), None if clear else {'id': 'replacement'})
        self.assertEqual(snapshot.state, store.state)

    async def test_stage_pending_preserves_unrelated_stored_key(self): await self.change_pending(clear=False)
    async def test_clear_pending_preserves_unrelated_stored_key(self): await self.change_pending(clear=True)

    async def test_none_removes_key_and_falsy_values_survive(self):
        store = Store({'remove': 1, 'keep': 2})
        result = await store.patch_state(1, {'remove': None, 'zero': 0, 'false': False, 'empty': ''})
        self.assertEqual(result, {'keep': 2, 'zero': 0, 'false': False, 'empty': ''})

    async def test_null_state_accepts_keys(self):
        self.assertEqual(await Store(None).patch_state(1, {'key': 'value'}), {'key': 'value'})

    async def test_missing_row_does_not_write_or_refresh_snapshot(self):
        store = Store({}, missing=True); snapshot = SimpleNamespace(id=1, state={'old': 1})
        helper = load(RUNTIME, '_patch_session_state')
        module = ModuleType('app.models.managers.agent_session_manager'); module.agent_sessions = store
        with patch.dict(sys.modules, {'app.models.managers.agent_session_manager': module}):
            await helper(snapshot, {'new': 2})
        self.assertEqual(snapshot.state, {'old': 1})
        self.assertEqual([s.kind for s in store.statements], ['select'])

    async def test_missing_tenant_scope_fails_before_database_statement(self):
        store = Store({}); store.fail_scope = True
        with self.assertRaisesRegex(RuntimeError, 'tenant context absent'):
            await store.patch_state(1, {'key': 1})
        self.assertEqual(store.statements, [])

    async def test_offset_helper_preserves_set_state_delegation(self):
        events = []
        async def set_state(session_id, **values): events.append((session_id, values))
        helper = load(MANAGER, 'set_update_offset', 'AgentSessionManager')
        await helper(SimpleNamespace(set_state=set_state), 1, 0)
        self.assertEqual(events, [(1, {'update_offset': 0})])


if __name__ == '__main__': unittest.main(verbosity=2)
