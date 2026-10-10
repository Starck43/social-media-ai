"""NEW actual-source tests, stdlib doubles only; no app/DB/pytest/bootstrap."""

import asyncio
import copy
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def load(name, path, imports=None):
    return load_isolated_source(name, ROOT/path, imports or {})


class Column:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return self.name, 'eq', value
    def __le__(self, value): return self.name, 'le', value
    def __lt__(self, value): return self.name, 'lt', value
    def __ge__(self, value): return self.name, 'ge', value
    def __gt__(self, value): return self.name, 'gt', value
    def is_(self, value): return self.name, 'is', value
    def startswith(self, value): return self.name, 'prefix', value


class Task:
    id=Column('id');tenant_id=Column('tenant_id');is_active=Column('is_active');next_run_at=Column('next_run_at')
    sources=Column('sources');agent_scenario=Column('agent_scenario')


class Source:
    id=Column('source_id');tenant_id=Column('tenant_id');is_active=Column('is_active')


class Job:
    id=Column('id');tenant_id=Column('tenant_id');status=Column('status');locked_at=Column('locked_at')
    run_at=Column('run_at');attempts=Column('attempts');max_attempts=Column('max_attempts');error=Column('error')
    created_at=Column('created_at')


class Query:
    def __init__(self, target): self.target=target;self.criteria=[];self.skip=None
    def where(self,*criteria): self.criteria.extend(criteria);return self
    def options(self,*options): return self
    def with_for_update(self,*,skip_locked): self.skip=skip_locked;return self


class Transaction:
    def __init__(self, session): self.session=session
    async def __aenter__(self):
        self.before=copy.deepcopy(vars(self.session.fixture.row));self.jobs_before=list(self.session.fixture.jobs)
    async def __aexit__(self, kind, error, traceback):
        fixture=self.session.fixture
        try:
            if kind or fixture.commit_error:
                if self.session.locked:
                    vars(fixture.row).clear();vars(fixture.row).update(self.before)
                    fixture.jobs[:]=self.jobs_before
                if not kind: raise fixture.commit_error
            else: fixture.commits+=1
        finally:
            if self.session.locked: fixture.lock.release()


class Session:
    def __init__(self, fixture): self.fixture=fixture;self.locked=False
    async def __aenter__(self): self.fixture.sessions+=1;return self
    async def __aexit__(self,*args): return False
    def begin(self): return Transaction(self)
    async def execute(self, query):
        self.fixture.queries.append(query)
        if query.target is Source.id:
            return SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:list(self.fixture.active_sources)))
        if query.skip is True and self.fixture.lock.locked():
            return SimpleNamespace(scalar_one_or_none=lambda:None)
        await self.fixture.lock.acquire();self.locked=True
        row=self.fixture.row
        valid=True
        for name,op,value in query.criteria:
            current=getattr(row,name)
            valid=valid and (current<=value if op=='le' else current==value)
        return SimpleNamespace(scalar_one_or_none=lambda:row if valid else None)
    async def flush(self):
        if self.fixture.insert_entered:
            self.fixture.insert_entered.set();await self.fixture.insert_release.wait()
        if self.fixture.flush_error: raise self.fixture.flush_error
    async def refresh(self,row): pass


class Base:
    fixture=None
    def __init__(self, model): self.model=model
    @classmethod
    def __class_getitem__(cls,item):return cls
    async def create(self,*,session=None,**kwargs):
        self.fixture.create_calls.append((session,kwargs.copy()))
        job=SimpleNamespace(**(kwargs | {'id':100+len(self.fixture.jobs),'tenant_id':self.fixture.tenant}))
        self.fixture.jobs.append(job)
        await session.flush()
        return job


def expression(*args):return args
SA=SimpleNamespace(select=Query,and_=expression,or_=expression,not_=lambda value:('not',value),
    func=SimpleNamespace(concat=lambda *args:('concat',*args),coalesce=lambda *args:('coalesce',*args)))
