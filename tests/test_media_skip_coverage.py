"""Actual media/analyzer/storage/dedup with local doubles; no DB/app/providers.

Run: python tests/test_media_skip_coverage.py
Historical modules supply fixture setup only; no historical test discovery.
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


def post(identity, *, text=True, media=None, urls=None):
    item = {"external_id": identity, "platform": "vk",
            "text": f"Useful social content for {identity}" if text else ""}
    if media:
        item["attachments"] = [{"type": media, "url": url} for url in (urls if urls is not None else ["local-media"])]
    return item


class MediaSkipCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        f = coverage_fixtures.PartialCoverageTests()
        f.setUp()
        self.f = f
        self.module = f.module
        self.analyzer = f.analyzer
        self.dedup = f.dedup
        utility = load_isolated_source("_media_attachment_vocabulary", ROOT / "app/utils/content_attachments.py")
        self.classifier = load_isolated_source("_media_classifier",
            ROOT / "app/services/ai/content_classifier.py",
            {"app.types.enums.llm_types": SimpleNamespace(MediaType=self.module.MediaType),
             "app.utils.content_attachments": utility})
        self.module.ContentClassifier = self.classifier.ContentClassifier
        for method in ("_analyze_text", "_analyze_images", "_analyze_videos"):
            setattr(self.analyzer, method, getattr(self.module.AIAnalyzer, method).__get__(self.analyzer))
        self.models = {media: SimpleNamespace(name=f"local-{media}") for media in ("text", "image", "video")}
        self.analyzer._get_llm_model = AsyncMock(side_effect=lambda scenario, media: self.models[media.db_value])
        self.client = SimpleNamespace(analyze=AsyncMock(side_effect=lambda *args, **kw: copy.deepcopy(f.good)))
        self.module.LLMClientFactory.create.return_value = self.client

    async def analyze(self, content, force=False):
        return await self.analyzer.base_analyze_content(content, self.f.source,
            agent_scenario=self.f.scenario, force_reanalyze=force)

    def hashes(self, content):
        return {self.dedup.item_hash(item) for item in content}

    def assert_coverage(self, content, row, covered):
        self.assertIsNotNone(row)
        self.assertEqual(set(row.summary_data["content_hashes"]), self.hashes(covered))
        pending = [item for item in content if item not in covered]
        remaining, _ = self.dedup._split(content, [row], self.f.source.id)
        self.assertEqual(remaining, pending)
        self.assertTrue(self.hashes(pending).isdisjoint(self.dedup.analysed_hashes([row])))
        if pending:
            self.assertFalse(row.summary_data["analysis_metadata"]["analysis_complete"])
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_missing_image_model_keeps_post_pending(self):
        content = [post("a", media="image")]
        self.models["image"] = None
        row = await self.analyze(content)
        self.assert_coverage(content, row, [])
        self.client.analyze.assert_awaited_once()
        self.assertIsNone(row.content_hash)
        self.assertEqual(row.summary_data["multi_llm_analysis"]["text_analysis"], self.f.good["parsed"])

    async def test_missing_video_model_keeps_post_pending(self):
        content = [post("a", media="video")]
        self.models["video"] = None
        self.assert_coverage(content, await self.analyze(content), [])
        self.client.analyze.assert_awaited_once()

    async def test_no_media_urls_do_not_dispatch_media(self):
        content = [post("a", media="image", urls=[None]), post("b", media="video", urls=[""])]
        self.assert_coverage(content, await self.analyze(content), [])
        self.client.analyze.assert_awaited_once()

    async def test_missing_one_image_url_keeps_same_parent_pending(self):
        content = [post("a", media="image", urls=["local-good", None])]
        self.assert_coverage(content, await self.analyze(content), [])
        self.assertEqual(self.client.analyze.await_args_list[-1].kwargs["media_urls"], ["local-good"])
        self.assertEqual(self.client.analyze.await_count, 2)

    async def test_missing_one_video_url_keeps_same_parent_pending(self):
        content = [post("a", media="video", urls=[None, "local-good"])]
        self.assert_coverage(content, await self.analyze(content), [])
        self.assertEqual(self.client.analyze.await_args_list[-1].kwargs["media_urls"], ["local-good"])

    async def test_mixed_batch_only_complete_posts_retire(self):
        plain = post("plain")
        image = post("image", media="image", urls=[None])
        video = post("video", media="video")
        content = [plain, image, video]
        row = await self.analyze(content)
        self.assert_coverage(content, row, [plain, video])
        self.assertEqual(row.content_hash, self.dedup.batch_hash([plain, video]))
        self.assertEqual(self.client.analyze.await_count, 2)

    async def test_text_sampling_and_media_skip_intersect(self):
        content = [post(str(i)) for i in range(101)]
        content[0]["attachments"] = [{"type": "image", "url": None}]
        row = await self.analyze(content)
        self.assert_coverage(content, row, content[1:100])
        self.client.analyze.assert_awaited_once()

    async def test_complete_media_only_batch_preserves_hashes(self):
        content = [post("a", text=False, media="image"), post("b", text=False, media="video")]
        self.assert_coverage(content, await self.analyze(content), content)
        self.assertEqual(self.client.analyze.await_count, 2)

    async def test_unanalyzable_media_only_batch_remains_unsaved(self):
        content = [post("a", text=False, media="image", urls=[None])]
        self.assertIsNone(await self.analyze(content))
        self.client.analyze.assert_not_awaited()
        self.f.create.assert_not_awaited()
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_complete_text_image_video_post_keeps_previous_coverage(self):
        content = [post("a", media="image")]
        content[0]["attachments"].append({"type": "video", "url": "local-video"})
        row = await self.analyze(content)
        self.assert_coverage(content, row, content)
        self.assertNotIn("analysis_complete", row.summary_data["analysis_metadata"])
        self.assertEqual(self.client.analyze.await_count, 3)

    async def test_empty_provider_parsed_media_cannot_certify_coverage(self):
        async def result(*args, **kwargs):
            return {"parsed": {}, "response": {}} if kwargs.get("media_urls") else copy.deepcopy(self.f.good)
        self.client.analyze.side_effect = result
        content = [post("a", media="image"), post("b", media="video")]
        self.assert_coverage(content, await self.analyze(content), [])

    async def test_legacy_none_media_result_is_unknown_not_reported_error(self):
        self.analyzer._analyze_images = AsyncMock(return_value=None)
        content = [post("a", media="image")]
        self.assert_coverage(content, await self.analyze(content), [])

    async def test_genuine_failure_still_clears_all_new_hashes(self):
        async def result(*args, **kwargs):
            return copy.deepcopy(self.f.failed if kwargs.get("media_urls") else self.f.good)
        self.client.analyze.side_effect = result
        content = [post("plain"), post("failed", media="image")]
        row = await self.analyze(content)
        self.assertEqual(row.summary_data["content_hashes"], [])
        self.assertIsNone(row.content_hash)
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_genuine_partial_collision_preserves_A_unchanged(self):
        row = await self.analyze([post("old")])
        before = copy.deepcopy(vars(row))
        async def result(*args, **kwargs):
            return copy.deepcopy(self.f.failed if kwargs.get("media_urls") else self.f.good)
        self.client.analyze.side_effect = result
        self.assertIsNone(await self.analyze([post("new", media="video")]))
        self.assertEqual(vars(row), before)
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_later_explicit_run_can_cover_previously_missing_media(self):
        content = [post("a", media="image")]
        self.models["image"] = None
        row = await self.analyze(content)
        self.assert_coverage(content, row, [])
        self.models["image"] = SimpleNamespace(name="local-image")
        self.assertIs(await self.analyze(content), row)
        self.assert_coverage(content, row, content)
        self.assertEqual(self.client.analyze.await_count, 3)

    async def test_forced_run_cannot_cover_skipped_media(self):
        content = [post("a", media="video")]
        self.models["video"] = None
        self.assert_coverage(content, await self.analyze(content, force=True), [])
        self.module.filter_analyzed.assert_not_awaited()

    async def test_provider_cannot_forge_private_url_selection_evidence(self):
        raw = {**copy.deepcopy(self.f.good), "_unsubmitted_media_hashes": []}
        self.client.analyze.side_effect = None
        self.client.analyze.return_value = raw
        content = [post("a", media="image", urls=[None, "local-good"])]
        row = await self.analyze(content)
        self.assert_coverage(content, row, [])
        self.assertEqual(raw["_unsubmitted_media_hashes"], [])
        self.assertNotIn("_unsubmitted_media_hashes", row.summary_data)
        self.assertNotIn("_unsubmitted_media_hashes", row.response_payload["image_analysis"])

    async def test_malformed_local_evidence_fails_coverage_closed(self):
        content = [post("a", media="image")]
        for value in (True, "private", ["foreign-hash"], [None]):
            self.f.existing = None
            self.f.rows.clear()
            self.analyzer._analyze_images = AsyncMock(return_value={**copy.deepcopy(self.f.good),
                "_unsubmitted_media_hashes": value})
            with self.subTest(value=value):
                self.assert_coverage(content, await self.analyze(content, force=True), [])

    async def test_silent_media_collision_preserves_A_before_pricing_and_writes(self):
        old = post("old")
        row = await self.analyze([old])
        before = copy.deepcopy(vars(row))
        self.analyzer._price_usage.reset_mock()
        self.f.update.reset_mock()
        new = post("new", media="image", urls=[None])
        self.assertIsNone(await self.analyze([new]))
        self.assertEqual(vars(row), before)
        self.analyzer._price_usage.assert_not_awaited()
        self.f.update.assert_not_awaited()
        self.assertEqual(self.dedup._split([new], [row], self.f.source.id)[0], [new])
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_sampling_alone_still_updates_existing_covered_subset(self):
        old = post("old")
        row = await self.analyze([old])
        content = [post(str(i)) for i in range(101)]
        self.assertIs(await self.analyze(content), row)
        self.assertEqual(set(row.summary_data["content_hashes"]), self.hashes([old] + content[:100]))
        self.assertEqual(self.dedup._split(content, [row], self.f.source.id)[0], content[100:])

    async def test_actual_media_cancellation_propagates_without_save(self):
        async def result(*args, **kwargs):
            if kwargs.get("media_urls"):
                raise asyncio.CancelledError()
            return copy.deepcopy(self.f.good)
        self.client.analyze.side_effect = result
        with self.assertRaises(asyncio.CancelledError):
            await self.analyze([post("a", media="video")])
        self.f.create.assert_not_awaited()
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_failed_save_returns_no_retirable_row(self):
        self.f.create.side_effect = RuntimeError("local save failed")
        self.assertIsNone(await self.analyze([post("a", media="image")]))
        self.assertEqual(self.f.rows, [])
        self.assertEqual(self.analyzer.reported_errors, 1)


if __name__ == "__main__":
    unittest.main()
