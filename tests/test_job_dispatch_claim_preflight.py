"""New actual-dispatcher checks; no app imports, pytest, DB or provider calls."""

import asyncio
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = load_isolated_source('_preflight_claims', ROOT / 'app/jobs/claim_outcomes.py')
RESULTS = load_isolated_source('_preflight_results', ROOT / 'app/jobs/result_outcomes.py')


class DeliveryFailure(RuntimeError):
    retryable = False


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = []

        @contextmanager
        def tenant_scope(tenant_id=None, **kwargs):
            self.scope.append(tenant_id)
            try:
                yield
            finally:
                self.scope.pop()

        self.job = SimpleNamespace(id=11, tenant_id=31, agent_task_id=7, job_type='learn', status='running',
                                   attempts=2, started_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
                                   payload={'safe': 'input'}, result=None)
        self.claim = CLAIMS.JobClaim.capture(self.job)
        self.jobs = SimpleNamespace(renew_claim=AsyncMock(return_value=True), mark_done=AsyncMock(),
                                    mark_failed=AsyncMock(), get=AsyncMock())

        async def done(job_id, *, claim, result=None, **kwargs):
            return CLAIMS.JobOutcomeReceipt(claim, CLAIMS.OutcomeAck.DONE, result=result)

        async def failed(job_id, *, claim, result=None, error=None, allow_retry=False, **kwargs):
            return CLAIMS.JobOutcomeReceipt(claim, CLAIMS.OutcomeAck.RETRY if allow_retry else CLAIMS.OutcomeAck.FAILED,
                                            result=result, error=error)

        self.jobs.mark_done.side_effect = done
        self.jobs.mark_failed.side_effect = failed
        self.handler = AsyncMock(return_value={'status': 'ok'})
        self.digest = AsyncMock(return_value={'status': 'ok'})
        self.finalize_digest = AsyncMock(return_value=False)
        self.checkpoint = False
        self.module = load_isolated_source('_preflight_dispatcher',
            Path(os.environ.get('DISPATCHER_SOURCE', ROOT / 'app/jobs/dispatcher.py')), imports={
                'app.core.tenant_context': SimpleNamespace(tenant_scope=tenant_scope),
                'app.jobs.claim_outcomes': CLAIMS,
                'app.jobs.result_outcomes': RESULTS,
                'app.jobs.handlers': SimpleNamespace(HANDLERS={'learn': self.handler}),
                'app.models.managers.job_manager': SimpleNamespace(JobManager=lambda: self.jobs),
                'app.services.digest.delivery_outcomes': SimpleNamespace(DeliveryFailure=DeliveryFailure),
                'app.services.digest.job_delivery': SimpleNamespace(REFERENCE_KEY='checkpoint',
                    enabled=lambda: self.checkpoint, execute_digest_job=self.digest,
                    finalize_digest_job=self.finalize_digest),
            })
        self.notify = AsyncMock()
        self.module._notify_job_result = self.notify

    async def execute(self, allow_retry=True):
        return await self.module.execute_job(self.job, self.handler, allow_retry=allow_retry)

    def assert_no_effects(self):
        for callback in (self.handler, self.digest, self.jobs.mark_done, self.jobs.mark_failed, self.notify):
            callback.assert_not_awaited()

    async def test_renewal_acknowledgement_precedes_dispatch(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def renew(claim):
            self.assertEqual(claim, self.claim)
            self.assertEqual(self.scope, [31])
            entered.set()
            await release.wait()
            return True
        self.jobs.renew_claim.side_effect = renew
        task = asyncio.create_task(self.execute())
        try:
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            self.assert_no_effects()
            release.set()
            receipt = await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.DONE)
        self.handler.assert_awaited_once_with({'safe': 'input', 'job_id': 11, 'agent_task_id': 7})
        self.jobs.mark_done.assert_awaited_once()

    async def test_false_ownership_executes_nothing(self):
        self.jobs.renew_claim.return_value = False
        with self.assertRaises(CLAIMS.JobClaimLostError):
            await self.execute()
        self.assert_no_effects()

    async def test_malformed_acknowledgement_is_not_truthy_admission(self):
        for value in (None, 1, 0, 'yes', {}, {'owned': True}, object()):
            with self.subTest(value=type(value).__name__):
                self.jobs.renew_claim.return_value = value
                with self.assertRaises(self.module.JobClaimRenewalError):
                    await self.execute()
                self.assert_no_effects()

    async def test_database_error_stays_outside_retry_and_outcomes(self):
        self.jobs.renew_claim.side_effect = ConnectionError('PRIVATE-DB-ERROR')
        with self.assertRaises(self.module.JobClaimRenewalError) as caught:
            await self.execute()
        self.assertEqual(caught.exception.error_code, 'claim_renewal_unconfirmed')
        self.assertEqual((caught.exception.job_id, caught.exception.tenant_id), (11, 31))
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assert_no_effects()

    async def test_missing_renewal_api_fails_closed(self):
        del self.jobs.renew_claim
        with self.assertRaises(self.module.JobClaimRenewalError):
            await self.execute()
        self.assert_no_effects()

    async def test_actual_cancellation_while_waiting_does_not_dispatch(self):
        entered = asyncio.Event()
        async def renew(claim):
            entered.set()
            await asyncio.Event().wait()
        self.jobs.renew_claim.side_effect = renew
        task = asyncio.create_task(self.execute())
        try:
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assert_no_effects()

    async def test_invalid_claim_is_rejected_before_renewal(self):
        self.job.status = 'pending'
        with self.assertRaises(ValueError):
            await self.execute()
        self.jobs.renew_claim.assert_not_awaited()
        self.assert_no_effects()

    async def test_immutable_claim_survives_handler_mutation(self):
        async def handler(payload):
            self.job.attempts = 99
            self.job.started_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
            return {'status': 'ok'}
        self.handler.side_effect = handler
        receipt = await self.execute()
        self.assertEqual(receipt.claim, self.claim)
        self.assertEqual(self.jobs.renew_claim.await_args.args, (self.claim,))
        self.assertEqual(self.jobs.mark_done.await_args.kwargs['claim'], self.claim)

    async def test_normal_handler_exception_retains_retry_policy(self):
        self.handler.side_effect = ValueError('normal handler failure')
        receipt = await self.execute()
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.RETRY)
        self.assertTrue(self.jobs.mark_failed.await_args.kwargs['allow_retry'])
        self.jobs.mark_done.assert_not_awaited()

    async def test_nonretryable_delivery_failure_stays_terminal(self):
        self.handler.side_effect = DeliveryFailure('known failure')
        receipt = await self.execute()
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.FAILED)
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs['allow_retry'])

    async def test_returned_failure_retains_nonretryable_outcome(self):
        self.handler.return_value = {'status': 'failed', 'error': 'invalid_structured_output', 'llm_cost': 0.25}
        receipt = await self.execute()
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.FAILED)
        self.assertEqual(self.jobs.mark_failed.await_args.kwargs['llm_cost'], 0.25)
        self.assertFalse(self.jobs.mark_failed.await_args.kwargs['allow_retry'])

    async def test_legacy_digest_is_gated_before_digest_boundary(self):
        self.job.job_type = 'digest'
        self.jobs.renew_claim.return_value = False
        with self.assertRaises(CLAIMS.JobClaimLostError):
            await self.execute()
        self.assert_no_effects()
        self.finalize_digest.assert_not_awaited()

    async def test_successful_legacy_digest_keeps_ordinary_finalizer(self):
        self.job.job_type = 'digest'
        receipt = await self.execute()
        self.digest.assert_awaited_once()
        self.handler.assert_not_awaited()
        self.jobs.mark_done.assert_awaited_once()
        self.finalize_digest.assert_not_awaited()
        self.assertEqual(receipt.acknowledgement, CLAIMS.OutcomeAck.DONE)

    async def test_checkpoint_digest_protocol_is_not_double_renewed(self):
        self.job.job_type = 'digest'
        self.checkpoint = True
        self.assertIsNone(await self.execute())
        self.jobs.renew_claim.assert_not_awaited()
        self.digest.assert_awaited_once()
        self.finalize_digest.assert_awaited_once()
        self.jobs.mark_done.assert_not_awaited()

    async def test_persisted_checkpoint_routing_remains_exempt(self):
        self.job.job_type = 'digest'
        self.job.result = {'checkpoint': {'phase': 'bound'}}
        self.assertIsNone(await self.execute())
        self.jobs.renew_claim.assert_not_awaited()
        self.digest.assert_awaited_once()

    async def test_handler_cancellation_never_becomes_failed_outcome(self):
        self.handler.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.execute()
        self.jobs.mark_failed.assert_not_awaited()
        self.jobs.mark_done.assert_not_awaited()
        self.notify.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
