"""Actual-source scope and task-link checks with local doubles, never an engine."""
from __future__ import annotations

import ast
import builtins
import importlib.util
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import sqlalchemy as sa
from sqlalchemy.sql import operators

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("_scope_loader", ROOT / "tests/source_import_isolation.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
load = loader.load_isolated_source


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.actor = NS(id=2, is_active=True)
        self.context = load("isolated.context", ROOT / "app/core/personal_catalog_context.py", {
            "app.core.permissions": NS(get_current_user=lambda:self.actor),
        })

    def test_missing_or_inactive_actor_fails_closed(self):
        self.actor.is_active=False
        with self.assertRaises(PermissionError): self.context.current_catalog_owner_id()
        self.actor=None
        with self.assertRaises(PermissionError): self.context.current_catalog_owner_id()

    def test_nested_trusted_execution_restores_actor_context(self):
        self.assertEqual(self.context.current_catalog_owner_id(),2)
        with self.context.catalog_execution_scope(3):
            self.assertEqual(self.context.current_catalog_owner_id(),3)
            with self.context.catalog_execution_scope(4): self.assertEqual(self.context.current_catalog_owner_id(),4)
            self.assertEqual(self.context.current_catalog_owner_id(),3)
        self.assertEqual(self.context.current_catalog_owner_id(),2)

    def test_denied_catalog_shadows_actor_and_nested_worker_owner(self):
        with self.context.catalog_execution_scope(3):
            with self.context.without_catalog_access():
                with self.assertRaises(PermissionError): self.context.current_catalog_owner_id()
            self.assertEqual(self.context.current_catalog_owner_id(),3)
        self.assertEqual(self.context.current_catalog_owner_id(),2)

    def test_foreign_model_and_provider_cannot_use_credentials(self):
        for model_owner, provider_owner in ((3,3),(2,3),(3,2)):
            with self.assertRaises(PermissionError):
                self.context.assert_catalog_model(NS(owner_user_id=model_owner,provider=NS(owner_user_id=provider_owner)))
        self.context.assert_catalog_model(NS(owner_user_id=2,provider=NS(owner_user_id=2)))

    def test_boolean_or_invalid_catalog_identity_rejected(self):
        for value in (True, None, 0, -1, "2"):
            with self.subTest(value=value), self.assertRaises(PermissionError):
                with self.context.catalog_execution_scope(value): pass

    def test_personal_api_paths_match_mounted_llm_namespace_and_boundaries(self):
        tree=ast.parse((ROOT/'app/core/api_scope.py').read_text())
        node=next(n for n in tree.body if getattr(n,'name',None)=='_is_personal_catalog_path')
        scope={}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-personal-api-path>','exec'),scope)
        check=scope['_is_personal_catalog_path']
        for root in ('/api/v1/llm/llm-models','/api/v1/llm/llm-providers'):
            for suffix in ('','/','/123'): self.assertTrue(check(root+suffix))
            self.assertFalse(check(root+'-other'))
        for wrong in ('/api/v1/llm-models','/api/v1/llm/other','/api/v1/users'):
            self.assertFalse(check(wrong))

    def test_base_manager_personal_predicate_and_stamping_actual_methods(self):
        tree=ast.parse((ROOT/'app/models/managers/base_manager.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BaseManager')
        nodes=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in {'_personal_criterion','_personal_values'}]
        scope={'is_bypass':lambda:False}
        normal_import=builtins.__import__
        def imports(name,globals=None,locals=None,fromlist=(),level=0):
            if name=='app.core.personal_catalog_context':return self.context
            return normal_import(name,globals,locals,fromlist,level)
        scope['__builtins__']=dict(vars(builtins),__import__=imports)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual-personal-manager>','exec'),scope)
        manager=NS(model=NS(__personal_scoped__=True,owner_user_id=sa.column('owner_user_id')))
        predicate=scope['_personal_criterion'](manager)
        self.assertEqual(predicate[0].right.value,2)
        self.assertEqual(scope['_personal_values'](manager,{}),{'owner_user_id':2})
        with self.assertRaises(PermissionError):scope['_personal_values'](manager,{'owner_user_id':3})
        with self.assertRaises(ValueError):scope['_personal_values'](manager,{'is_default':True})


class WorkerScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pure_collect_cannot_inherit_ambient_actor_or_worker_catalog(self):
        context = load("isolated.worker_context", ROOT / "app/core/personal_catalog_context.py", {
            "app.core.permissions": NS(get_current_user=lambda:NS(id=2,is_active=True)),
        })
        execution = load("isolated.worker", ROOT / "app/services/ai/catalog_execution.py", {
            "app.core.personal_catalog_context":context,
            "app.models":NS(TaskBillingPayer=NS()),
        })
        job=NS(job_type='collect',agent_task_id=None,initiated_by_user_id=None,tenant_id=10)
        async def handler(payload):
            with self.assertRaises(PermissionError): context.current_catalog_owner_id()
            return payload
        with context.catalog_execution_scope(3):
            self.assertEqual(await execution.run_with_job_catalog(job,handler,{'value':1}),{'value':1})
            self.assertEqual(context.current_catalog_owner_id(),3)
        self.assertEqual(context.current_catalog_owner_id(),2)


class FakeResult:
    def __init__(self,values):self.values=values
    def scalar_one_or_none(self):return self.values[0] if self.values else None
    def scalars(self):return self
    def all(self):return self.values


class LinkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.task_table=sa.table('agent_tasks',sa.column('id'),sa.column('tenant_id'))
        self.source_table=sa.table('sources',sa.column('id'),sa.column('tenant_id'))
        self.links=sa.table('agent_task_sources',sa.column('agent_task_id'),sa.column('source_id'))
        self.task=NS(id=1,tenant_id=10)
        self.sources=[NS(id=5,tenant_id=10,owner_task_id=None)]
        self.writes=[];self.rollbacks=0;self.commits=0;self.invalidated=[]
        self.manager=NS(_get_owned_session=self.session,filter=lambda **kw:NS(to_select=lambda:sa.select(self.task_table)))
        self.manager.invalidated=self.invalidated
        tree=ast.parse((ROOT/'app/models/managers/agent_task_manager.py').read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AgentTaskManager')
        node=next(n for n in cls.body if getattr(n,'name',None)=='_write_source_bindings')
        normal_import=builtins.__import__
        replacements={
            ('source',2):NS(Source=self.source_table.c),
            ('agent_task',2):NS(agent_task_sources=self.links),
            ('job_manager',1):NS(JobManager=lambda:NS(delete_pending_for_task=self.invalidate)),
        }
        def imports(name,globals=None,locals=None,fromlist=(),level=0):
            if (name,level) in replacements:return replacements[(name,level)]
            return normal_import(name,globals,locals,fromlist,level)
        scope={'__builtins__':dict(vars(builtins),__import__=imports)}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-task-source-bindings>','exec'),scope)
        self.write=types.MethodType(scope['_write_source_bindings'],self.manager)

    async def invalidate(self,session,**kwargs):self.invalidated.append(kwargs)

    @asynccontextmanager
    async def session(self, supplied=None):
        if supplied is not None:
            yield supplied
            return
        before=list(self.writes)
        try:yield NS(execute=self.execute);self.commits+=1
        except Exception:self.writes=before;self.rollbacks+=1;raise

    async def execute(self,statement):
        if isinstance(statement,sa.sql.Select):
            if 'agent_tasks' in str(statement):return FakeResult([self.task] if self.task else [])
            # The fake is deliberately explicit about SQL tenant/id filtering.
            ids=next(c.right.value for c in statement._where_criteria if c.operator is operators.in_op)
            tenant=next(c.right.value for c in statement._where_criteria if c.operator is operators.eq)
            return FakeResult([s for s in self.sources if s.id in ids and s.tenant_id==tenant])
        self.writes.append(statement)
        return FakeResult([])

    async def test_shared_task_transaction_session_is_not_committed_by_links(self):
        shared=NS(execute=self.execute)
        await self.write(1,[5],replace=True,session=shared)
        self.assertEqual(len(self.writes),2)
        self.assertEqual(self.commits,0)
        self.assertEqual(self.rollbacks,0)

    async def test_missing_source_rejects_before_delete(self):
        with self.assertRaises(ValueError):await self.write(1,[5,999],replace=True)
        self.assertEqual(self.writes,[]);self.assertEqual(self.commits,0)

    async def test_foreign_source_rejects_before_delete(self):
        self.sources.append(NS(id=6,tenant_id=20,owner_task_id=None))
        with self.assertRaises(ValueError):await self.write(1,[5,6],replace=True)
        self.assertEqual(self.writes,[])

    async def test_task_owned_source_rejects_foreign_task(self):
        self.sources[0].owner_task_id=2
        with self.assertRaises(ValueError):await self.write(1,[5],replace=False)
        self.assertEqual(self.writes,[])

    async def test_foreign_task_rejected(self):
        self.task=None
        with self.assertRaises(ValueError):await self.write(999,[5],replace=True)
        self.assertEqual(self.writes,[])

    async def test_valid_replacement_commits_once_and_invalidates_pending_snapshot(self):
        result=await self.write(1,[5,5],replace=True)
        self.assertEqual(result,1)
        self.assertEqual(len(self.writes),2)
        self.assertEqual(self.commits,1)
        self.assertEqual(self.invalidated,[{'tenant_id':10,'task_id':1}])

    async def test_append_does_not_delete_existing_links(self):
        await self.write(1,[5],replace=False)
        self.assertEqual(len(self.writes),1)
        self.assertIsInstance(self.writes[0],sa.sql.dml.Insert)

    async def test_invalid_ids_are_not_coerced_to_integers(self):
        for identity in (True,'5',0,-1):
            with self.subTest(identity=identity),self.assertRaises(ValueError):
                await self.write(1,[identity],replace=True)
        self.assertEqual(self.commits,0)


if __name__=='__main__':unittest.main()
