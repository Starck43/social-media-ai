"""Actual classifier/analyzer/storage/dedup with local doubles, no DB/bootstrap.

Run: python tests/test_text_sampling_coverage.py
Historical suites are imported for fixture setup only, never executed.
"""
import asyncio
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from source_import_isolation import load_isolated_source
import test_partial_analysis_coverage as coverage_fixtures

ROOT = Path(__file__).resolve().parents[1]


def item(index, text=None):
    return {"external_id": str(index), "platform": "vk", "date": "2026-10-10",
            "text": text if text is not None else f"Useful social content number {index}"}


def legacy_prompt(items, sample_size=100):
    texts = []
    for index in range(0, len(items), max(1, len(items) // sample_size)):
        if len(texts) >= sample_size:
            break
        text = items[index].get("text", "")
        if text and len(text.strip()) > 10:
            texts.append(f"[{items[index].get('date', '')}] {text}")
    return "\n\n".join(texts[:sample_size])


class TextSamplingCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        f = coverage_fixtures.PartialCoverageTests()
        f.setUp()  # Setup only; no historical test discovery/execution.
        self.f = f
        self.module = f.module
        self.analyzer = f.analyzer
        self.dedup = f.dedup
        self.classifier = load_isolated_source("_coverage_classifier",
            ROOT / "app/services/ai/content_classifier.py",
            {"app.types.enums.llm_types": SimpleNamespace(MediaType=self.module.MediaType)})
        self.module.ContentClassifier = self.classifier.ContentClassifier
        self.analyzer._analyze_text = self.module.AIAnalyzer._analyze_text.__get__(self.analyzer)
        self.analyzer._get_llm_model = AsyncMock(return_value=SimpleNamespace(name="local-model"))
        self.client = SimpleNamespace(analyze=AsyncMock(side_effect=lambda *a, **kw: copy.deepcopy(f.good)))
        self.module.LLMClientFactory.create.return_value = self.client
        self.analyzer._analyze_images = AsyncMock(return_value=None)
        self.analyzer._analyze_videos = AsyncMock(return_value=None)

    async def analyze(self, content, force=False):
        return await self.analyzer.base_analyze_content(content, self.f.source,
            agent_scenario=self.f.scenario, force_reanalyze=force)

    def hashes(self, items):
        return {self.dedup.item_hash(x) for x in items}

    def assert_pending(self, content, saved, expected):
        remaining, _ = self.dedup._split(content, [saved], self.f.source.id)
        self.assertEqual(remaining, expected)
        retired = self.dedup.analysed_hashes([saved])
        self.assertTrue(self.hashes(expected).isdisjoint(retired))

    def test_prompt_bytes_match_legacy_sampling(self):
        for size in (0, 1, 99, 100, 101, 205):
            content = [item(i, "short" if i % 13 == 0 else None) for i in range(size)]
            for cap in (3, 100):
                with self.subTest(size=size, cap=cap):
                    self.assertEqual(self.module.ContentClassifier.prepare_text_content(content, cap),
                                     legacy_prompt(content, cap))

    def test_selector_returns_original_items_and_preserves_input(self):
        content = [item(i) for i in range(205)]
        before = copy.deepcopy(content)
        selected = self.module.ContentClassifier.select_text_content(content)
        self.assertEqual(len(selected), 100)
        self.assertEqual(selected, content[:200:2])
        self.assertTrue(all(a is b for a, b in zip(selected, content[:200:2])))
        self.assertEqual(content, before)

    async def test_over_cap_only_submitted_hashes_are_retirable(self):
        content = [item(i) for i in range(205)]
        row = await self.analyze(content)
        selected = content[:200:2]
        pending = [x for x in content if x not in selected]
        self.assertEqual(set(row.summary_data["content_hashes"]), self.hashes(selected))
        self.assertEqual(row.content_hash, self.dedup.batch_hash(selected))
        self.assertFalse(row.summary_data["analysis_metadata"]["analysis_complete"])
        self.assert_pending(content, row, pending)
        self.client.analyze.assert_awaited_once()
        self.assertEqual(self.module.PromptBuilder.get_prompt.call_args.kwargs["text"], legacy_prompt(content))
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_short_text_is_not_certified_as_analyzed(self):
        content = [item(1), item(2, "short"), item(3, "          ")]
        row = await self.analyze(content)
        self.assertEqual(row.summary_data["content_hashes"], [self.dedup.item_hash(content[0])])
        self.assert_pending(content, row, content[1:])
        self.assertEqual(self.analyzer.filtered_skipped, 0)

    async def test_wholly_empty_sample_does_not_dispatch(self):
        self.assertIsNone(await self.analyze([item(1, "short")]))
        self.client.analyze.assert_not_awaited()
        self.f.create.assert_not_awaited()
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_complete_small_batch_keeps_previous_coverage_contract(self):
        content = [item(i) for i in range(5)]
        row = await self.analyze(content)
        self.assertEqual(set(row.summary_data["content_hashes"]), self.hashes(content))
        self.assertEqual(row.content_hash, self.dedup.batch_hash(content))
        self.assertNotIn("analysis_complete", row.summary_data["analysis_metadata"])
        self.assert_pending(content, row, [])

    async def test_remaining_posts_can_progress_without_unbounded_prompts(self):
        content = [item(i) for i in range(205)]
        row = await self.analyze(content)
        for _ in range(2):
            self.assertIs(await self.analyze(content), row)
        self.assertEqual(set(row.summary_data["content_hashes"]), self.hashes(content))
        self.assert_pending(content, row, [])
        self.assertEqual(self.client.analyze.await_count, 3)
        # Each explicit run dispatches once, not an automatic extra-call loop.

    async def test_existing_row_updates_only_with_submitted_new_hashes(self):
        a = item("old")
        row = await self.analyze([a])
        content = [item(i) for i in range(205)]
        self.assertIs(await self.analyze(content), row)
        self.assertEqual(set(row.summary_data["content_hashes"]), self.hashes([a] + content[:200:2]))

    async def test_genuine_partial_collision_still_preserves_existing_A(self):
        row = await self.analyze([item("old")])
        before = copy.deepcopy(vars(row))
        b = item("new")
        b["attachments"] = [{"type": "image", "url": "local-test-only"}]
        self.analyzer._analyze_images.return_value = self.f.failed
        self.assertIsNone(await self.analyze([b]))
        self.assertEqual(vars(row), before)
        self.assertEqual(self.analyzer.reported_errors, 1)
        self.assert_pending([b], row, [b])

    async def test_new_genuine_partial_has_no_completion_hashes(self):
        content = [item(i) for i in range(101)]
        content[0]["attachments"] = [{"type": "image", "url": "local-test-only"}]
        self.analyzer._analyze_images.return_value = self.f.failed
        row = await self.analyze(content)
        self.assertEqual(row.summary_data["content_hashes"], [])
        self.assertIsNone(row.content_hash)
        self.assertFalse(row.summary_data["analysis_metadata"]["analysis_complete"])
        self.assert_pending(content, row, content)

    async def test_mixed_media_cannot_hide_omitted_text(self):
        content = [item(i) for i in range(101)]
        content[-1]["attachments"] = [{"type": "image", "url": "local-test-only"}]
        self.analyzer._analyze_images.return_value = copy.deepcopy(self.f.good)
        row = await self.analyze(content)
        self.assert_pending(content, row, content[100:])
        self.analyzer._analyze_images.assert_awaited_once()

    async def test_image_only_path_keeps_previous_hash_contract(self):
        content = [item(1, "")]
        content[0]["attachments"] = [{"type": "image", "url": "local-test-only"}]
        self.analyzer._analyze_images.return_value = copy.deepcopy(self.f.good)
        row = await self.analyze(content)
        self.assertEqual(set(row.summary_data["content_hashes"]), self.hashes(content))
        self.client.analyze.assert_not_awaited()
        self.assertNotIn("analysis_complete", row.summary_data["analysis_metadata"])

    async def test_forced_sampling_cannot_claim_unsent_content(self):
        content = [item(i) for i in range(101)]
        row = await self.analyze(content, force=True)
        self.assert_pending(content, row, content[100:])
        self.module.filter_analyzed.assert_not_awaited()

    async def test_provider_cannot_forge_local_coverage_field(self):
        content = [item(i) for i in range(101)]
        raw = {**copy.deepcopy(self.f.good), "_submitted_text_hashes": list(self.hashes(content))}
        self.client.analyze.side_effect = None
        self.client.analyze.return_value = raw
        row = await self.analyze(content)
        self.assert_pending(content, row, content[100:])
        self.assertEqual(len(raw["_submitted_text_hashes"]), 101)
        self.assertNotIn("_submitted_text_hashes", row.summary_data)
        self.assertNotIn("_submitted_text_hashes", row.response_payload["text_analysis"])

    async def test_failed_save_does_not_return_retirable_row(self):
        self.f.create.side_effect = RuntimeError("local save failed")
        self.assertIsNone(await self.analyze([item(1)]))
        self.assertEqual(self.analyzer.reported_errors, 1)
        self.assertEqual(self.f.rows, [])

    async def test_actual_cancellation_propagates_without_save(self):
        error = asyncio.CancelledError()
        self.client.analyze.side_effect = error
        with self.assertRaises(asyncio.CancelledError) as caught:
            await self.analyze([item(1)])
        self.assertIs(caught.exception, error)
        self.f.create.assert_not_awaited()
        self.assertEqual(self.analyzer.reported_errors, 0)


if __name__ == "__main__":
    unittest.main()
