"""OWNER ONLY: queue admission/recovery, distinct PostgreSQL connections.

Requires --allow-tagged-fixture-commits. Only tagged synthetic creation (including
Job insertion + Task schedule) and scoped cleanup may COMMIT. Other mutations use
caller rollback/savepoints. Existing local test_schema only; no bootstrap, DDL,
providers, pytest, global bypass, old checks or worker-process termination.
This is NOT production timer, provider idempotency or independent-worker kill proof.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_job_claim_heartbeat_db import validate_environment  # Guard only, no old execution.


async def execute():
    # ALL application imports follow validate_environment().
    from sqlalchemy import delete, event, select, text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.sql import operators
    from app.core import database
    from app.core.config import settings
    from app.core.tenant_context import TenantContextError, tenant_scope
    from app.jobs.claim_outcomes import JobClaim, OutcomeAck
    from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX
    from app.models import AgentTask, Job, Tenant
    from app.models.managers import base_manager
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.models.managers.job_manager import JobManager
    from app.services.digest.delivery_outcomes import DeliveryFailure
    from source_import_isolation import load_isolated_source

    for model in (Tenant, AgentTask, Job):
        if model.__table__.schema != 'test_schema' or settings.DB_SCHEMA != 'test_schema':
            raise RuntimeError('Stop: mapping/schema mismatch')
    engine = database.async_engine
    prefix = 'queue-accept-' + uuid4().hex[:18]
    owned = {'tenants': set(), 'agent_tasks': set(), 'jobs': set()}
    actor_factory = ContextVar('queue_acceptance_factory')
    commit_attempts = []
    commits = []
    rollbacks = []
    sequences_before = None
    sequences_after = None
    checks = 0
    connections = set()
    now = datetime.now(timezone.utc)

    def routed_factory(*args, **kwargs):
        return actor_factory.get()(*args, **kwargs)

    def guard_write(connection, cursor, statement, parameters, context, executemany):
        compiled = context.compiled
        sql = statement.lstrip().upper()
        read_or_control = sql.startswith(('SELECT ', 'SET LOCAL ', 'SAVEPOINT ', 'RELEASE SAVEPOINT ', 'ROLLBACK TO SAVEPOINT '))
        if compiled is None:
            if not read_or_control:
                raise RuntimeError('Stop: uncompiled write/DDL forbidden')
            return
        command = compiled.statement
        kind = next((k for k in ('insert', 'update', 'delete') if getattr(command, 'is_' + k, False)), None)
        if kind is None:
            if not getattr(command, 'is_select', False) and not read_or_control:
                raise RuntimeError('Stop: only SELECT or explicitly fenced fixture DML allowed')
            return
        table = command.table
        if table.schema != 'test_schema' or table.name not in owned:
            raise RuntimeError('Stop: write outside approved fixture tables')
        phase = connection.info.get('fixture_commit_phase')
        if phase == 'cleanup' and kind != 'delete':
            raise RuntimeError('Stop: cleanup permits verified fixture DELETE only')
        if phase == 'creation' and kind != 'insert':
            schedule_fields = {'last_status', 'last_error', 'last_run_at', 'next_run_at', 'is_active'}
            # ORM flush may compile an UPDATE with _values=None; inspect the
            # generated SET targets, not its WHERE bind parameters. Fail closed.
            match = re.search(r'\bSET\s+(.+?)\s+WHERE\b', statement, re.I | re.S)
            assignments = match.group(1).split(',') if match else []
            targets = [re.fullmatch(r'\s*"?([a-z_]+)"?\s*=.+', part, re.S) for part in assignments]
            changed = {target.group(1) for target in targets if target}
            if not assignments or not all(targets):
                raise RuntimeError('Stop: unrecognized committed schedule SET shape')
            if kind != 'update' or table.name != 'agent_tasks' or not changed or not changed <= schedule_fields:
                raise RuntimeError('Stop: creation permits INSERT and accompanying Task schedule UPDATE only')
        for values in context.compiled_parameters:
            if kind == 'insert':
                if table.name == 'tenants':
                    if values.get('slug') not in {prefix + '-a', prefix + '-b'} or values.get('is_active') is not False:
                        raise RuntimeError('Stop: unmarked tenant creation')
                else:
                    if values.get('tenant_id') not in owned['tenants']:
                        raise RuntimeError('Stop: foreign fixture INSERT')
                    marker = values.get('name') if table.name == 'agent_tasks' else (values.get('payload') or {}).get('synthetic')
                    if not (str(marker).startswith(prefix + '-') if table.name == 'agent_tasks' else marker == prefix):
                        raise RuntimeError('Stop: unmarked fixture INSERT')
                connection.info.setdefault('fixture_inserts', set()).add(table.name)
                continue
            # Require a real positive tenant or exact owned PK predicate. Never add
            # a bypass or predicate to the production query to make this pass.
            def fenced_predicate(node):
                operator = getattr(node, 'operator', None)
                clauses = list(getattr(node, 'clauses', ()))
                if operator is operators.and_:
                    return any(fenced_predicate(child) for child in clauses)
                if operator is operators.or_:
                    return bool(clauses) and all(fenced_predicate(child) for child in clauses)
                element = getattr(node, 'element', None)
                if element is not None:
                    return fenced_predicate(element)
                if operator not in (operators.eq, operators.in_op):
                    return False
                left, right = getattr(node, 'left', None), getattr(node, 'right', None)
                if getattr(left, 'table', None) is None or left.table.name != table.name or left.table.schema != 'test_schema':
                    return False
                key = getattr(right, 'key', None)
                value = values.get(key, getattr(right, 'value', None))
                allowed = owned['tenants'] if left.name == 'tenant_id' else owned[table.name] if left.name == 'id' else set()
                candidates = value if isinstance(value, (tuple, list, set)) else [value]
                return bool(allowed and candidates and all(type(v) is int and v in allowed for v in candidates))
            fenced = fenced_predicate(getattr(command, 'whereclause', None))
            if not fenced:
                raise RuntimeError('Stop: UPDATE/DELETE lacks an owned fixture predicate')
        connection.info.setdefault('fixture_writes', set()).add(table.name)

    def guard_commit(connection):
        phase = connection.info.get('fixture_commit_phase')
        inserts = connection.info.get('fixture_inserts', set())
        writes = connection.info.get('fixture_writes', set())
        if phase == 'creation' and inserts:
            if writes and (writes != {'agent_tasks'} or 'jobs' not in inserts):
                raise RuntimeError('Stop: committed schedule UPDATE requires newly inserted Job')
            commit_attempts.append(('creation', sorted(inserts)))
        elif phase == 'cleanup' and connection.info.get('cleanup_verified') is True:
            commit_attempts.append(('cleanup', []))
        else:
            raise RuntimeError('Stop: UPDATE-only or unapproved COMMIT forbidden')

    event.listen(engine.sync_engine, 'before_cursor_execute', guard_write)
    event.listen(engine.sync_engine, 'commit', guard_commit)

    class InjectedFlushFailure(AsyncSession):
        async def flush(self, *args, **kwargs):
            await super().flush(*args, **kwargs)
            if any(isinstance(row, AgentTask) and row.last_status == 'queued' for row in self.identity_map.values()):
                await self.execute(text('SELECT 1 / 0'))  # Actual PostgreSQL error, no fake driver exception.

    @asynccontextmanager
    async def actor(*, creation=False, failing=False, cleanup=False):
        connection = await engine.connect()
        connections.add(connection)
        info = connection.sync_connection.info
        info['fixture_inserts'] = set()
        connection.sync_connection.info['fixture_writes'] = set()
        connection.sync_connection.info['fixture_commit_phase'] = 'cleanup' if cleanup else 'creation' if creation else None
        connection.sync_connection.info['cleanup_verified'] = False
        outer = None
        token = None
        try:
            outer = await connection.begin()
            await connection.execute(text("SET LOCAL statement_timeout = '12s'"))
            await connection.execute(text("SET LOCAL lock_timeout = '8s'"))
            pid = (await connection.execute(text('SELECT pg_backend_pid()'))).scalar_one()
            factory = async_sessionmaker(bind=connection, class_=InjectedFlushFailure if failing else AsyncSession,
                expire_on_commit=False, join_transaction_mode='create_savepoint')
            token = actor_factory.set(factory)
            async def rollback():
                if outer.is_active:
                    await outer.rollback()
                    rollbacks.append('explicit')
            record = SimpleNamespace(connection=connection, factory=factory, pid=pid, outer=outer, rollback=rollback, commit=False)
            yield record
            if record.commit:
                phase = info['fixture_commit_phase']
                inserted = sorted(info['fixture_inserts'])
                await outer.commit()
                commits.append((phase, inserted))  # Driver acknowledgement, not engine commit-event intent.
            elif outer.is_active:
                await outer.rollback()
                rollbacks.append('actor')
        finally:
            if token is not None:
                actor_factory.reset(token)
            try:
                if outer is not None and outer.is_active:
                    await outer.rollback()
                    rollbacks.append('exception')
            finally:
                for key in ('fixture_inserts', 'fixture_writes', 'fixture_commit_phase', 'cleanup_verified'):
                    info.pop(key, None)
                connections.discard(connection)
                await connection.close()

    async def rows(model, **filters):
        async with actor() as current:
            async with current.factory() as session:
                query = select(model)
                for key, value in filters.items():
                    query = query.where(getattr(model, key) == value)
                return list((await session.execute(query)).scalars().all())

    async def passed(label):
        nonlocal checks
        checks += 1
        print(f'OK {checks}: {label}')

    async def create_task(cron='0 0 * * *'):
        async with actor(creation=True) as current:
            async with current.factory() as session:
                async with session.begin():
                    row = AgentTask(tenant_id=own, name=prefix + '-' + uuid4().hex[:6], job_type='learn',
                        cron_expr=cron, payload={'synthetic': prefix}, next_run_at=now, is_active=True)
                    session.add(row)
                    await session.flush()
                    owned['agent_tasks'].add(row.id)
            current.commit = True
        return row

    async def create_job(*, fresh=False, tenant=None):
        async with actor(creation=True) as current:
            async with current.factory() as session:
                async with session.begin():
                    row = Job(tenant_id=own if tenant is None else tenant, job_type='learn', status='running',
                        run_at=now, started_at=now, locked_at=now if fresh else now - timedelta(minutes=40),
                        attempts=1, max_attempts=3, payload={'synthetic': prefix},
                        result={'retained': prefix}, error='retained', llm_cost=1.25)
                    session.add(row)
                    await session.flush()
                    owned['jobs'].add(row.id)
            current.commit = True
        return row

    async def snapshot(row):
        read = (await rows(type(row), id=row.id))[0]
        return {column.key: getattr(read, column.key) for column in row.__table__.columns}

    async def reap_only(manager, job):
        # A fixture-ID bound supplements (does not replace) real normal tenant predicates.
        original = manager.filter
        with patch.object(manager, 'filter', lambda *a, **kw: original(*a, **kw).filter(id=job.id)):
            return await manager.reap_stale()

    async def sequence_snapshot():
        async with actor() as current:
            result = await current.connection.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'test_schema' "
                "AND sequencename IN ('tenants_id_seq', 'agent_tasks_id_seq', 'jobs_id_seq') ORDER BY sequencename"))
            return [tuple(row) for row in result]

    try:
        sequences_before = await sequence_snapshot()
        with patch.object(database, 'async_session_maker', routed_factory), patch.object(base_manager, 'async_session_maker', routed_factory):
            async with actor(creation=True) as current:
                async with current.factory() as session:
                    async with session.begin():
                        tenants = [Tenant(name=prefix, slug=prefix + suffix, plan='business', is_active=False)
                                   for suffix in ('-a', '-b')]
                        session.add_all(tenants)
                        await session.flush()
                        owned['tenants'].update(row.id for row in tenants)
                current.commit = True
            own, foreign = [row.id for row in tenants]
            manager, tasks = JobManager(), AgentTaskManager()

            # A and B have distinct backend PIDs; B runs while A still owns the
            # task lock. Only A commits the newly created fixture Job + schedule.
            for cron in ('0 0 * * *', '@once'):
                task = await create_task(cron)
                async with actor(creation=True) as a:
                    with tenant_scope(own):
                        receipt = await tasks._admit_task_run(task.id, now=now, expected_next_run_at=now, scheduled=True)
                    assert receipt['status'] == 'enqueued'
                    owned['jobs'].add(receipt['job'].id)
                    async with actor() as b:
                        assert a.pid != b.pid
                        with tenant_scope(own):
                            contender = await tasks._admit_task_run(task.id, now=now, expected_next_run_at=now, scheduled=True)
                        assert contender is None
                    a.commit = True
                async with actor():
                    with tenant_scope(own):
                        assert await tasks._admit_task_run(task.id, now=now, expected_next_run_at=now, scheduled=True) is None
                persisted = (await rows(AgentTask, id=task.id))[0]
                assert len(await rows(Job, agent_task_id=task.id)) == 1 and persisted.last_status == 'queued'
                assert (persisted.next_run_at is None and not persisted.is_active) if cron == '@once' else persisted.next_run_at > now
                await passed('distinct-connection ' + cron + ' admission commits exactly one Job and matching schedule')

            task = await create_task()
            before = await snapshot(task)
            try:
                async with actor(failing=True):
                    with tenant_scope(own):
                        await tasks._admit_task_run(task.id, now=now, expected_next_run_at=now, scheduled=True)
                raise AssertionError('Expected actual PostgreSQL flush error')
            except Exception as error:
                if isinstance(error, AssertionError):
                    raise
                assert 'division by zero' in str(error).lower()
            assert await snapshot(task) == before and not await rows(Job, agent_task_id=task.id)
            await passed('actual driver failure rolls back Job insertion and schedule together; no receipt')

            # Manual caller uses an old snapshot; actual API reloads the locked row.
            from app.jobs.enqueue import enqueue_task_run
            # Select the recurring one explicitly; never depend on ID set iteration.
            recurring = [row for row in await rows(AgentTask, tenant_id=own) if row.cron_expr != '@once' and row.last_status == 'queued'][0]
            previous_next = recurring.next_run_at
            stale = SimpleNamespace(id=recurring.id, tenant_id=own, next_run_at=now, job_type='prune', payload={})
            async with actor():
                with tenant_scope(own):
                    manual = await enqueue_task_run(stale, {'synthetic': prefix, 'manual': True})
                owned['jobs'].add(manual.id)
                async with actor_factory.get()() as session:
                    changed = (await session.execute(select(AgentTask).where(AgentTask.id == recurring.id))).scalar_one()
                    assert changed.next_run_at == previous_next and manual.job_type == 'learn'
            assert (await rows(AgentTask, id=recurring.id))[0].next_run_at == previous_next
            await passed('manual stale snapshot preserves fresh recurring schedule; caller rollback retains no new Job')

            async with actor():
                with tenant_scope(foreign):
                    assert await tasks._admit_task_run(task.id, now=now, scheduled=True, expected_next_run_at=now) is None
                with tenant_scope(None):
                    try:
                        await tasks._admit_task_run(task.id, now=now)
                        raise AssertionError('Missing tenant admitted')
                    except TenantContextError:
                        pass
            assert await snapshot(task) == before
            await passed('foreign/missing tenant admission fails closed without fixture writes')

            stale_job, foreign_job = await create_job(), await create_job(tenant=foreign)
            foreign_before = await snapshot(foreign_job)
            async with actor():
                with tenant_scope(own):
                    assert await reap_only(manager, stale_job) == 1
                    stopped = await snapshot_in_current(stale_job, actor_factory, select)
                    assert stopped.status == 'failed' and stopped.error.startswith(OUTCOME_UNCONFIRMED_PREFIX)
                    assert stopped.locked_at == stale_job.locked_at and stopped.result == stale_job.result and stopped.llm_cost == stale_job.llm_cost
                    assert stopped.attempts == stale_job.attempts
                    assert await manager.claim_job(stale_job.id) is None
                    lost = await manager.mark_done(stale_job.id, result={}, claim=JobClaim.capture(stale_job))
                    assert lost.acknowledgement == OutcomeAck.CLAIM_LOST
            assert await snapshot(foreign_job) == foreign_before
            await passed('stale quarantine keeps evidence and refuses replay/old finalization; foreign row unchanged; rollback')

            fresh = await create_job(fresh=True)
            async with actor():
                with tenant_scope(own):
                    assert await reap_only(manager, fresh) == 0
            await passed('fresh committed lease remains ineligible for scoped quarantine')

            # Independent connection row-lock wait verified through pg_stat_activity.
            # A renewal is intentionally rolled back; B then quarantines the OLD
            # committed lease. No claim of committed renewal/reaper ordering.
            async with actor() as a:
                with tenant_scope(own):
                    assert await manager.renew_claim(JobClaim.capture(stale_job)) is True
                started = asyncio.Event()
                release = asyncio.Event()
                result = {}
                async def contender():
                    async with actor() as b:
                        result['pid'] = b.pid
                        started.set()
                        with tenant_scope(own):
                            result['count'] = await reap_only(JobManager(), stale_job)
                        release.set()
                waiting = asyncio.create_task(contender())
                try:
                    await asyncio.wait_for(started.wait(), 3)
                    assert result['pid'] != a.pid
                    locked = False
                    for _ in range(100):
                        async with actor() as observer:
                            state = (await observer.connection.execute(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'),
                                {'pid': result['pid']})).scalar_one_or_none()
                        if state == 'Lock':
                            locked = True
                            break
                        await asyncio.sleep(.02)
                    assert locked and not release.is_set()
                    await a.rollback()
                    await asyncio.wait_for(waiting, 5)
                    assert result['count'] == 1
                finally:
                    if not waiting.done():
                        waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)
            await passed('distinct PostgreSQL backend reaper waits for renewal lock; rolled-back renewal exposes old lease')

            module = load_isolated_source('_owner_queue_recovery_dispatcher', ROOT/'app/jobs/dispatcher.py', imports={
                'app.jobs.handlers': SimpleNamespace(HANDLERS={})})
            async def no_notify(*args, **kwargs):
                return None
            module._notify_job_result = no_notify
            for failure, expected in ((ValueError('synthetic-private-error'), OutcomeAck.FAILED),
                                      (DeliveryFailure('synthetic_known_unsent', retryable=True), OutcomeAck.RETRY)):
                row = await create_job(fresh=True)
                async def handler(payload):
                    raise failure
                async with actor():
                    with tenant_scope(own):
                        receipt = await module.execute_job(row, handler)
                        assert receipt.acknowledgement == expected
                        stored = await snapshot_in_current(row, actor_factory, select)
                        if expected == OutcomeAck.FAILED:
                            assert stored.error == OUTCOME_UNCONFIRMED_PREFIX and stored.status == 'failed'
                            assert stored.result == row.result and stored.llm_cost == row.llm_cost
                        else:
                            assert stored.status == 'pending' and stored.run_at > datetime.now(timezone.utc)
                await passed('actual dispatcher/storage ' + expected.value + '; synthetic handler only; root rollback')

            # Explicit rollback plus owned connection close follows a simulated
            # effect. Another connection observes old lease and cannot auto-replay.
            effects = []
            async with actor() as a:
                with tenant_scope(own):
                    assert await manager.renew_claim(JobClaim.capture(stale_job)) is True
                effects.append(prefix)
                async with actor() as b:
                    assert a.pid != b.pid
                    await a.rollback()
                    await a.connection.close()
                    with tenant_scope(own):
                        assert await reap_only(manager, stale_job) == 1
                        assert await manager.claim_job(stale_job.id) is None
            assert effects == [prefix]
            await passed('explicit rollback and owned connection close after simulated effect do not authorize replay; no worker/provider kill claim')
    finally:
        try:
            # Only known committed tagged fixtures; no blanket tenant/global purge.
            with patch.object(database, 'async_session_maker', routed_factory), patch.object(base_manager, 'async_session_maker', routed_factory):
                if owned['tenants']:
                    async with actor(cleanup=True) as cleanup:
                        async with cleanup.factory() as session:
                            async with session.begin():
                                tenant_rows = (await session.execute(select(Tenant).where(Tenant.id.in_(owned['tenants'])))).scalars().all()
                                assert all(row.slug in {prefix+'-a', prefix+'-b'} and not row.is_active for row in tenant_rows)
                                jobs = (await session.execute(select(Job).where(Job.tenant_id.in_(owned['tenants'])))).scalars().all()
                                task_rows = (await session.execute(select(AgentTask).where(AgentTask.tenant_id.in_(owned['tenants'])))).scalars().all()
                                assert all((row.payload or {}).get('synthetic') == prefix for row in jobs)
                                assert all(row.name.startswith(prefix + '-') for row in task_rows)
                                owned['jobs'].update(row.id for row in jobs)
                                owned['agent_tasks'].update(row.id for row in task_rows)
                                if owned['jobs']:
                                    await session.execute(delete(Job).where(Job.tenant_id.in_(owned['tenants']), Job.id.in_(owned['jobs'])))
                                if owned['agent_tasks']:
                                    await session.execute(delete(AgentTask).where(AgentTask.tenant_id.in_(owned['tenants']), AgentTask.id.in_(owned['agent_tasks'])))
                                await session.execute(delete(Tenant).where(Tenant.id.in_(owned['tenants']), Tenant.slug.in_([prefix+'-a', prefix+'-b'])))
                        cleanup.connection.sync_connection.info['cleanup_verified'] = True
                        cleanup.commit = True
                    async with actor() as verify:
                        for model in (Tenant, AgentTask, Job):
                            column = model.id if model is Tenant else model.tenant_id
                            assert not (await verify.connection.execute(select(model.id).where(column.in_(owned['tenants'])))).all()
                    await passed('scoped tagged fixture cleanup committed and read-only verification found no leftovers')
            sequences_after = await sequence_snapshot()
        finally:
            event.remove(engine.sync_engine, 'before_cursor_execute', guard_write)
            event.remove(engine.sync_engine, 'commit', guard_commit)
            for connection in list(connections):
                await connection.close()
            await engine.dispose()
    print(f'{checks} checks passed; acknowledged commits only tagged creation/cleanup: {commits}')
    print(f'COMMIT attempts={len(commit_attempts)}; acknowledged={len(commits)}; rollback phases={len(rollbacks)}')
    print(f'Fixture sequences before={sequences_before}; after={sequences_after}; values may advance or be unavailable')
    print('No DDL/bootstrap/providers/pytest/old checks. Sequences may advance. UPDATE-only outcome/lease transactions rolled back; no real timer, worker kill or provider idempotency proof.')


async def snapshot_in_current(row, factory_context, select):
    async with factory_context.get()() as session:
        return (await session.execute(select(type(row)).where(type(row).id == row.id))).scalar_one()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-tagged-fixture-commits', action='store_true')
    args = parser.parse_args()
    if not args.allow_tagged_fixture_commits:
        parser.error('Explicit approval flag required; no DB action performed')
    validate_environment()
    asyncio.run(execute())
