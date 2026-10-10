"""OWNER ONLY: actual PG supervision/reaper interleavings, caller rollback.

Standalone, not pytest. Reuse the existing local shared test_schema sequentially.
No bootstrap/DDL/seed helper/global bypass/DELETE/provider/job effects.
Real production renewal/finalizer/reaper run only for a synthetic tenant.
One connection/outer transaction/savepoints: NOT inter-connection lock race proof.
Timer ticks are explicitly released; no wall-clock 20s/30m wait is claimed.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_job_claim_heartbeat_db import validate_environment  # Guard only; never execute its old checks.


async def execute():
    # All application imports follow the existing host/schema guard in main().
    from sqlalchemy import event, select, text, update
    from sqlalchemy.sql import operators, visitors
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.core import database
    from app.core.config import settings
    from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
    from app.jobs import claim_outcomes, result_outcomes
    from app.jobs.recovery_policy import OUTCOME_UNCONFIRMED_PREFIX
    from app.models import Job, Tenant
    from app.models.managers import base_manager
    from app.models.managers.job_manager import JobManager
    from source_import_isolation import load_isolated_source
    from test_job_ordinary_heartbeat import Clock  # Pure timer seam; no unittest invocation.

    if settings.DB_SCHEMA != 'test_schema' or any(model.__table__.schema != 'test_schema' for model in (Tenant, Job)):
        raise RuntimeError('Stop: approved test_schema mapping required')
    engine = database.async_engine
    commits = []
    prefix = 'ordinary-heartbeat-' + uuid4().hex[:16]
    tenant_ids, job_ids, running, releases = [], [], [], []
    connection = outer = None
    checks = 0
    reaper_active = False

    def reject_commit(connection):
        commits.append(True)
        raise RuntimeError('Stop: actual COMMIT forbidden')

    def guard_reaper_scope(connection, cursor, statement, parameters, context, executemany):
        if not reaper_active or not statement.lstrip().upper().startswith('UPDATE'):
            return
        compiled = getattr(context, 'compiled', None)
        query = getattr(compiled, 'statement', None)
        criteria = getattr(query, '_where_criteria', ())
        own = tenant_ids[0]
        fenced = any(
            getattr(node, 'operator', None) is operators.eq
            and getattr(getattr(node, 'left', None), 'name', None) == 'tenant_id'
            and getattr(getattr(getattr(node, 'left', None), 'table', None), 'name', None) == 'jobs'
            and getattr(getattr(node, 'right', None), 'value', None) == own
            for clause in criteria for node in visitors.iterate(clause)
        )
        if not fenced:
            raise RuntimeError('Stop: reaper UPDATE lacks exact synthetic tenant predicate')

    event.listen(engine.sync_engine, 'commit', reject_commit)
    event.listen(engine.sync_engine, 'before_cursor_execute', guard_reaper_scope)
    try:
        connection = await engine.connect()
        outer = await connection.begin()
        await connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
        await connection.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
        factory = async_sessionmaker(bind=connection, class_=AsyncSession, expire_on_commit=False,
                                    join_transaction_mode='create_savepoint')
        with patch.object(database, 'async_session_maker', factory), patch.object(base_manager, 'async_session_maker', factory):
            async with factory() as session:
                async with session.begin():
                    tenants = [Tenant(name='Synthetic ordinary heartbeat', slug=prefix + suffix, plan='business')
                               for suffix in ('-own', '-foreign')]
                    session.add_all(tenants)
                    await session.flush()
                    tenant_ids.extend(tenant.id for tenant in tenants)
            tenant_id = tenant_ids[0]
            manager = JobManager()

            async def make_job(owner_id=None):
                now = datetime.now(timezone.utc)
                async with factory() as session:
                    async with session.begin():
                        job = Job(tenant_id=tenant_id if owner_id is None else owner_id, job_type='learn', agent_task_id=None,
                                  status='running', attempts=1, max_attempts=3, run_at=now, started_at=now,
                                  locked_at=now - timedelta(minutes=40), payload={'synthetic': prefix})
                        session.add(job)
                        await session.flush()
                        job_ids.append(job.id)
                return job

            async def scoped_reap():
                nonlocal reaper_active
                with tenant_scope(tenant_id):
                    assert current_tenant_id() == tenant_id and not is_bypass()
                    reaper_active = True
                    try:
                        return await manager.reap_stale()
                    finally:
                        reaper_active = False

            async def snapshot(job):
                async with factory() as session:
                    row = (await session.execute(select(Job).where(Job.id == job.id, Job.tenant_id == job.tenant_id))).scalar_one()
                    return {column.key: getattr(row, column.key) for column in Job.__table__.columns}

            async def change(job, **values):
                async with factory() as session:
                    async with session.begin():
                        await session.execute(update(Job).where(Job.id == job.id, Job.tenant_id == tenant_id).values(**values))

            class Probe:
                def __init__(self):
                    self.calls = 0
                    self.finished = asyncio.Queue()
                    self.entered = asyncio.Event()
                    self.release = asyncio.Event()
                    self.hold = False
                    self.fail = False
                    self.notifications = []
                    releases.append(self.release)

                async def renew_claim(self, claim):
                    assert current_tenant_id() == tenant_id and not is_bypass()
                    self.calls += 1
                    if self.calls > 1 and self.hold:
                        self.entered.set()
                        await self.release.wait()
                    if self.calls > 1 and self.fail:
                        # Actual PostgreSQL error in a savepoint, not a fake driver exception.
                        async with factory() as session:
                            async with session.begin():
                                await session.execute(text('SELECT 1 / 0'))
                    result = await manager.renew_claim(claim)
                    self.finished.put_nowait(result)
                    return result

                def __getattr__(self, name):
                    return getattr(manager, name)

            async def prepare(job, swallow_cancel=False):
                clock, probe = Clock(), Probe()
                started, release_handler, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
                releases.append(release_handler)

                async def handler(payload):
                    assert current_tenant_id() == tenant_id and not is_bypass()
                    assert payload['job_id'] == job.id
                    started.set()
                    try:
                        await release_handler.wait()
                        return {'status': 'ok', 'synthetic': prefix}
                    except asyncio.CancelledError:
                        if swallow_cancel:
                            return {'status': 'ok', 'synthetic': prefix}
                        raise
                    finally:
                        drained.set()

                module = load_isolated_source('_owner_ordinary_heartbeat_dispatcher', ROOT/'app/jobs/dispatcher.py', imports={
                    'app.jobs.handlers': SimpleNamespace(HANDLERS={}),
                    'app.models.managers.job_manager': SimpleNamespace(JobManager=lambda: probe),
                })
                module.asyncio = SimpleNamespace(**{name: getattr(asyncio, name) for name in (
                    'create_task', 'wait', 'FIRST_COMPLETED', 'CancelledError', 'gather', 'shield', 'Event')}, sleep=clock.sleep)
                async def notify(*args, **kwargs):
                    probe.notifications.append(kwargs)
                module._notify_job_result = notify
                task = asyncio.create_task(module.execute_job(job, handler))
                running.append(task)
                await asyncio.wait_for(started.wait(), 5)
                assert await asyncio.wait_for(probe.finished.get(), 5) is True  # preflight
                return SimpleNamespace(clock=clock, probe=probe, task=task, module=module,
                    release_handler=release_handler, drained=drained)

            async def periodic(run):
                await run.clock.tick()
                return await asyncio.wait_for(run.probe.finished.get(), 5)

            async def finish(run):
                run.release_handler.set()
                return await asyncio.wait_for(run.task, 5)

            async def passed(label):
                nonlocal checks
                checks += 1
                assert not commits
                print('OK ' + str(checks) + ': ' + label)

            foreign = await make_job(owner_id=tenant_ids[1])
            foreign_before = await snapshot(foreign)
            job = await make_job()
            run = await prepare(job)
            await change(job, locked_at=datetime.now(timezone.utc) - timedelta(minutes=40))
            assert await periodic(run) is True
            assert await scoped_reap() == 0
            assert (await snapshot(job))['status'] == 'running'
            assert await snapshot(foreign) == foreign_before
            receipt = await finish(run)
            assert receipt.acknowledgement == claim_outcomes.OutcomeAck.DONE
            assert (await snapshot(job))['status'] == 'done'
            await passed('periodic actual renewal keeps own synthetic job running through scoped reaper')

            job = await make_job()
            run = await prepare(job)
            await change(job, locked_at=datetime.now(timezone.utc) - timedelta(minutes=40))
            assert await scoped_reap() == 1
            stopped = await snapshot(job)
            assert stopped['status'] == 'failed' and stopped['error'].startswith(OUTCOME_UNCONFIRMED_PREFIX)
            with tenant_scope(tenant_id):
                assert await manager.claim_job(job.id) is None  # No automatic replay after quarantine.
            before = await snapshot(job)
            assert await periodic(run) is False
            try:
                await asyncio.wait_for(run.task, 5)
                raise AssertionError('Expected claim loss')
            except claim_outcomes.JobClaimLostError:
                pass
            assert run.drained.is_set() and not run.probe.notifications
            assert await snapshot(job) == before
            assert await snapshot(foreign) == foreign_before
            await passed('scoped quarantine forbids reacquisition and drains old handler without rewriting stopped evidence')

            job = await make_job()
            run = await prepare(job)
            before = await snapshot(job)
            run.probe.fail = True
            await run.clock.tick()
            try:
                await asyncio.wait_for(run.task, 5)
                raise AssertionError('Expected renewal uncertainty')
            except run.module.JobClaimRenewalError:
                pass
            assert run.drained.is_set() and not run.probe.notifications
            assert await snapshot(job) == before
            await passed('actual PostgreSQL renewal-path error stops handler without retry/outcome')

            job = await make_job()
            run = await prepare(job)
            assert await periodic(run) is True
            before = await snapshot(job)
            run.task.cancel()
            try:
                await asyncio.wait_for(run.task, 5)
                raise AssertionError('Expected cancellation')
            except asyncio.CancelledError:
                pass
            assert run.drained.is_set() and not run.probe.notifications
            assert await snapshot(job) == before
            await passed('caller cancellation after actual renewal drains tasks without outcome')

            job = await make_job()
            run = await prepare(job)
            run.probe.hold = True
            await run.clock.tick()
            await asyncio.wait_for(run.probe.entered.wait(), 5)
            run.release_handler.set()
            await asyncio.wait_for(run.drained.wait(), 5)
            await asyncio.sleep(0)
            assert not run.task.done() and (await snapshot(job))['status'] == 'running'
            run.probe.release.set()
            receipt = await asyncio.wait_for(run.task, 5)
            assert receipt.acknowledgement == claim_outcomes.OutcomeAck.DONE
            assert (await snapshot(job))['status'] == 'done'
            await passed('handler completion waits for held actual renewal acknowledgement before finalization')

            job = await make_job()
            run = await prepare(job)
            run.probe.hold = run.probe.fail = True
            before = await snapshot(job)
            await run.clock.tick()
            await asyncio.wait_for(run.probe.entered.wait(), 5)
            run.release_handler.set()
            await asyncio.wait_for(run.drained.wait(), 5)
            run.probe.release.set()
            try:
                await asyncio.wait_for(run.task, 5)
                raise AssertionError('Expected uncertainty over completed handler')
            except run.module.JobClaimRenewalError:
                pass
            assert not run.probe.notifications and await snapshot(job) == before
            await passed('actual driver error dominates already-completed handler; no success or retry')

            job = await make_job()
            run = await prepare(job, swallow_cancel=True)
            await change(job, attempts=2, started_at=datetime.now(timezone.utc))
            before = await snapshot(job)
            assert await periodic(run) is False
            try:
                await asyncio.wait_for(run.task, 5)
                raise AssertionError('Expected claim loss despite cancelled handler result')
            except claim_outcomes.JobClaimLostError:
                pass
            assert run.drained.is_set() and not run.probe.notifications
            assert await snapshot(job) == before
            await passed('cancel-suppressing handler result never overwrites newer generation')
    finally:
        try:
            for release in releases:
                release.set()
            for task in running:
                if not task.done():
                    task.cancel()
            await asyncio.wait_for(asyncio.gather(*running, return_exceptions=True), 15)
        finally:
            try:
                if outer is not None and outer.is_active:
                    await outer.rollback()
                if connection is not None:
                    await connection.close()
                if tenant_ids:
                    async with engine.connect() as verify:
                        for model, ids in ((Tenant, tenant_ids), (Job, job_ids)):
                            assert not (await verify.execute(select(model.id).where(model.id.in_(ids)))).all()
                        await verify.rollback()
            finally:
                event.remove(engine.sync_engine, 'before_cursor_execute', guard_reaper_scope)
                event.remove(engine.sync_engine, 'commit', reject_commit)
                await engine.dispose()
    assert not commits
    checks += 1
    assert checks == 8
    print('OK 8: outer rollback/no synthetic leftovers/COMMIT=0')
    print('8 checks passed; no bootstrap/DDL/global bypass/DELETE/provider actions; sequences may advance')
    print('Single-connection savepoint/task interleavings, not independent-connection races or real timer-duration acceptance')


def main():
    validate_environment()
    asyncio.run(execute())


if __name__ == '__main__':
    main()
