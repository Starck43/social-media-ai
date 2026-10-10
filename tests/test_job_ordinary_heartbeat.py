"""Actual dispatcher + stdlib task interleavings; no DB/provider/old suite."""

import asyncio
import os
import unittest
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = load_isolated_source('_ordinary_heartbeat_claims', ROOT / 'app/jobs/claim_outcomes.py')
RESULTS = load_isolated_source('_ordinary_heartbeat_results', ROOT / 'app/jobs/result_outcomes.py')


class DeliveryFailure(RuntimeError):
    retryable = False


class Clock:
    """Release timer ticks explicitly; never wait wall-clock 20 seconds."""
    def __init__(self):
        self.requested = asyncio.Queue()
        self.releases = asyncio.Queue()

    async def sleep(self, seconds):
        await self.requested.put(seconds)
        await self.releases.get()

    async def tick(self):
        seconds = await asyncio.wait_for(self.requested.get(), 1)
        if seconds != 20:
            raise AssertionError('unexpected heartbeat interval')
        self.releases.put_nowait(None)
        await asyncio.sleep(0)


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant = ContextVar('test_tenant', default=None)

        @contextmanager
        def scope(tenant_id=None, **kwargs):
            token = self.tenant.set(tenant_id)
            try:
                yield
            finally:
                self.tenant.reset(token)

        self.clock = Clock()
        self.events = []
        self.renewed = asyncio.Queue()
        self.job = SimpleNamespace(id=11, tenant_id=31, agent_task_id=7, job_type='learn', status='running',
            attempts=2, started_at=datetime(2026, 10, 10, tzinfo=timezone.utc), payload={}, result=None)
        self.claim = CLAIMS.JobClaim.capture(self.job)
        self.jobs = SimpleNamespace(renew_claim=AsyncMock(), mark_done=AsyncMock(), mark_failed=AsyncMock())
        self.ack = True

        async def renew(claim):
            self.assertEqual(self.tenant.get(), 31)
            self.assertEqual(claim, self.claim)
            self.events.append('renew')
            self.renewed.put_nowait(claim)
            if isinstance(self.ack, BaseException):
                raise self.ack
            return self.ack

        async def done(job_id, *, claim, result=None, **kwargs):
            self.events.append('done')
            return CLAIMS.JobOutcomeReceipt(claim, CLAIMS.OutcomeAck.DONE, result=result)

        async def failed(job_id, *, claim, result=None, error=None, allow_retry=False, **kwargs):
            self.events.append('failed')
            return CLAIMS.JobOutcomeReceipt(claim, CLAIMS.OutcomeAck.RETRY if allow_retry else CLAIMS.OutcomeAck.FAILED,
                result=result, error=error)

        self.jobs.renew_claim.side_effect = renew
        self.jobs.mark_done.side_effect = done
        self.jobs.mark_failed.side_effect = failed
        self.started = asyncio.Event()
        self.release_handler = asyncio.Event()
        self.drained = asyncio.Event()

        async def handler(payload):
            self.assertEqual(self.tenant.get(), 31)
            self.events.append('handler')
            self.started.set()
            try:
                await self.release_handler.wait()
                return {'status': 'ok'}
            finally:
                self.events.append('handler_drained')
                self.drained.set()

        self.handler = AsyncMock(side_effect=handler)
        self.digest = AsyncMock(side_effect=lambda job, payload, callback: callback(payload))
        # AsyncMock does not await a coroutine returned by a synchronous lambda.
        async def digest(job, payload, callback):
            return await callback(payload)
        self.digest.side_effect = digest
        self.finalize_digest = AsyncMock(return_value=False)
        self.checkpoint = False
        self.module = load_isolated_source('_ordinary_heartbeat_dispatcher', Path(os.environ.get('DISPATCHER_SOURCE', ROOT/'app/jobs/dispatcher.py')), imports={
            'app.core.tenant_context': SimpleNamespace(tenant_scope=scope),
            'app.jobs.claim_outcomes': CLAIMS,
            'app.jobs.result_outcomes': RESULTS,
            'app.jobs.handlers': SimpleNamespace(HANDLERS={}),
            'app.models.managers.job_manager': SimpleNamespace(JobManager=lambda: self.jobs),
            'app.services.digest.delivery_outcomes': SimpleNamespace(DeliveryFailure=DeliveryFailure),
            'app.services.digest.job_delivery': SimpleNamespace(REFERENCE_KEY='checkpoint', enabled=lambda:self.checkpoint,
                execute_digest_job=self.digest, finalize_digest_job=self.finalize_digest),
        })
        self.module.asyncio = SimpleNamespace(**{name:getattr(asyncio, name) for name in (
            'create_task','wait','FIRST_COMPLETED','CancelledError','gather','shield','Event')}, sleep=self.clock.sleep)
        self.notify = AsyncMock()
        self.module._notify_job_result = self.notify
        self.running = []

    async def asyncTearDown(self):
        self.release_handler.set()
        for task in self.running:
            if not task.done():
                task.cancel()
        await asyncio.wait_for(asyncio.gather(*self.running, return_exceptions=True), 1)
        self.assertIsNone(self.tenant.get())

    def start(self, **kwargs):
        task = asyncio.create_task(self.module.execute_job(self.job, self.handler, **kwargs))
        self.running.append(task)
        return task

    async def begin(self):
        task = self.start()
        await asyncio.wait_for(self.started.wait(), 1)
        await asyncio.wait_for(self.renewed.get(), 1)  # preflight
        return task

    async def periodic(self):
        await self.clock.tick()
        await asyncio.wait_for(self.renewed.get(), 1)
        await asyncio.sleep(0)

    async def finish(self, task):
        self.release_handler.set()
        return await asyncio.wait_for(task, 1)

    def no_outcome(self):
        self.jobs.mark_done.assert_not_awaited()
        self.jobs.mark_failed.assert_not_awaited()
        self.notify.assert_not_awaited()

    async def test_multiple_ticks_preserve_claim_and_tenant(self):
        task=await self.begin()
        await self.periodic(); await self.periodic()
        receipt=await self.finish(task)
        self.assertEqual(self.jobs.renew_claim.await_count,3)
        self.assertEqual(receipt.claim,self.claim)
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.DONE)
        self.assertLess(self.events.index('handler_drained'),self.events.index('done'))

    async def test_fast_handler_cancels_timer_without_periodic_write(self):
        self.release_handler.set()
        receipt=await asyncio.wait_for(self.start(),1)
        self.assertEqual(self.jobs.renew_claim.await_count,1)
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.DONE)

    async def test_loss_stops_and_drains_before_return(self):
        task=await self.begin(); self.ack=False
        await self.periodic()
        with self.assertRaises(CLAIMS.JobClaimLostError): await asyncio.wait_for(task,1)
        self.assertTrue(self.drained.is_set()); self.no_outcome()

    async def test_database_failure_is_uncertain_not_handler_retry(self):
        task=await self.begin(); self.ack=ConnectionError('PRIVATE-SQL-TOKEN')
        await self.periodic()
        with self.assertRaises(self.module.JobClaimRenewalError) as error: await asyncio.wait_for(task,1)
        self.assertNotIn('PRIVATE',str(error.exception)); self.no_outcome()
        self.assertTrue(self.drained.is_set())

    async def test_malformed_periodic_acknowledgement_fails_closed(self):
        task=await self.begin(); self.ack={'owned':True}
        await self.periodic()
        with self.assertRaises(self.module.JobClaimRenewalError): await asyncio.wait_for(task,1)
        self.no_outcome()

    async def test_cancelled_renewal_task_is_uncertain(self):
        task=await self.begin(); self.ack=asyncio.CancelledError()
        await self.periodic()
        with self.assertRaises(self.module.JobClaimRenewalError): await asyncio.wait_for(task,1)
        self.no_outcome()

    async def test_handler_suppresses_cancel_result_still_not_saved(self):
        async def stubborn(payload):
            self.started.set()
            try: await self.release_handler.wait()
            except asyncio.CancelledError: return {'status':'ok','cost':99}
            finally: self.drained.set()
        self.handler.side_effect=stubborn
        task=await self.begin(); self.ack=False; await self.periodic()
        with self.assertRaises(CLAIMS.JobClaimLostError): await asyncio.wait_for(task,1)
        self.assertTrue(self.drained.is_set()); self.no_outcome()

    async def test_lease_loss_waits_for_cooperative_handler_cleanup(self):
        cleanup_started=asyncio.Event(); cleanup_release=asyncio.Event()
        async def slow_cleanup(payload):
            self.started.set()
            try: await self.release_handler.wait()
            finally:
                cleanup_started.set()
                await cleanup_release.wait()
                self.drained.set()
        self.handler.side_effect=slow_cleanup
        task=await self.begin();self.ack=False;await self.periodic()
        await asyncio.wait_for(cleanup_started.wait(),1)
        self.assertFalse(task.done());self.no_outcome()
        cleanup_release.set()
        with self.assertRaises(CLAIMS.JobClaimLostError):await asyncio.wait_for(task,1)
        self.assertTrue(self.drained.is_set())

    async def test_actual_caller_cancel_drains_both_tasks(self):
        task=await self.begin();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        self.assertTrue(self.drained.is_set());self.no_outcome()
        calls=self.jobs.renew_claim.await_count
        self.clock.releases.put_nowait(None);await asyncio.sleep(0)
        self.assertEqual(self.jobs.renew_claim.await_count,calls)

    async def test_repeated_caller_cancel_does_not_abandon_cleanup(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def cleanup(payload):
            self.started.set()
            try:await self.release_handler.wait()
            finally:
                entered.set();await release.wait();self.drained.set()
        self.handler.side_effect=cleanup
        task=await self.begin();task.cancel();await asyncio.wait_for(entered.wait(),1)
        task.cancel();await asyncio.sleep(0)
        self.assertFalse(task.done());release.set()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        self.assertTrue(self.drained.is_set());self.no_outcome()

    async def test_completion_waits_for_inflight_renewal_acknowledgement(self):
        task=await self.begin();entered=asyncio.Event();release=asyncio.Event()
        async def renew(claim):
            entered.set();await release.wait();return True
        self.jobs.renew_claim.side_effect=renew
        await self.clock.tick();await asyncio.wait_for(entered.wait(),1)
        self.release_handler.set();await asyncio.sleep(0);await asyncio.sleep(0)
        self.assertFalse(task.done());self.no_outcome()
        release.set();receipt=await asyncio.wait_for(task,1)
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.DONE)

    async def test_inflight_failure_dominates_completed_handler(self):
        task=await self.begin();entered=asyncio.Event();release=asyncio.Event()
        async def renew(claim):
            entered.set();await release.wait();raise TimeoutError('PRIVATE')
        self.jobs.renew_claim.side_effect=renew
        await self.clock.tick();await asyncio.wait_for(entered.wait(),1)
        self.release_handler.set();await asyncio.sleep(0);await asyncio.sleep(0)
        release.set()
        with self.assertRaises(self.module.JobClaimRenewalError):await asyncio.wait_for(task,1)
        self.no_outcome()

    async def test_simultaneous_handler_success_and_loss_is_not_done(self):
        task=await self.begin()
        async def renew(claim):
            self.release_handler.set();return False
        self.jobs.renew_claim.side_effect=renew
        await self.clock.tick()
        with self.assertRaises(CLAIMS.JobClaimLostError):await asyncio.wait_for(task,1)
        self.no_outcome()

    async def test_handler_exception_retains_retry_policy_after_renewal(self):
        async def failed(payload):
            self.started.set()
            await self.release_handler.wait()
            raise ValueError('handler failure')
        self.handler.side_effect=failed
        task=await self.begin()
        await self.periodic()
        receipt=await self.finish(task)
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.RETRY)
        self.jobs.mark_done.assert_not_awaited()

    async def test_handler_owned_error_is_not_confused_with_monitor_stop(self):
        async def failed(payload):raise self.module.JobClaimRenewalError(self.claim)
        self.handler.side_effect=failed
        receipt=await asyncio.wait_for(self.start(),1)
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.RETRY)
        self.jobs.mark_failed.assert_awaited_once()

    async def test_returned_failure_keeps_known_cost_and_no_retry(self):
        async def failed(payload):return {'status':'failed','error':'invalid_structured_output','llm_cost':0.25}
        self.handler.side_effect=failed
        receipt=await asyncio.wait_for(self.start(),1)
        self.assertEqual(receipt.acknowledgement,CLAIMS.OutcomeAck.FAILED)
        self.assertEqual(self.jobs.mark_failed.await_args.kwargs['llm_cost'],0.25)
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs['allow_retry'])

    async def test_actual_handler_cancel_remains_cancelled_not_failed(self):
        async def cancel(payload):raise asyncio.CancelledError()
        self.handler.side_effect=cancel
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(self.start(),1)
        self.no_outcome()

    async def test_mutated_observed_job_cannot_change_heartbeat_claim(self):
        task=await self.begin();self.job.attempts=99
        self.job.started_at=datetime(2027,1,1,tzinfo=timezone.utc)
        await self.periodic();receipt=await self.finish(task)
        self.assertEqual(receipt.claim,self.claim)
        self.assertEqual(self.jobs.mark_done.await_args.kwargs['claim'],self.claim)

    async def test_legacy_digest_gets_ordinary_supervision(self):
        self.job.job_type='digest';self.claim=CLAIMS.JobClaim.capture(self.job)
        task=await self.begin();await self.periodic();await self.finish(task)
        self.digest.assert_awaited_once();self.finalize_digest.assert_not_awaited()
        self.assertEqual(self.jobs.renew_claim.await_count,2)

    async def test_checkpoint_digest_retains_its_protocol(self):
        self.job.job_type='digest';self.checkpoint=True
        self.release_handler.set();self.assertIsNone(await asyncio.wait_for(self.start(),1))
        self.jobs.renew_claim.assert_not_awaited();self.finalize_digest.assert_awaited_once()
        self.jobs.mark_done.assert_not_awaited()

    async def test_preflight_false_still_creates_no_handler_or_timer(self):
        self.ack=False;task=self.start()
        with self.assertRaises(CLAIMS.JobClaimLostError):await asyncio.wait_for(task,1)
        self.handler.assert_not_awaited();self.assertTrue(self.clock.requested.empty());self.no_outcome()

    async def test_preflight_uncertainty_still_creates_no_handler_or_timer(self):
        self.ack=ConnectionError('private');task=self.start()
        with self.assertRaises(self.module.JobClaimRenewalError):await asyncio.wait_for(task,1)
        self.handler.assert_not_awaited();self.assertTrue(self.clock.requested.empty());self.no_outcome()


if __name__=='__main__':
    unittest.main()
