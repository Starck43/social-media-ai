"""Standalone actual-source staging COUNT contract; no app/bootstrap/DB imports."""
import ast
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('STAGED_COUNT_SOURCE', ROOT / 'app/models/managers/collected_item_manager.py'))

class Statement:
    def __init__(self, text):
        self.text = text
        self.binds = ()
    def bindparams(self, *binds):
        self.binds = binds
        return self

class JsonType:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

class Result:
    def __init__(self, rowcount=-1, value=0):
        self.rowcount = rowcount
        self.value = value
    def scalar(self):
        return self.value

class Session:
    """SQL/session double: checks the exact SELECT grammar before evaluation."""
    def __init__(self, persisted=(), rowcount=-1, null_scalar=False):
        self.persisted = list(persisted)
        self.rowcount = rowcount
        self.null_scalar = null_scalar
        self.calls = []
    async def execute(self, stmt, params):
        self.calls.append((stmt, params))
        if stmt.text.startswith('INSERT'):
            return Result(self.rowcount)
        prefix = 'SELECT count(*) FROM unit_schema.collected_items WHERE run_id = :run_id'
        if stmt.text == prefix:  # Original source comparator, never treated as scoped.
            matches = [r for r in self.persisted if r['run_id'] == params['run_id']]
        else:
            self.assert_scoped_query(stmt.text, prefix, params)
            pairs = {(params[f'tenant_id_{i}'], params[f'source_id_{i}']) for i in range((len(params)-1)//2)}
            matches = [r for r in self.persisted if r['run_id'] == params['run_id'] and (r['tenant_id'],r['source_id']) in pairs]
        return Result(value=None if self.null_scalar else len(matches))
    @staticmethod
    def assert_scoped_query(sql, prefix, params):
        total = (len(params)-1)//2
        assert total > 0
        clauses = [f'(tenant_id = :tenant_id_{i} AND source_id = :source_id_{i})' for i in range(total)]
        assert sql == prefix + ' AND (' + ' OR '.join(clauses) + ')', sql
        assert set(re.findall(r':(\w+)', sql)) == set(params), (sql, params)

def load_method():
    tree = ast.parse(SOURCE.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CollectedItemManager')
    method = next(n for n in cls.body if getattr(n, 'name', None) == 'store_items')
    ns = {'Any':Any,'Sequence':Sequence,'JSON':JsonType,'sa_text':Statement,
          'bindparam':lambda name, **kwargs:(name,kwargs),'settings':SimpleNamespace(DB_SCHEMA='unit_schema')}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(SOURCE),'exec'),ns)
    return ns['store_items']

STORE = load_method()

def row(tenant=17, source=1, run=42):
    return {'tenant_id':tenant,'source_id':source,'run_id':run,'external_id':'item','metrics':{'views':1},'author':{'id':2}}

class Manager:
    store_items = STORE
    def __init__(self, stamp=None, reject=False):
        self.stamp = stamp
        self.reject = reject
        self.applied = []
    async def _apply_tenant(self, session, data):
        self.applied.append(data)
        if self.reject:
            raise RuntimeError('tenant scope missing')
        if self.stamp is not None:
            data['tenant_id'] = self.stamp
        return data

class StagedCountScopeTests(unittest.IsolatedAsyncioTestCase):
    async def run_store(self, rows, persisted=(), rowcount=-1, stamp=None, null_scalar=False):
        session = Session(persisted,rowcount,null_scalar)
        manager = Manager(stamp)
        value = await manager.store_items(session,rows)
        return value, session, manager
    async def test_same_run_other_tenant_is_not_counted(self):
        value,_,_ = await self.run_store([row()],[row(),row(tenant=18)])
        self.assertEqual(value,1)
    async def test_same_run_other_source_is_not_counted(self):
        value,_,_ = await self.run_store([row()],[row(),row(source=2)])
        self.assertEqual(value,1)
    async def test_other_run_is_not_counted(self):
        value,_,_ = await self.run_store([row()],[row(),row(run=43)])
        self.assertEqual(value,1)
    async def test_stamped_tenant_not_raw_input_selects_readback(self):
        incoming = row(tenant=999)
        value,session,manager = await self.run_store([incoming],[row(),row(tenant=999)],stamp=17)
        self.assertEqual(value,1)
        self.assertEqual(session.calls[1][1],{'run_id':42,'tenant_id_0':17,'source_id_0':1})
        self.assertEqual(incoming['tenant_id'],999)
        self.assertEqual(manager.applied[0]['tenant_id'],17)
    async def test_multiple_sources_in_current_batch_are_counted(self):
        value,_,_ = await self.run_store([row(),row(source=2)],[row(),row(source=2),row(source=3)])
        self.assertEqual(value,2)
    async def test_tenant_source_pairs_are_not_cartesian(self):
        rows = [row(),row(tenant=18,source=2)]
        persisted = rows + [row(tenant=18),row(source=2)]
        value,_,_ = await self.run_store(rows,persisted)
        self.assertEqual(value,2)
    async def test_duplicate_pairs_generate_one_bound_clause(self):
        value,session,_ = await self.run_store([row(),row()],[row()])
        self.assertEqual(value,1)
        self.assertEqual(session.calls[1][1],{'run_id':42,'tenant_id_0':17,'source_id_0':1})
    async def test_only_selected_probe_pairs_are_included(self):
        rows = [row(run=None,source=2),row(),row(run=43,source=3)]
        value,session,_ = await self.run_store(rows,[row(),row(source=2),row(source=3)])
        self.assertEqual(value,1)
        self.assertEqual(session.calls[1][1],{'run_id':42,'tenant_id_0':17,'source_id_0':1})
    async def test_positive_driver_count_keeps_fast_path(self):
        value,session,_ = await self.run_store([row()],[row(),row(source=2)],rowcount=7)
        self.assertEqual(value,7)
        self.assertEqual(len(session.calls),1)
    async def test_no_run_keeps_legacy_estimate(self):
        value,session,_ = await self.run_store([row(run=None),row(run=None)])
        self.assertEqual(value,2)
        self.assertEqual(len(session.calls),1)
    async def test_empty_input_does_not_touch_session_or_tenant(self):
        value,session,manager = await self.run_store([])
        self.assertEqual(value,0)
        self.assertEqual(session.calls,[])
        self.assertEqual(manager.applied,[])
    async def test_zero_unknown_driver_path_remains_scoped_persisted_count(self):
        value,_,_ = await self.run_store([row()],[row(),row(source=2)],rowcount=0)
        self.assertEqual(value,1)  # Preserves the legacy fallback, not a new-insertion claim.
    async def test_missing_driver_count_and_null_scalar_keep_zero(self):
        value,_,_ = await self.run_store([row()],rowcount=None,null_scalar=True)
        self.assertEqual(value,0)
    async def test_tenant_apply_failure_stops_before_sql(self):
        manager,session = Manager(reject=True),Session()
        with self.assertRaises(RuntimeError):await manager.store_items(session,[row()])
        self.assertEqual(session.calls,[])
    async def test_insert_json_bindings_and_conflict_semantics_unchanged(self):
        rows = [row()]
        _,session,_ = await self.run_store(rows,rowcount=1)
        stmt,inserted = session.calls[0]
        self.assertIn('ON CONFLICT (source_id, external_id) DO NOTHING',stmt.text)
        self.assertIn('CAST(:metrics AS jsonb)',stmt.text)
        self.assertIn('CAST(:author AS jsonb)',stmt.text)
        self.assertEqual({name for name,_ in stmt.binds},{'metrics','author'})
        self.assertEqual(inserted,rows)

if __name__ == '__main__':
    unittest.main()