BUDGET=load('app.jobs._closure_budget','app/jobs/attempt_budget.py',{'sqlalchemy':SA})
POLICY=load('app.jobs._closure_policy','app/jobs/recovery_policy.py',{'app.jobs.attempt_budget':BUDGET})
CLAIMS=load('_closure_claims','app/jobs/claim_outcomes.py')
RESULTS=load('_closure_results','app/jobs/result_outcomes.py')


class ContextError(RuntimeError):pass


class Fixture:
    def __init__(self):
        self.tenant=31;self.bypass=False;self.jobs=[];self.queries=[];self.sessions=0;self.commits=0
        self.lock=asyncio.Lock();self.commit_error=None;self.flush_error=None
        self.insert_entered=None;self.insert_release=asyncio.Event();self.active_sources=set();self.create_calls=[]
        self.row=SimpleNamespace(id=7,tenant_id=31,is_active=True,next_run_at=NOW,cron_expr='0 * * * *',
            job_type='learn',payload={'original':1},sources=[],agent_scenario=None,
            last_run_at=None,last_status='failed',last_error='old')
        context=SimpleNamespace(current_tenant_id=lambda:self.tenant,is_bypass=lambda:self.bypass,TenantContextError=ContextError)
        config=SimpleNamespace(settings=SimpleNamespace(JOB_MAX_ATTEMPTS=3,SCHEDULER_TIMEZONE='UTC'))
        class LocalBase(Base):pass
        LocalBase.fixture=self
        imports={'app.models.managers.base_manager':SimpleNamespace(BaseManager=LocalBase),
            'app.models.job':SimpleNamespace(Job=Job),'app.core.config':config,
            'app.core.tenant_context':context,'app.jobs.attempt_budget':BUDGET,
            'app.jobs.recovery_policy':POLICY,'sqlalchemy':SA}
        self.job_module=load('app.models.managers._closure_jobs','app/models/managers/job_manager.py',imports)
        self.job_manager=self.job_module.JobManager()
        self.cron_calls=[]
        def cron(value,tz,*,after):
            self.cron_calls.append((value,tz,after))
            if value=='invalid':raise ValueError('bad cron')
            return after+timedelta(hours=1)
        imports=imports|{'croniter':SimpleNamespace(croniter=lambda *a:None),
            'app.core.permissions':SimpleNamespace(require_permission=lambda *a:lambda f:f),
            'app.types':SimpleNamespace(ActionType=SimpleNamespace(CREATE='create',UPDATE='update',DELETE='delete')),
            'app.models.agent_task':SimpleNamespace(AgentTask=Task),
            'app.models.source':SimpleNamespace(Source=Source),
            'app.models.managers.job_manager':SimpleNamespace(JobManager=lambda:self.job_manager),
            'app.core.database':SimpleNamespace(async_session_maker=lambda:Session(self)),
            'sqlalchemy.orm':SimpleNamespace(selectinload=lambda value:value),
            'app.tasks.cron':SimpleNamespace(next_run_at=cron)}
        self.module=load('app.models.managers._closure_tasks','app/models/managers/agent_task_manager.py',imports)
        self.manager=self.module.AgentTaskManager()
    async def admit(self,**kwargs):
        return await self.manager._admit_task_run(7,now=NOW,scheduled=True,expected_next_run_at=NOW,**kwargs)


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.fixture=Fixture()
    async def test_insert_and_advance_share_session_and_commit(self):
        f=self.fixture;receipt=await f.admit(timezone_name='Europe/Kirov')
        self.assertEqual(receipt['status'],'enqueued');self.assertEqual(len(f.jobs),1)
        self.assertEqual(f.row.next_run_at,NOW+timedelta(hours=1));self.assertEqual(f.row.last_status,'queued')
        self.assertEqual(f.commits,1);self.assertEqual(f.sessions,1)
        self.assertIsInstance(f.create_calls[0][0],Session)
        self.assertEqual(f.jobs[0].run_at,NOW);self.assertEqual(f.jobs[0].agent_task_id,7)
        self.assertEqual(f.cron_calls,[('0 * * * *','Europe/Kirov',NOW)])
    async def test_stale_snapshot_cannot_create_second_job(self):
        f=self.fixture;await f.admit();self.assertIsNone(await f.admit());self.assertEqual(len(f.jobs),1)
    async def test_competing_tick_skips_locked_uncommitted_admission(self):
        f=self.fixture;f.insert_entered=asyncio.Event()
        first=asyncio.create_task(f.admit())
        try:
            await asyncio.wait_for(f.insert_entered.wait(),1)
            self.assertIsNone(await f.admit());f.insert_release.set();await first
        finally:
            if not first.done():first.cancel()
            await asyncio.gather(first,return_exceptions=True)
        self.assertEqual(len(f.jobs),1)
        self.assertTrue(all(query.skip is True for query in f.queries))
    async def test_insert_error_rolls_back_schedule_and_job(self):
        f=self.fixture;before=copy.deepcopy(vars(f.row));f.flush_error=RuntimeError('insert failure')
        with self.assertRaises(RuntimeError):await f.admit()
        self.assertEqual(vars(f.row),before);self.assertEqual(f.jobs,[]);self.assertEqual(f.commits,0)
    async def test_commit_error_never_returns_receipt_or_second_write(self):
        f=self.fixture;before=copy.deepcopy(vars(f.row));f.commit_error=ConnectionError('ack lost')
        with self.assertRaises(ConnectionError):await f.admit()
        self.assertEqual(vars(f.row),before);self.assertEqual(f.jobs,[]);self.assertEqual(f.sessions,1)
    async def test_once_insert_disable_and_clear_are_atomic(self):
        f=self.fixture;f.row.cron_expr='@once';await f.admit()
        self.assertFalse(f.row.is_active);self.assertIsNone(f.row.next_run_at);self.assertEqual(len(f.jobs),1)
        self.assertIsNone(await f.admit())
    async def test_inactive_not_due_and_foreign_rows_are_not_admitted(self):
        for changes in ({'is_active':False},{'next_run_at':NOW+timedelta(seconds=1)},{'tenant_id':32}):
            f=Fixture();vars(f.row).update(changes)
            self.assertIsNone(await f.admit());self.assertEqual(f.jobs,[])
    async def test_missing_malformed_and_bypass_context_fail_before_session(self):
        for tenant,bypass in ((None,False),(True,False),('31',False),(0,False),(31,True)):
            f=Fixture();f.tenant=tenant;f.bypass=bypass
            with self.assertRaises(ContextError):await f.admit()
            self.assertEqual(f.sessions,0)
    async def test_invalid_timestamp_and_ids_fail_before_session(self):
        f=self.fixture
        for values in ({'task_id':True,'now':NOW},{'task_id':7,'now':NOW.replace(tzinfo=None)},
                       {'task_id':7,'now':NOW,'scheduled':True,'expected_next_run_at':None}):
            with self.assertRaises(ValueError):await f.manager._admit_task_run(**values)
        self.assertEqual(f.sessions,0)
    async def test_effective_activity_is_rechecked_under_lock(self):
        for configure in ('inactive_scenario','no_sources','inactive_link'):
            f=Fixture();f.row.job_type='collect'
            if configure=='inactive_scenario':f.row.agent_scenario=SimpleNamespace(is_active=False)
            if configure=='inactive_link':f.row.sources=[SimpleNamespace(is_active=False)]
            self.assertIsNone(await f.admit());self.assertEqual(f.jobs,[])
    async def test_workspace_source_fallback_keeps_tenant_predicate(self):
        f=self.fixture;f.row.job_type='collect';f.active_sources={14}
        self.assertEqual((await f.admit())['status'],'enqueued')
        self.assertIn(('tenant_id','eq',31),f.queries[1].criteria)
    async def test_invalid_cron_commits_failure_but_no_job(self):
        f=self.fixture;f.row.cron_expr='invalid'
        self.assertEqual((await f.admit())['status'],'invalid_schedule');self.assertEqual(f.jobs,[])
        self.assertEqual(f.row.last_status,'failed');self.assertEqual(f.row.next_run_at,NOW)
    async def test_manual_recurring_preserves_current_advanced_schedule(self):
        f=self.fixture;await f.admit();advanced=f.row.next_run_at
        receipt=await f.manager._admit_task_run(7,now=NOW,extra_payload={'original':2,'ignored':None})
        self.assertEqual(f.row.next_run_at,advanced);self.assertEqual(len(f.jobs),2)
        self.assertEqual(receipt['job'].payload,{'original':2});self.assertFalse(f.queries[-1].skip)
    async def test_manual_once_disarms_scheduler_in_same_transaction(self):
        f=self.fixture;f.row.cron_expr='@once'
        await f.manager._admit_task_run(7,now=NOW)
        self.assertIsNone(await f.admit());self.assertEqual(len(f.jobs),1)
    async def test_actual_cancellation_rolls_back_uncommitted_insert(self):
        f=self.fixture;before=copy.deepcopy(vars(f.row));f.insert_entered=asyncio.Event()
        task=asyncio.create_task(f.admit())
        try:
            await asyncio.wait_for(f.insert_entered.wait(),1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        self.assertEqual(vars(f.row),before);self.assertEqual(f.jobs,[])

    async def test_actual_runner_counts_only_committed_admissions(self):
        f=self.fixture
        async def get_due(now):
            return [copy.deepcopy(f.row)] if f.row.next_run_at is not None and f.row.next_run_at<=now else []
        f.manager.get_due=get_due
        module=load('app.tasks._closure_runner','app/tasks/runner.py',{
            'app.core.config':SimpleNamespace(settings=SimpleNamespace(SCHEDULER_TIMEZONE='UTC')),
            'app.core.tenant_context':SimpleNamespace(tenant_scope=lambda *a,**k:__import__('contextlib').nullcontext()),
            'app.models.managers.agent_task_manager':SimpleNamespace(AgentTaskManager=lambda:f.manager),
            'app.tasks.cron':SimpleNamespace(resolve_tz=lambda *a:'UTC')})
        self.assertEqual(await module.tick_tenant(31,now=NOW,tz='Europe/Kirov'),{'due':1,'enqueued':1,'failed':0})
        self.assertEqual(await module.tick_tenant(31,now=NOW),{'due':0,'enqueued':0,'failed':0})
        self.assertEqual(len(f.jobs),1)

    async def test_actual_manual_wrapper_uses_locked_fresh_task_not_stale_snapshot(self):
        f=self.fixture
        from contextlib import contextmanager
        @contextmanager
        def scope(tenant_id):
            previous=f.tenant;f.tenant=tenant_id
            try:yield
            finally:f.tenant=previous
        module=load('_closure_enqueue','app/jobs/enqueue.py',{
            'app.core.tenant_context':SimpleNamespace(tenant_scope=scope),
            'app.models.managers.agent_task_manager':SimpleNamespace(AgentTaskManager=lambda:f.manager)})
        stale=SimpleNamespace(id=7,tenant_id=31,payload={'stale':True},job_type='prune',cron_expr='@once')
        job=await module.enqueue_task_run(stale,{'original':2})
        self.assertEqual(job.job_type,'learn');self.assertEqual(job.payload,{'original':2})
        self.assertEqual(f.row.payload,{'original':1});self.assertTrue(f.row.is_active)

    async def test_enqueue_optional_session_preserves_default_fields(self):
        f=self.fixture;f.job_manager.create=AsyncMock(return_value=SimpleNamespace(id=1))
        session=object()
        await f.job_manager.enqueue('learn',run_at=NOW,session=session)
        self.assertIs(f.job_manager.create.await_args.kwargs['session'],session)
        self.assertEqual(f.job_manager.create.await_args.kwargs['max_attempts'],3)
        await f.job_manager.enqueue('learn',run_at=NOW)
        self.assertNotIn('session',f.job_manager.create.await_args.kwargs)


    async def test_enqueue_uses_actual_session_decorator_without_duplicate_none(self):
        import ast
        from functools import wraps
        source=(ROOT/'app/core/database.py').read_text()
        function=next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name=='with_db_session')
        module=ast.Module(body=[function],type_ignores=[])
        namespace={'wraps':wraps,'Callable':__import__('typing').Callable,'T':object}
        class OwnedSession:
            commit=AsyncMock();rollback=AsyncMock()
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
        owned=OwnedSession();namespace['async_session_maker']=lambda:owned
        exec(compile(module,'actual_database_decorator','exec'),namespace)
        seen=[]
        async def create(manager,*,session,**kwargs):seen.append(session);return SimpleNamespace(id=1)
        manager=self.fixture.job_manager
        manager.create=namespace['with_db_session'](create).__get__(manager,type(manager))
        await manager.enqueue('learn',run_at=NOW)
        self.assertEqual(seen,[owned]);owned.commit.assert_awaited_once()
        caller=object();await manager.enqueue('learn',run_at=NOW,session=caller)
        self.assertEqual(seen,[owned,caller]);owned.commit.assert_awaited_once()


class ReaperPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_write_is_stop_not_requeue_and_preserves_evidence(self):
        f=Fixture();query=SimpleNamespace(update=AsyncMock(side_effect=[0,2]));calls=[]
        def filtered(*args,**kwargs):calls.append((args,kwargs));return query
        f.job_manager.filter=filtered
        self.assertEqual(await f.job_manager.reap_stale(),2)
        changed=query.update.await_args_list[1].kwargs
        self.assertEqual(changed['status'],'failed');self.assertIn(POLICY.OUTCOME_UNCONFIRMED_PREFIX,changed['error'])
        self.assertEqual(calls[1][1]['status'],'running')
        for field in ('locked_at','started_at','attempts','result','llm_cost'):self.assertNotIn(field,changed)
    async def test_budget_stop_and_unknown_stop_remain_distinct(self):
        f=Fixture();query=SimpleNamespace(update=AsyncMock(side_effect=[7,1]));f.job_manager.filter=lambda *a,**k:query
        self.assertEqual(await f.job_manager.reap_stale(),1)
        self.assertIn(BUDGET.ATTEMPT_BUDGET_STOP_PREFIX,query.update.await_args_list[0].kwargs['error'])
        self.assertIn(POLICY.OUTCOME_UNCONFIRMED_PREFIX,query.update.await_args_list[1].kwargs['error'])
    async def test_reaper_storage_error_has_no_second_write(self):
        f=Fixture();query=SimpleNamespace(update=AsyncMock(side_effect=ConnectionError('store unavailable')))
        f.job_manager.filter=lambda *a,**k:query
        with self.assertRaises(ConnectionError):await f.job_manager.reap_stale()
        query.update.assert_awaited_once()


