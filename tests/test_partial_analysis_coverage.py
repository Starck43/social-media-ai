"""Actual analyzer storage + dedup source with manager/provider doubles.

python tests/test_partial_analysis_coverage.py
No application bootstrap, DB, provider/network, schema or live execution.
"""

import asyncio
import copy
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source
from test_analyzer_reported_errors import load_analyzer
from test_scenario_output_contract import load_contract

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-PROVIDER-FAILURE"


class PartialCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_analyzer()
        # Coverage suites exercise real storage; the schema boundary must return
        # validated dictionaries, not the legacy validator Mock placeholder.
        _, builder = load_contract()
        self.module.validate_with_pydantic = builder.validate_with_pydantic
        self.dedup = load_isolated_source("_partial_dedup", ROOT / "app/services/ai/dedup.py")
        self.module.item_hash = self.dedup.item_hash
        self.module.batch_hash = self.dedup.batch_hash
        self.module.hashes_hash = self.dedup.hashes_hash
        self.module.get_enum_value = Mock(return_value=None)
        self.module._normalize = Mock(return_value="topic")
        self.module.PeriodType.DAY = "DAY"
        self.module.logger = Mock()
        self.module.build_pydantic_model.return_value = None
        self.analyzer = self.module.AIAnalyzer()
        self.analyzer._calculate_content_stats = Mock(return_value={"total_posts": 1})
        self.analyzer._get_platform_name = AsyncMock(return_value="platform")
        self.analyzer._generate_topic_chain_id = AsyncMock(return_value="chain")
        self.analyzer._resolve_chain_label = Mock(return_value="topic")
        self.analyzer._price_usage = AsyncMock(return_value=Decimal("1.25"))
        self.source = SimpleNamespace(id=1, tenant_id=1, name="source", source_type=None)
        self.scenario = SimpleNamespace(
            id=2, name="scenario", output_schema={"type": "object"}, max_tokens=None,
            scope={}, analysis_types=[], content_types=[],
        )
        self.a = {"external_id": "A", "text": "Previous useful content"}
        self.b = {"external_id": "B", "text": "New useful content"}
        self.good = {
            "request": {"model": "m", "provider": "p", "prompt": "safe-prompt"},
            "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 4}},
            "parsed": {"analysis": "A useful conclusion"},
        }
        self.failed = {
            "request": {"model": "vision", "provider": "p", "prompt": "safe-prompt"},
            "response": {"error": PRIVATE, "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
            "parsed": {"analysis": "Error: " + PRIVATE},
        }
        self.analyzer._analyze_text = AsyncMock(return_value=self.good)
        self.analyzer._analyze_images = AsyncMock(return_value=self.failed)
        self.analyzer._analyze_videos = AsyncMock(return_value=None)
        self.analyzer._create_unified_summary = AsyncMock(return_value=None)
        self.module.ContentClassifier.classify_content = Mock(side_effect=self.classify)
        self.existing = None
        self.rows = []
        self.first = AsyncMock(side_effect=lambda: self.existing)
        self.create = AsyncMock(side_effect=self.create_row)
        self.update = AsyncMock(side_effect=self.update_row)
        self.module.AIAnalytics.objects = SimpleNamespace(
            filter=Mock(return_value=SimpleNamespace(first=self.first)), create=self.create, update_by_id=self.update,
        )
        self.module.filter_analyzed = AsyncMock(side_effect=self.filter_items)

    def classify(self, content):
        return {"text": content, "image": content, "video": []}

    async def filter_items(self, content, source_id):
        return self.dedup._split(content, self.rows, source_id)

    async def create_row(self, **kwargs):
        row = SimpleNamespace(id=10, **kwargs)
        self.existing = row
        self.rows.append(row)
        return row

    async def update_row(self, row_id, **kwargs):
        self.assertEqual(row_id, self.existing.id)
        for key, value in kwargs.items():
            setattr(self.existing, key, value)
        return self.existing

    def old_row(self, legacy=False):
        row = SimpleNamespace(
            id=9, content_hash=self.dedup.batch_hash([self.a]), source_id=1,
            summary_data={"multi_llm_analysis": {"text_analysis": {"analysis": "A preserved conclusion"}}},
            topic_chain_id="old-chain", chain_label="old-topic", normalized_label="old-topic",
            request_tokens=90, response_tokens=30, estimated_cost=Decimal("2"),
        )
        if not legacy:
            row.summary_data["content_hashes"] = [self.dedup.item_hash(self.a)]
        self.existing = row
        self.rows = [row]
        return row

    async def analyze(self, force=False):
        return await self.analyzer.base_analyze_content(
            [self.b], self.source, agent_scenario=self.scenario, force_reanalyze=force,
        )

    def assert_b_uncovered(self, rows):
        remaining, known = self.dedup._split([self.b], rows, 1)
        self.assertEqual(remaining, [self.b])
        self.assertIsNone(known)
        self.assertNotIn(self.dedup.item_hash(self.b), self.dedup.analysed_hashes(rows))

    async def test_new_partial_keeps_useful_output_not_failed_conclusion(self):
        row = await self.analyze()
        self.assertIsNotNone(row)
        data = row.summary_data
        self.assertEqual(data["multi_llm_analysis"]["text_analysis"], self.good["parsed"])
        self.assertEqual(data["multi_llm_analysis"]["image_analysis"], {})
        self.assertIs(data["analysis_metadata"]["analysis_complete"], False)
        self.assertNotIn(PRIVATE, repr(data))
        self.assert_b_uncovered([row])
        self.assertIsNone(row.content_hash)
        self.assertEqual(data["content_hashes"], [])

    async def test_failure_envelope_is_not_mutated_and_usage_is_retained(self):
        original = copy.deepcopy(self.failed)
        row = await self.analyze()
        self.assertEqual(self.failed, original)
        self.assertEqual((row.request_tokens, row.response_tokens, row.estimated_cost), (13, 5, Decimal("1.25")))
        usage = self.analyzer._price_usage.call_args.args[0]
        self.assertEqual(sum(value["request_tokens"] for value in usage), 13)
        self.assertEqual(sum(value["response_tokens"] for value in usage), 5)
        self.assertEqual(row.response_payload["image_analysis"]["parsed_keys"], [])

    async def test_existing_A_is_not_replaced_by_partial_B(self):
        old = self.old_row()
        original = copy.deepcopy(vars(old))
        self.assertIsNone(await self.analyze())
        self.assertEqual(vars(old), original)
        self.create.assert_not_awaited()
        self.update.assert_not_awaited()
        self.assert_b_uncovered([old])
        self.assertEqual(self.dedup.analysed_hashes([old]), {self.dedup.item_hash(self.a)})
        remaining, known = self.dedup._split([self.a], [old], 1)
        self.assertEqual(remaining, [])
        self.assertIs(known, old)

    async def test_legacy_A_batch_does_not_falsely_cover_B(self):
        old = self.old_row(legacy=True)
        original = copy.deepcopy(vars(old))
        self.assertIsNone(await self.analyze())
        self.assertEqual(vars(old), original)
        self.update.assert_not_awaited()
        self.assert_b_uncovered([old])
        self.assertEqual(self.dedup._split([self.a], [old], 1), ([], old))

    async def test_forced_partial_does_not_overwrite_existing_A(self):
        old = self.old_row()
        original = copy.deepcopy(vars(old))
        self.assertIsNone(await self.analyze(force=True))
        self.assertEqual(vars(old), original)
        self.update.assert_not_awaited()
        self.assert_b_uncovered([old])

    async def test_partial_then_later_success_can_cover_B(self):
        partial = await self.analyze()
        self.assert_b_uncovered([partial])
        self.analyzer._analyze_images.return_value = self.good
        complete = await self.analyze()
        self.assertIs(complete, partial)
        self.assertEqual(complete.summary_data["content_hashes"], [self.dedup.item_hash(self.b)])
        self.assertEqual(complete.content_hash, self.dedup.batch_hash([self.b]))
        self.assertNotIn("analysis_complete", complete.summary_data["analysis_metadata"])
        self.assertEqual(self.dedup._split([self.b], [complete], 1), ([], complete))
        self.assertEqual(self.dedup.analysed_hashes([complete]), {self.dedup.item_hash(self.b)})

    async def test_later_full_success_updates_existing_A_under_original_union(self):
        old = self.old_row()
        self.assertIsNone(await self.analyze())
        self.analyzer._analyze_images.return_value = self.good
        row = await self.analyze()
        self.assertIs(row, old)
        self.assertEqual(set(row.summary_data["content_hashes"]), {
            self.dedup.item_hash(self.a), self.dedup.item_hash(self.b),
        })
        self.assertNotIn("analysis_complete", row.summary_data["analysis_metadata"])
        self.assertIn(self.dedup.item_hash(self.b), self.dedup.analysed_hashes([row]))

    async def test_prior_instance_errors_do_not_make_clean_B_partial(self):
        self.analyzer.reported_errors = 7
        self.analyzer._analyze_images.return_value = self.good
        row = await self.analyze()
        self.assertNotIn("analysis_complete", row.summary_data["analysis_metadata"])
        self.assertEqual(self.dedup._split([self.b], [row], 1), ([], row))

    async def test_failed_unified_summary_does_not_become_stored_conclusion(self):
        self.analyzer._analyze_images.return_value = self.good
        self.analyzer._create_unified_summary.return_value = self.failed
        row = await self.analyze()
        self.assertEqual(row.summary_data["unified_summary"], {})
        self.assertIs(row.summary_data["analysis_metadata"]["analysis_complete"], False)
        self.assert_b_uncovered([row])
        self.assertNotIn(PRIVATE, repr(row.summary_data))

    async def test_invalid_structured_output_also_leaves_B_uncovered(self):
        self.analyzer._analyze_images.return_value = {
            "response": {"error": "invalid_structured_output"}, "parsed": {},
        }
        row = await self.analyze()
        self.assert_b_uncovered([row])
        self.assertEqual(row.summary_data["multi_llm_analysis"]["image_analysis"], {})

    async def test_all_failed_results_do_not_create_or_update_row(self):
        self.analyzer._analyze_text.return_value = self.failed
        self.assertIsNone(await self.analyze())
        self.create.assert_not_awaited()
        self.update.assert_not_awaited()
        self.assertEqual(self.analyzer.reported_errors, 2)

    async def test_caught_media_failure_with_useful_sibling_is_partial(self):
        async def caught(*args, **kwargs):
            self.analyzer.reported_errors += 1
            return None
        self.analyzer._analyze_images.side_effect = caught
        row = await self.analyze()
        self.assertIs(row.summary_data["analysis_metadata"]["analysis_complete"], False)
        self.assert_b_uncovered([row])

    async def test_missing_model_none_stays_unknown_not_claimed_complete(self):
        self.analyzer._analyze_images.return_value = None
        row = await self.analyze()
        self.assertIs(row.summary_data["analysis_metadata"]["analysis_complete"], False)
        self.assert_b_uncovered([row])
        self.assertIsNone(row.content_hash)
        self.assertEqual(row.summary_data["multi_llm_analysis"]["text_analysis"], self.good["parsed"])
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_direct_storage_guard_rejects_partial_completion_hashes(self):
        row = await self.analyzer._save_analysis(
            {"text_analysis": self.good}, None, self.source, {}, "p",
            content_hash=self.dedup.batch_hash([self.b]), content_hashes=[self.dedup.item_hash(self.b)],
            reported_partial=True,
        )
        self.assert_b_uncovered([row])
        self.assertEqual(row.summary_data["content_hashes"], [])

    async def test_legacy_storage_call_defaults_remain_compatible(self):
        row = await self.analyzer._save_analysis(
            {"text_analysis": self.good}, None, self.source, {}, "p",
            content_hash=self.dedup.batch_hash([self.b]), content_hashes=[self.dedup.item_hash(self.b)],
        )
        self.assertEqual(self.dedup._split([self.b], [row], 1), ([], row))
        self.assertNotIn("analysis_complete", row.summary_data["analysis_metadata"])

    async def test_existing_unknown_row_also_not_silently_replaced(self):
        old = self.old_row(legacy=True)
        old.content_hash = None
        original = copy.deepcopy(vars(old))
        self.assertIsNone(await self.analyze())
        self.assertEqual(vars(old), original)
        self.update.assert_not_awaited()
        self.assert_b_uncovered([old])

    async def test_cancelled_media_still_propagates_without_storage(self):
        self.analyzer._analyze_images.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.analyze()
        self.create.assert_not_awaited()
        self.update.assert_not_awaited()

    async def test_successful_known_A_dedup_still_bypasses_model_calls(self):
        old = self.old_row()
        result = await self.analyzer.base_analyze_content([self.a], self.source, agent_scenario=self.scenario)
        self.assertIs(result, old)
        self.analyzer._analyze_text.assert_not_awaited()
        self.analyzer._analyze_images.assert_not_awaited()
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_existing_guard_precedes_pricing_tracing_and_validation(self):
        old = self.old_row()
        original = copy.deepcopy(vars(old))
        self.analyzer._build_trace_payload = Mock(side_effect=AssertionError("trace must not run"))
        self.analyzer._build_request_snapshot = Mock(side_effect=AssertionError("snapshot must not run"))
        self.assertIsNone(await self.analyze())
        self.assertEqual(vars(old), original)
        self.analyzer._price_usage.assert_not_awaited()
        self.analyzer._build_trace_payload.assert_not_called()
        self.analyzer._build_request_snapshot.assert_not_called()
        self.module.build_pydantic_model.assert_not_called()
        self.create.assert_not_awaited()
        self.update.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
