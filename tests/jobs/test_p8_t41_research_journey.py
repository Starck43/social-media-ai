"""Actual orchestrator/auth/report generation, with all I/O explicitly doubled."""

import unittest
from copy import deepcopy

from tests.shared.vk_research_execution import ResearchHarness
from tests.shared.vk_research_journey import journey_plan

STAGES = journey_plan()["pipeline"]["stages"]
CAPABILITIES = [(99, "ai.analyze"), (99, "social.collect")]


class ResearchJourneyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = ResearchHarness()

    async def test_admitted_snapshot_to_preview_has_scoped_provenance_and_no_send(self):
        result = await self.h.pipeline.execute_task_pipeline(self.h.payload())
        self.assertEqual(self.h.order, ["collect", "analyze", "digest"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual((result["collected"], result["analyzed"]), (2, 2))
        self.assertEqual(result["per_source"][0]["analytics_ids"], [123, 124])
        self.assertEqual(result["digest"]["source_ids"], [11])
        self.assertEqual(result["digest"]["scenario_id"], 42)
        self.assertIn("https://vk.com/wall-100_1", result["digest"]["preview_text"])
        self.assertTrue(result["digest"]["generated"])
        self.assertEqual(result["digest"]["delivered"], 0)
        self.assertEqual(
            self.h.stage_transitions(), [(stage, state) for stage in STAGES for state in ("started", "completed")]
        )
        self.assertEqual([job for _, job in self.h.entitlement_calls], [99] * len(STAGES))
        self.assertEqual(self.h.usage_rows, CAPABILITIES)
        self.h.send.assert_not_awaited()

    async def test_completed_retry_rechecks_rights_but_does_not_repeat_work(self):
        result = await self.h.pipeline.execute_task_pipeline(self.h.payload())
        checkpoints = deepcopy(self.h.checkpoints)
        replay = await self.h.pipeline.execute_task_pipeline(self.h.payload(attempt=2, prior=result))
        self.assertEqual(replay["pipeline"], result["pipeline"])
        self.assertEqual(self.h.order, ["collect", "analyze", "digest"])
        self.assertEqual(self.h.checkpoints, checkpoints)
        self.assertEqual(len(self.h.entitlement_calls), 2 * len(STAGES))
        self.assertEqual(self.h.usage_rows, CAPABILITIES, "Replay must not record a second charge")
        self.assertEqual(len([q for kind, q in self.h.authorization_queries if kind == "task"]), 2 * len(STAGES))

    async def test_revocation_between_stages_stops_analysis_and_digest(self):
        async def revoke(*args, **kwargs):
            result = await self.h._collect(*args, **kwargs)
            self.h.permission_allowed = False
            return result

        self.h.handlers.handle_collect.side_effect = revoke
        with self.assertRaisesRegex(PermissionError, "pipeline_permission_revoked"):
            await self.h.pipeline.execute_task_pipeline(self.h.payload())
        self.assertEqual(self.h.order, ["collect"])
        self.h.handlers.handle_analyze.assert_not_awaited()
        self.h.aggregate.assert_not_awaited()

    async def test_foreign_workspace_membership_payer_and_entitlement_denials_stop_work(self):
        cases = [
            ("tenant_id", 4, "pipeline_task_unavailable"),
            ("member_active", False, "pipeline_membership_revoked"),
            ("permission_allowed", False, "pipeline_permission_revoked"),
            ("catalog_owner_id", 8, "pipeline_payer_changed"),
            ("payer_available", False, "pipeline_payer_unavailable"),
            ("entitlement_allowed", False, "synthetic_entitlement_denied"),
        ]
        for attribute, value, reason in cases:
            with self.subTest(attribute=attribute):
                h = ResearchHarness()
                setattr(h, attribute, value)
                with self.assertRaisesRegex(PermissionError, reason):
                    await h.pipeline.execute_task_pipeline(h.payload())
                self.assertEqual(h.order, [])
                self.assertEqual(h.usage_rows, [])
                h.summarize.assert_not_awaited()
                h.send.assert_not_awaited()

    async def test_scenario_revocation_after_collection_stops_following_stages(self):
        self.h.scenario.is_active = False
        with self.assertRaisesRegex(PermissionError, "pipeline_scenario_unavailable"):
            await self.h.pipeline.execute_task_pipeline(self.h.payload())
        self.assertEqual(self.h.order, ["collect"])

    async def test_interrupted_stage_is_review_required_not_replayed(self):
        checkpoint = self.h.pipeline._checkpoint_base(job_id=99, task_id=7, attempt=1)
        checkpoint["stages"]["collect"] = {"state": "started", "attempt": 1}
        with self.assertRaisesRegex(self.h.pipeline.PipelineReviewRequired, "pipeline_stage_outcome_requires_review"):
            await self.h.pipeline.execute_task_pipeline(self.h.payload(attempt=2, prior={"pipeline": checkpoint}))
        self.assertEqual(self.h.order, [])
        self.assertEqual(self.h.checkpoints[-1]["stages"]["collect"]["state"], "review_required")

    async def test_foreign_or_corrupt_checkpoint_never_replays(self):
        for mutation in ({"job_id": 100}, {"task_id": 8}, {"version": 2}, {"stages": {"send": {}}}):
            with self.subTest(mutation=mutation):
                checkpoint = self.h.pipeline._checkpoint_base(job_id=99, task_id=7, attempt=1)
                checkpoint.update(mutation)
                with self.assertRaises(self.h.pipeline.PipelineReviewRequired):
                    await self.h.pipeline.execute_task_pipeline(self.h.payload(prior={"pipeline": checkpoint}))
        self.assertEqual(self.h.order, [])

    async def test_partial_and_unsupported_coverage_is_visible_in_preview(self):
        self.h.collection["error"] = 1
        self.h.collection["per_source"][0]["collection"] = {"platform": "vk", "warnings": ["unsupported_category"]}
        self.h.analysis["per_source"][0].update(raw_total=3, raw_processed=2)
        result = await self.h.pipeline.execute_task_pipeline(self.h.payload())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["digest"]["coverage_status"], "partial")
        self.assertIn("Collection was partial", result["digest"]["preview_text"])
        self.assertIn("Analysis was partial", result["digest"]["preview_text"])
        self.assertIn("unsupported_category", result["digest"]["preview_text"])

    async def test_known_zero_counts_are_retained_not_replaced_by_successful_items(self):
        self.h.collection.update(items=0, new_items=0)
        self.h.collection["per_source"][0].update(items=0, selected=0, outcome="empty", content_hashes=[])
        self.h.analysis.update(analyzed=0)
        self.h.analysis["per_source"][0].update(analyzed=0, analytics_ids=[])
        result = await self.h.pipeline.execute_task_pipeline(self.h.payload())
        self.assertEqual((result["items"], result["collected"], result["analyzed"]), (0, 0, 0))
        self.assertEqual(result["digest"]["analysis_count"], 0)

    async def test_timeout_leaves_unknown_receipt_and_cannot_be_retried_blindly(self):
        self.h.handlers.handle_collect.side_effect = TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):
            await self.h.pipeline.execute_task_pipeline(self.h.payload())
        self.assertEqual(self.h.checkpoints[-1]["stages"]["collect"]["state"], "started")
        with self.assertRaises(self.h.pipeline.PipelineReviewRequired):
            await self.h.pipeline.execute_task_pipeline(
                self.h.payload(attempt=2, prior={"pipeline": self.h.checkpoints[-1]})
            )
        self.h.aggregate.assert_not_awaited()
        self.assertEqual(self.h.handlers.handle_collect.await_count, 1)

    async def test_digest_dry_run_never_aggregates_summarizes_or_sends(self):
        self.h.snapshot["dry_run"] = True
        result = await self.h.pipeline.execute_task_pipeline(self.h.payload())
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["digest"]["generated"])
        self.h.aggregate.assert_not_awaited()
        self.h.summarize.assert_not_awaited()
        self.h.send.assert_not_awaited()

    async def test_reader_redacts_diagnostics_and_obeys_preview_permission(self):
        raw = await self.h.pipeline.execute_task_pipeline(self.h.payload())
        raw["pipeline"]["stages"]["collect"]["result"].update(error="PRIVATE_PROVIDER_DETAIL")
        hidden = self.h.results._pipeline_outcome(raw, can_view_preview=False)
        visible = self.h.results._pipeline_outcome(raw, can_view_preview=True)
        self.assertNotIn("PRIVATE_PROVIDER_DETAIL", repr(hidden))
        self.assertNotIn("preview_text", hidden["stages"]["digest"])
        self.assertIn("preview_text", visible["stages"]["digest"])
        self.assertEqual(visible["stages"]["digest"]["delivered"], 0)


if __name__ == "__main__":
    unittest.main()