class DeliveryFailure(RuntimeError):
    def __init__(self,retryable=False):self.retryable=retryable;super().__init__('known_delivery_failure')


class DispatcherPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def run_error(self,error,allow_retry=True):
        job=SimpleNamespace(id=11,tenant_id=31,agent_task_id=None,job_type='learn',status='running',
            attempts=1,started_at=NOW,payload={},result=None)
        jobs=SimpleNamespace(renew_claim=AsyncMock(return_value=True),mark_failed=AsyncMock(),mark_done=AsyncMock())
        async def failed(job_id,*,claim,error,allow_retry,**kwargs):
            return CLAIMS.JobOutcomeReceipt(claim,CLAIMS.OutcomeAck.RETRY if allow_retry else CLAIMS.OutcomeAck.FAILED,error=error)
        jobs.mark_failed.side_effect=failed
        module=load('_closure_dispatcher','app/jobs/dispatcher.py',{
            'app.core.tenant_context':SimpleNamespace(tenant_scope=lambda *a,**k:__import__('contextlib').nullcontext()),
            'app.jobs.claim_outcomes':CLAIMS,'app.jobs.result_outcomes':RESULTS,'app.jobs.recovery_policy':POLICY,
            'app.jobs.handlers':SimpleNamespace(HANDLERS={}),
            'app.models.managers.job_manager':SimpleNamespace(JobManager=lambda:jobs),
            'app.services.digest.delivery_outcomes':SimpleNamespace(DeliveryFailure=DeliveryFailure)})
        module._notify_job_result=AsyncMock()
        receipt=await module.execute_job(job,AsyncMock(side_effect=error),allow_retry=allow_retry)
        return receipt,jobs,module
    async def test_generic_failure_is_unknown_terminal_without_raw_error(self):
        for error in (ValueError('PRIVATE'),TimeoutError('PRIVATE'),ConnectionError('PRIVATE')):
            receipt,jobs,module=await self.run_error(error)
            self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.FAILED)
            self.assertFalse(jobs.mark_failed.await_args.kwargs['allow_retry'])
            self.assertEqual(receipt.error,POLICY.OUTCOME_UNCONFIRMED_PREFIX)
            self.assertNotIn('PRIVATE',str(module._notify_job_result.await_args))
            self.assertIn('не подтверждён',module._notify_job_result.await_args.kwargs['error'])
    async def test_explicit_safe_delivery_retry_preserves_backoff_admission(self):
        receipt,jobs,module=await self.run_error(DeliveryFailure(True))
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.RETRY)
        self.assertTrue(jobs.mark_failed.await_args.kwargs['allow_retry']);module._notify_job_result.assert_not_awaited()
    async def test_manual_allow_retry_false_overrides_safe_delivery(self):
        receipt,jobs,module=await self.run_error(DeliveryFailure(True),False)
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.FAILED)
    async def test_truthy_nonboolean_retryable_is_not_safe(self):
        receipt,jobs,module=await self.run_error(DeliveryFailure(1))
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.FAILED)
    async def test_explicit_nonretryable_delivery_error_is_known_not_generic_unknown(self):
        receipt,jobs,module=await self.run_error(DeliveryFailure(False))
        self.assertEqual(receipt.error,'known_delivery_failure')
        self.assertFalse(POLICY.outcome_unconfirmed(receipt.error))


class ProjectionAndRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_markers_are_typed_and_include_existing_budget_stop(self):
        for value in (POLICY.OUTCOME_UNCONFIRMED_PREFIX,BUDGET.ATTEMPT_BUDGET_STOP_PREFIX+'old'):
            self.assertTrue(POLICY.outcome_unconfirmed(value))
        for value in (None,1,{},'known_delivery_failure'):
            self.assertFalse(POLICY.outcome_unconfirmed(value))
    async def test_ui_shows_unknown_and_does_not_offer_blind_retry(self):
        text=(ROOT/'app/web/templates/web/jobs.html').read_text()
        self.assertIn('job.id in unconfirmed_jobs',text);self.assertIn('результат не подтверждён',text)
        self.assertIn('job.id not in unconfirmed_jobs and job.status in retryable',text)
        task=(ROOT/'app/web/templates/web/tasks.html').read_text()
        self.assertIn("task.last_status == 'queued'",task);self.assertIn('поставлена в очередь',task)
    async def test_prune_excludes_both_operational_stop_markers(self):
        class Filter:
            def __init__(self):self.calls=[]
            def filter(self,*args,**kwargs):self.calls.append((args,kwargs));return self
            async def delete(self):return 0
        query=Filter();session=SimpleNamespace(begin=lambda:DummyAsyncContext(),close=AsyncMock())
        module=load('_closure_prune','app/jobs/handlers.py',{
            'app.services.social.credentials':SimpleNamespace(AuthorizationRequired=type('AuthorizationRequired',(Exception,),{})),
            'sqlalchemy':SA,'app.core.database':SimpleNamespace(new_session=lambda:session),
            'app.jobs.attempt_budget':BUDGET,'app.jobs.recovery_policy':POLICY,
            'app.models':SimpleNamespace(Job=SimpleNamespace(objects=query,created_at=Job.created_at,error=Job.error),
                CollectedItem=SimpleNamespace(objects=SimpleNamespace(delete_older_than=AsyncMock(return_value=0))))})
        await module.handle_prune({})
        self.assertIn(BUDGET.ATTEMPT_BUDGET_STOP_PREFIX,repr(query.calls))
        self.assertIn(POLICY.OUTCOME_UNCONFIRMED_PREFIX,repr(query.calls))


