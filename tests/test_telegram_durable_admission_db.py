"""OWNER ONLY: new L1/L2 admission on real, independent PostgreSQL connections.

Requires separate --allow-tagged-fixture-commits approval for THIS package.
Existing local test_schema only. Tagged fixture creation, admission and scoped
cleanup may commit; no DDL/bootstrap/reset/migration/foreign writes/providers.
Synthetic tenants are inactive; source mode is api; queued jobs are future-dated
by a test-only enqueue wrapper and are NEVER dispatched. Sequences may advance.
"""
from __future__ import annotations
import argparse
import asyncio
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
import sys
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_job_claim_heartbeat_db import validate_environment  # Environment guard ONLY.
from telegram_admission_guard import FixtureGuardError, admission_diagnostic, insert_parameter_rows


async def execute():
    # No application/engine imports before environment + explicit approval guard.
    from sqlalchemy import column, delete, event, func, select, table
    from sqlalchemy.sql import operators
    from app.core import database
    from app.core.config import settings
    from app.core.tenant_context import tenant_scope
    from app.models import AIAnalytics, CollectedItem, Job, Platform, Source, Tenant
    from app.services.monitoring.staging import ContentAdmissionError, admit_telegram_items
    from app.services.ai.dedup import item_hash
    from app.types import PeriodType, SourceType, PlatformType

    models = {'tenants':Tenant,'sources':Source,'collected_items':CollectedItem,'jobs':Job,'ai_analytics':AIAnalytics}
    if settings.DB_SCHEMA != 'test_schema' or any(model.__table__.schema != 'test_schema' for model in models.values()):
        raise FixtureGuardError('guard_06')
    prefix='tg-admission-'+uuid4().hex[:18]
    phase=ContextVar('tg_admission_fixture_phase',default=None)
    owned={name:set() for name in models}
    engine=database.async_engine
    commits=[]; rollbacks=[]; checks=0
    blocked_commits=[]
    backend_pids=set()
    sequences_before=sequences_after=None
    own=other=source=None

    def write_guard(conn,cursor,statement,parameters,context,executemany):
        driver=conn.connection.driver_connection
        backend_pids.add(driver.get_server_pid())
        compiled=context.compiled
        if compiled is None:
            if re.fullmatch(r"SET LOCAL (statement_timeout = '10s'|lock_timeout = '3s')",statement,re.I):
                return
            if statement.lstrip().upper().startswith(('SELECT ','SAVEPOINT ','RELEASE SAVEPOINT ','ROLLBACK TO SAVEPOINT ')):
                return
            raise FixtureGuardError('guard_13')
        command=compiled.statement
        kind=next((kind for kind in ('insert','update','delete') if getattr(command,'is_'+kind,False)),None)
        if kind is None:
            if not getattr(command,'is_select',False):raise FixtureGuardError('guard_11')
            return
        table=command.table
        if table.schema!='test_schema' or table.name not in owned or phase.get() not in {'creation','admission','cleanup'}:
            raise FixtureGuardError('guard_07')
        if phase.get()=='cleanup' and kind!='delete':raise FixtureGuardError('guard_03')
        if phase.get()=='admission' and not ((kind=='insert' and table.name in {'collected_items','jobs'}) or (kind=='update' and table.name=='sources')):
            raise FixtureGuardError('guard_02')
        if phase.get()=='creation' and not (kind=='insert' and table.name in {'tenants','sources','collected_items','ai_analytics'}):
            raise FixtureGuardError('guard_04')
        if kind=='update':
            match=re.search(r'\bSET\s+(.+?)\s+WHERE\b',statement,re.I|re.S)
            targets=[re.fullmatch(r'\s*"?([a-z_]+)"?\s*=.+',part,re.S) for part in (match.group(1).split(',') if match else [])]
            allowed={'params','last_item_id','last_checked','updated_at'} if table.name=='sources' else {'analyze_attempts','updated_at'}
            if not targets or not all(targets) or not {target.group(1) for target in targets}<=allowed:
                raise FixtureGuardError('guard_14')
        # Normal ORM binds and multi-VALUES _mN binds must be fenced per row.
        parameter_rows = [row for values in context.compiled_parameters
                          for row in (insert_parameter_rows(command, values, compiled=compiled) if kind == 'insert' else [values])]
        for values in parameter_rows:
            if kind=='insert':
                if table.name=='tenants':
                    if values.get('slug') not in {prefix+'-a',prefix+'-b'} or values.get('is_active') is not False:
                        raise FixtureGuardError('guard_18')
                else:
                    if values.get('tenant_id') not in owned['tenants']:raise FixtureGuardError('guard_10')
                    if table.name=='sources':
                        if not str(values.get('name','')).startswith(prefix) or (values.get('params') or {}).get('synthetic')!=prefix or (values.get('params') or {}).get('mode')!='api':
                            raise FixtureGuardError('guard_17')
                    elif table.name=='jobs':
                        payload=values.get('payload') or {}
                        if payload.get('synthetic')!=prefix or payload.get('source_ids')!=[source.id] or values.get('agent_task_id') is not None or values.get('run_at') < datetime.now(timezone.utc)+timedelta(days=300):
                            raise FixtureGuardError('guard_19')
                    else:
                        if values.get('source_id') not in owned['sources']:raise FixtureGuardError('guard_09')
                        if table.name=='collected_items' and not str(values.get('external_id','')).startswith(source.external_id+'_'):
                            raise FixtureGuardError('guard_16')
                        if table.name=='ai_analytics' and (values.get('summary_data') or {}).get('synthetic')!=prefix:
                            raise FixtureGuardError('guard_15')
                continue
            def positive_fence(node):
                operator=getattr(node,'operator',None)
                clauses=list(getattr(node,'clauses',()))
                if operator is operators.and_:return any(positive_fence(child) for child in clauses)
                if operator is operators.or_:return bool(clauses) and all(positive_fence(child) for child in clauses)
                if getattr(node,'__visit_name__',None)=='grouping':return positive_fence(node.element)
                if operator not in (operators.eq,operators.in_op):return False
                left,right=getattr(node,'left',None),getattr(node,'right',None)
                if getattr(left,'table',None) is None or left.table.name!=table.name or left.table.schema!='test_schema':return False
                allowed=owned['tenants'] if left.name=='tenant_id' else owned['sources'] if left.name=='source_id' else owned[table.name] if left.name=='id' else set()
                value=values.get(getattr(right,'key',None),getattr(right,'value',None))
                candidates=value if isinstance(value,(list,tuple,set)) else [value]
                return bool(allowed and candidates and all(type(candidate) is int and candidate in allowed for candidate in candidates))
            if not positive_fence(getattr(command,'whereclause',None)):
                raise FixtureGuardError('guard_01')

    def bounded_begin(conn):
        conn.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
        conn.exec_driver_sql("SET LOCAL lock_timeout = '3s'")

    def commit_guard(conn):
        if phase.get() not in {'creation','admission','cleanup'}:
            blocked_commits.append(True);raise FixtureGuardError('guard_12')
        commits.append(phase.get())
    def rollback_record(conn):rollbacks.append(True)

    event.listen(engine.sync_engine,'before_cursor_execute',write_guard)
    event.listen(engine.sync_engine,'begin',bounded_begin)
    event.listen(engine.sync_engine,'commit',commit_guard)
    event.listen(engine.sync_engine,'rollback',rollback_record)
    real_enqueue=Job.objects.enqueue
    async def future_enqueue(kind,payload=None,**kwargs):
        # TEST ONLY: avoid any real worker seeing an executable synthetic job.
        return await real_enqueue(kind,{**(payload or {}),'synthetic':prefix},
                                  run_at=datetime.now(timezone.utc)+timedelta(days=365),**kwargs)
    factory=database.async_session_maker
    def item(identity):return {'id':str(identity),'external_id':f'{source.external_id}_{identity}',
                              'platform':'telegram','text':prefix+' useful '+str(identity),
                              'date':datetime.now(timezone.utc),'attachments':[{'type':'unknown','url':None}]}
    async def admit(identity,*,wake=True,actor=own):
        token=phase.set('admission')
        try:
            with tenant_scope(own if actor is None else actor):
                return await admit_telegram_items([item(identity)],source,wake_analysis=wake)
        except ContentAdmissionError as error:
            print('Admission diagnostic:', admission_diagnostic(error, root=ROOT))
            raise
        finally:phase.reset(token)
    async def snapshot():
        async with factory() as session:
            current=(await session.execute(select(Source).where(Source.id==source.id,Source.tenant_id==own))).scalar_one()
            raws=list((await session.execute(select(CollectedItem).where(CollectedItem.source_id==source.id,CollectedItem.tenant_id==own))).scalars())
            jobs=list((await session.execute(select(Job).where(Job.tenant_id==own))).scalars())
            return current,raws,jobs
    async def check(title):
        nonlocal checks
        checks+=1;print(f'PASS {checks}: {title}')

    sequences=table('pg_sequences',column('schemaname'),column('sequencename'),column('last_value'),schema='pg_catalog')
    async def sequence_values():
        async with factory() as session:
            return dict((await session.execute(select(sequences.c.sequencename,sequences.c.last_value).where(
                sequences.c.schemaname=='test_schema',
                sequences.c.sequencename.in_([name+'_id_seq' for name in models]),
            ))).all())
    try:
        sequences_before=await sequence_values()
        async with factory() as session:
            platform=(await session.execute(select(Platform).where(Platform.platform_type==PlatformType.TELEGRAM).limit(1))).scalar_one_or_none()
            if platform is None:raise FixtureGuardError('guard_05')
            platform_id=platform.id
        token=phase.set('creation')
        try:
            async with factory() as session:
                async with session.begin():
                    tenants=[Tenant(name=prefix,slug=prefix+suffix,plan='business',is_active=False) for suffix in ('-a','-b')]
                    session.add_all(tenants);await session.flush();owned['tenants'].update(row.id for row in tenants)
                    own,other=[row.id for row in tenants]
                    source=Source(tenant_id=own,platform_id=platform_id,name=prefix,external_id='-100'+str(int(uuid4().hex[:14],16)),
                                  source_type=SourceType.CHANNEL,is_active=True,last_item_id='50',params={'mode':'api','synthetic':prefix})
                    session.add(source);await session.flush();owned['sources'].add(source.id)
        finally:phase.reset(token)
        with patch.object(Job.objects,'enqueue',future_enqueue):
            assert await admit(90,actor=own)==1
            current,raws,jobs=await snapshot()
            assert len(raws)==len(jobs)==1 and current.last_item_id=='90' and current.params['telegram_l2_last_item_id']=='50'
            assert raws[0].attachments==[{'type':'unknown','url':None}] and jobs[0].agent_task_id is None
            await check('committed L1 raw/job/display cursor; pull cursor frozen')
            # INSERT a marked raw fixture with a consumed attempt budget;
            # never grant a general UPDATE-only root COMMIT for test setup.
            token=phase.set('creation')
            try:
                from app.utils.collected_content import build_staged_rows
                async with factory() as session:
                    async with session.begin():
                        raw=CollectedItem(tenant_id=own,analyze_attempts=2,
                                          **build_staged_rows([item(91)],source,None)[0])
                        session.add(raw);await session.flush();owned['collected_items'].add(raw.id)
            finally:phase.reset(token)
            assert await admit(90,actor=own)==0 and await admit(91,actor=own)==0
            _,raws,jobs=await snapshot()
            assert len(raws)==2 and len(jobs)==1 and next(row for row in raws if row.external_id==item(91)['external_id']).analyze_attempts==2
            await check('duplicate delivery creates no new raw/job and does not reset attempts')
            assert await admit(20,actor=own)==1
            current,raws,jobs=await snapshot();assert current.last_item_id=='91' and len(raws)==3 and len(jobs)==2
            await check('unseen lower-than-watermark message still admitted')
            assert await admit(55,wake=False,actor=own)==1
            current,raws,jobs=await snapshot();assert current.params['telegram_l2_last_item_id']=='55' and current.last_item_id=='91' and len(jobs)==2
            await check('L2 durable admission advances only own pull progress without extra job')
            async def fail_queue(*args,**kwargs):raise RuntimeError('synthetic queue failure')
            with patch.object(Job.objects,'enqueue',fail_queue):
                try:await admit(100,actor=own)
                except ContentAdmissionError:pass
                else:raise AssertionError('queue failure was acknowledged')
            current,raws,jobs=await snapshot();assert current.last_item_id=='91' and len(raws)==4 and len(jobs)==2
            await check('queue failure rolls back raw and both cursors')
            conflict=item(90);conflict['text']='different immutable payload'
            token=phase.set('admission')
            try:
                with tenant_scope(own):
                    try:await admit_telegram_items([conflict],source,wake_analysis=True)
                    except ContentAdmissionError:pass
                    else:raise AssertionError('conflicting raw identity acknowledged')
            finally:phase.reset(token)
            _,raws,jobs=await snapshot();assert len(raws)==4 and len(jobs)==2
            await check('conflicting identity is not overwritten/acknowledged')
            try:await admit(101,actor=other)
            except ContentAdmissionError:pass
            else:raise AssertionError('foreign tenant admission acknowledged')
            await check('foreign tenant refused without mutation')
            before_jobs=len(jobs)
            results=await asyncio.wait_for(asyncio.gather(admit(110,actor=own),admit(110,actor=own)),15)
            _,raws,jobs=await snapshot();assert sorted(results)==[0,1] and len(jobs)==before_jobs+1 and sum(row.external_id==item(110)['external_id'] for row in raws)==1
            await check('two concurrent deliveries produce exactly one raw and wakeup')
            ready=asyncio.Event();release=asyncio.Event();real_admit=CollectedItem.objects.admit_items
            async def pause_raw(session,rows):
                count=await real_admit(session,rows);ready.set();await release.wait();return count
            with patch.object(CollectedItem.objects,'admit_items',pause_raw):
                writer=asyncio.create_task(admit(120,actor=own))
                try:
                    await asyncio.wait_for(ready.wait(),10)
                    current,raws,jobs=await snapshot()
                    assert current.last_item_id=='110' and all(row.external_id!=item(120)['external_id'] for row in raws)
                    release.set();assert await asyncio.wait_for(writer,10)==1
                finally:
                    release.set()
                    if not writer.done():writer.cancel()
                    await asyncio.gather(writer,return_exceptions=True)
            current,raws,jobs=await snapshot();assert current.last_item_id=='120' and any(row.external_id==item(120)['external_id'] for row in raws)
            await check('independent connection sees no precommit raw/cursor and sees committed receipt')
            covered=item(130);token=phase.set('creation')
            try:
                async with factory() as session:
                    async with session.begin():
                        row=AIAnalytics(tenant_id=own,source_id=source.id,analysis_date=date.today(),period_type=PeriodType.DAY,
                                        summary_data={'synthetic':prefix,'content_hashes':[item_hash(covered)]})
                        session.add(row);await session.flush();owned['ai_analytics'].add(row.id)
            finally:phase.reset(token)
            job_count=len(jobs);assert await admit(130,actor=own)==0
            _,raws,jobs=await snapshot();assert len(jobs)==job_count and all(row.external_id!=covered['external_id'] for row in raws)
            await check('retired saved parent-hash receipt prevents re-staging/wakeup')
        assert checks==10 and not blocked_commits and len(backend_pids)>=2
    finally:
        # Exact newly-created tenant IDs fence all cleanup. Never drop/reset/DDL.
        if owned['tenants']:
            # Positively revalidate markers before deleting by tracked new IDs.
            async with factory() as session:
                tenants=list((await session.execute(select(Tenant).where(Tenant.id.in_(owned['tenants'])))).scalars())
                if any(row.slug not in {prefix+'-a',prefix+'-b'} or row.is_active is not False for row in tenants):
                    raise FixtureGuardError('guard_08')
                for name in ('sources','jobs','collected_items','ai_analytics'):
                    model=models[name]
                    rows=list((await session.execute(select(model).where(model.tenant_id.in_(owned['tenants'])))).scalars())
                    for row in rows:
                        if name=='sources':valid=row.id in owned['sources'] and row.params.get('synthetic')==prefix
                        elif name=='jobs':valid=row.payload.get('synthetic')==prefix and row.payload.get('source_ids')==[source.id]
                        elif name=='collected_items':valid=row.source_id in owned['sources'] and row.external_id.startswith(source.external_id+'_')
                        else:valid=row.source_id in owned['sources'] and row.summary_data.get('synthetic')==prefix
                        if not valid:raise FixtureGuardError('guard_20')
            token=phase.set('cleanup')
            try:
                async with factory() as session:
                    async with session.begin():
                        for name in ('jobs','collected_items','ai_analytics','sources'):
                            model=models[name]
                            await session.execute(delete(model).where(model.tenant_id.in_(owned['tenants'])))
                        await session.execute(delete(Tenant).where(Tenant.id.in_(owned['tenants'])))
            finally:phase.reset(token)
            async with factory() as session:
                for name,model in models.items():
                    predicate=model.id.in_(owned['tenants']) if name=='tenants' else model.tenant_id.in_(owned['tenants'])
                    assert (await session.execute(select(func.count()).select_from(model).where(predicate))).scalar()==0
            print('Scoped cleanup: no tagged leftovers')
        sequences_after=await sequence_values()
        event.remove(engine.sync_engine,'before_cursor_execute',write_guard)
        event.remove(engine.sync_engine,'begin',bounded_begin)
        event.remove(engine.sync_engine,'commit',commit_guard)
        event.remove(engine.sync_engine,'rollback',rollback_record)
        await engine.dispose()
    print(f'{checks}/10 PASS; committed tagged phases={commits}; rollbacks={len(rollbacks)}; distinct PostgreSQL backends={len(backend_pids)}')
    print(f'Sequences before={sequences_before}; after={sequences_after}')
    print('Limits: test-only future job timestamp; no dispatcher/providers/production timing/eternal receipts/historical repair. Sequences may advance.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-tagged-fixture-commits',action='store_true')
    args=parser.parse_args()
    if not args.allow_tagged_fixture_commits:
        raise SystemExit('Stop: explicit package-specific tagged fixture COMMIT/cleanup approval required; no DB action')
    validate_environment()
    asyncio.run(execute())