class DummyAsyncContext:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):return False




class PreparedRunnerSafety(unittest.TestCase):
    def test_owner_runner_guards_before_application_imports(self):
        import ast
        source=(ROOT/'tests/test_queue_recovery_admission_db.py').read_text()
        tree=ast.parse(source)
        execute=next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name=='execute')
        self.assertIn('--allow-tagged-fixture-commits',source)
        self.assertLess(source.rfind('validate_environment()'),source.rfind('asyncio.run(execute())'))
        self.assertTrue(any(isinstance(node, ast.ImportFrom) and node.module=='app.core' for node in execute.body))
        self.assertNotIn('ensure_test_database',source)
        self.assertNotIn('pg_terminate_backend',source)
    def test_owner_commit_guard_requires_creation_or_verified_cleanup(self):
        source=(ROOT/'tests/test_queue_recovery_admission_db.py').read_text()
        self.assertIn("phase == 'creation' and inserts",source)
        self.assertIn("connection.info.get('cleanup_verified') is True",source)
        self.assertIn('UPDATE-only or unapproved COMMIT forbidden',source)
        self.assertIn("all(row.slug in {prefix+'-a', prefix+'-b'} and not row.is_active",source)
        self.assertIn("(row.payload or {}).get('synthetic') == prefix",source)
    def runner_guard(self):
        import ast
        import re
        source = (ROOT/'tests/test_queue_recovery_admission_db.py').read_text()
        execute = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'execute')
        nodes = [n for n in execute.body if isinstance(n, ast.FunctionDef) and n.name in {'guard_write', 'guard_commit'}]
        operators = SimpleNamespace(eq=object(), in_op=object(), and_=object(), or_=object())
        context = {'re': re, 'operators': operators, 'owned': {'tenants': {31}, 'jobs': {7}, 'agent_tasks': {5}},
                   'prefix': 'synthetic', 'commit_attempts': []}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<actual-runner-guards>', 'exec'), context)
        return context

    def test_actual_guard_accepts_compiled_and_raw_savepoints_only(self):
        context = self.runner_guard()
        for compiled in (None, SimpleNamespace(statement=SimpleNamespace())):
            for sql in ('SAVEPOINT sa_savepoint_1', 'RELEASE SAVEPOINT sa_savepoint_1', 'ROLLBACK TO SAVEPOINT sa_savepoint_1'):
                context['guard_write'](SimpleNamespace(info={}), None, sql, {}, SimpleNamespace(compiled=compiled), False)
            for sql in ('COMMIT', 'DROP TABLE test_schema.jobs', 'UPDATE test_schema.jobs SET status=1'):
                with self.assertRaises(RuntimeError):
                    context['guard_write'](SimpleNamespace(info={}), None, sql, {}, SimpleNamespace(compiled=compiled), False)

    def test_actual_guard_rejects_or_escape_and_non_schedule_creation_update(self):
        context = self.runner_guard(); operators = context['operators']
        table = SimpleNamespace(name='agent_tasks', schema='test_schema')
        predicate = SimpleNamespace(operator=operators.eq, left=SimpleNamespace(table=table, name='id'),
                                    right=SimpleNamespace(key='pk', value=None))
        command = SimpleNamespace(table=table, is_update=True, whereclause=predicate, _values=None)
        compiled = SimpleNamespace(statement=command)
        current = SimpleNamespace(compiled=compiled, compiled_parameters=[{'pk': 5, 'last_status': 'queued'}])
        connection = SimpleNamespace(info={'fixture_commit_phase': 'creation'})
        context['guard_write'](connection, None, 'UPDATE test_schema.agent_tasks SET last_status=$1 WHERE id=$2', {}, current, False)
        with self.assertRaises(RuntimeError):
            context['guard_write'](connection, None, 'UPDATE test_schema.agent_tasks SET payload=$1 WHERE id=$2', {}, current, False)
        command.whereclause = SimpleNamespace(operator=operators.or_, clauses=[predicate, SimpleNamespace()])
        with self.assertRaises(RuntimeError):
            context['guard_write'](connection, None, 'UPDATE test_schema.agent_tasks SET last_status=$1 WHERE id=$2 OR true', {}, current, False)

    def test_actual_guard_rejects_negated_owned_predicate_but_allows_parentheses(self):
        context = self.runner_guard(); operators = context['operators']
        table = SimpleNamespace(name='jobs', schema='test_schema')
        predicate = SimpleNamespace(operator=operators.eq, left=SimpleNamespace(table=table, name='id'),
                                    right=SimpleNamespace(key='pk', value=None))
        command = SimpleNamespace(table=table, is_update=True,
                                  whereclause=SimpleNamespace(element=predicate, __visit_name__='grouping'))
        current = SimpleNamespace(compiled=SimpleNamespace(statement=command), compiled_parameters=[{'pk': 7}])
        connection = SimpleNamespace(info={})
        context['guard_write'](connection, None, 'UPDATE test_schema.jobs SET status=$1 WHERE (id=$2)', {}, current, False)
        command.whereclause = SimpleNamespace(element=predicate, __visit_name__='unary', operator=object())
        with self.assertRaises(RuntimeError):
            context['guard_write'](connection, None, 'UPDATE test_schema.jobs SET status=$1 WHERE NOT(id=$2)', {}, current, False)

    def test_actual_commit_guard_never_counts_driver_ack_or_allows_update_only(self):
        context = self.runner_guard()
        for info in ({}, {'fixture_commit_phase': 'creation', 'fixture_inserts': set()},
                     {'fixture_commit_phase': 'cleanup', 'cleanup_verified': False},
                     {'fixture_commit_phase': 'creation', 'fixture_inserts': {'tenants'}, 'fixture_writes': {'agent_tasks'}}):
            with self.assertRaises(RuntimeError):
                context['guard_commit'](SimpleNamespace(info=info))
        context['guard_commit'](SimpleNamespace(info={'fixture_commit_phase': 'creation', 'fixture_inserts': {'jobs'}, 'fixture_writes': {'agent_tasks'}}))
        context['guard_commit'](SimpleNamespace(info={'fixture_commit_phase': 'cleanup', 'cleanup_verified': True}))
        self.assertEqual(len(context['commit_attempts']), 2)
        source = (ROOT/'tests/test_queue_recovery_admission_db.py').read_text()
        self.assertLess(source.index('await outer.commit()'), source.index('commits.append((phase, inserted))'))

    def test_distinct_connection_checks_and_uncertainty_limits_are_explicit(self):
        source=(ROOT/'tests/test_queue_recovery_admission_db.py').read_text()
        self.assertIn('assert a.pid != b.pid',source)
        self.assertIn("state == 'Lock'",source)
        self.assertIn('SELECT 1 / 0',source)
        self.assertIn('UPDATE-only outcome/lease transactions rolled back',source)
        self.assertIn('await outer.rollback()',source)

if __name__=='__main__':unittest.main()
