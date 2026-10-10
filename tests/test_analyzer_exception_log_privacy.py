"""Five actual analyzer exception sinks; stdlib/doubles, no DB/network/bootstrap.

Run: python tests/test_analyzer_exception_log_privacy.py
Other analyzer logs and external tracing are deliberately outside this contract.
"""

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from test_analyzer_reported_errors import load_analyzer

PRIVATE = "PRIVATE-token-session-prompt-sql-provider-url"
STAGES = ("base", "text", "image", "video", "summary")


class RecordSink(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class HostileError(ValueError):
    def __str__(self):
        raise AssertionError("Exception must not be stringified")

    def __repr__(self):
        raise AssertionError("Exception must not be repr'd")


class AnalyzerExceptionLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_analyzer()
        self.analyzer = self.module.AIAnalyzer()
        self.source = SimpleNamespace(id=7, tenant_id=2, source_type=None)
        self.scenario = SimpleNamespace(output_schema={"type": "object"}, max_tokens=None)
        self.good = {"parsed": {"analysis": "useful"}, "response": {}}
        self.saved = SimpleNamespace(id=9)
        self.sink = RecordSink()
        self.module.logger = logging.Logger("isolated_analyzer_privacy", logging.DEBUG)
        self.module.logger.addHandler(self.sink)
        self.model = SimpleNamespace(name="model")
        self.analyzer._get_llm_model = AsyncMock(return_value=self.model)
        self.module.ContentClassifier.prepare_text_content = Mock(return_value=PRIVATE)
        self.module.ContentClassifier.get_media_urls = Mock(return_value=[PRIVATE])
        self.module.PromptBuilder.get_unified_summary_prompt = Mock(return_value=PRIVATE)
        self.client = SimpleNamespace(analyze=AsyncMock(return_value=self.good))
        self.module.LLMClientFactory.create.return_value = self.client
        self.module.ContentClassifier.classify_content = Mock(
            return_value={"text": [1], "image": [], "video": []}
        )
        self.analyzer._calculate_content_stats = Mock(return_value={})
        self.analyzer._get_platform_name = AsyncMock(return_value="platform")
        self.analyzer._generate_topic_chain_id = AsyncMock(return_value="chain")
        self.analyzer._resolve_chain_label = Mock(return_value="label")
        self.analyzer._save_analysis = AsyncMock(return_value=self.saved)

    async def call_stage(self, stage, error=None):
        if stage == "base":
            # The base method is actual source; only its collaborators are doubled.
            self.analyzer._analyze_text = AsyncMock(return_value=self.good)
            self.analyzer._create_unified_summary = AsyncMock(return_value=None)
            self.analyzer._save_analysis.side_effect = error
            return await self.analyzer.base_analyze_content(
                [{"text": PRIVATE}], self.source, force_reanalyze=True, agent_scenario=self.scenario
            )
        self.client.analyze.side_effect = error
        if stage == "text":
            return await self.module.AIAnalyzer._analyze_text(self.analyzer, [{}], None, {}, "platform", self.source)
        if stage == "image":
            return await self.module.AIAnalyzer._analyze_images(self.analyzer, [{}], None, "platform")
        if stage == "video":
            return await self.module.AIAnalyzer._analyze_videos(self.analyzer, [{}], None, "platform")
        return await self.module.AIAnalyzer._create_unified_summary(
            self.analyzer, {"text_analysis": self.good, "image_analysis": self.good}, None
        )

    def assert_safe_record(self, stage, kind):
        errors = [record for record in self.sink.records if record.levelno >= logging.ERROR]
        self.assertEqual(len(errors), 1)
        record = errors[0]
        self.assertEqual(record.getMessage(), f"analysis_failed stage={stage} error_kind={kind}")
        self.assertEqual(record.args, (kind,))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)
        rendered = logging.Formatter("%(levelname)s %(message)s").format(record)
        self.assertNotIn(PRIVATE, rendered)
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn(PRIVATE, str(vars(record)))

    async def failure(self, stage):
        self.assertIsNone(await self.call_stage(stage, RuntimeError(PRIVATE)))
        self.assertEqual(self.analyzer.reported_errors, 1)
        self.assert_safe_record(stage, "runtime_error")

    async def cancellation(self, stage):
        with self.assertRaises(asyncio.CancelledError):
            await self.call_stage(stage, asyncio.CancelledError(PRIVATE))
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.assertFalse(any(r.levelno >= logging.ERROR for r in self.sink.records))

    async def success(self, stage):
        result = await self.call_stage(stage)
        self.assertIs(result, self.saved if stage == "base" else self.good)
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.assertFalse(any(r.levelno >= logging.ERROR for r in self.sink.records))

    async def test_base_failure(self):
        await self.failure("base")

    async def test_text_failure(self):
        await self.failure("text")

    async def test_image_failure(self):
        await self.failure("image")

    async def test_video_failure(self):
        await self.failure("video")

    async def test_summary_failure(self):
        await self.failure("summary")

    async def test_base_cancellation(self):
        await self.cancellation("base")

    async def test_text_cancellation(self):
        await self.cancellation("text")

    async def test_image_cancellation(self):
        await self.cancellation("image")

    async def test_video_cancellation(self):
        await self.cancellation("video")

    async def test_summary_cancellation(self):
        await self.cancellation("summary")

    async def test_base_success(self):
        await self.success("base")

    async def test_text_success(self):
        await self.success("text")

    async def test_image_success(self):
        await self.success("image")

    async def test_video_success(self):
        await self.success("video")

    async def test_summary_success(self):
        await self.success("summary")

    async def test_error_categories_are_allowlisted(self):
        for error, kind in (
            (TimeoutError(PRIVATE), "timeout"),
            (ConnectionError(PRIVATE), "connection_error"),
            (OSError(PRIVATE), "io_error"),
            (ValueError(PRIVATE), "value_error"),
            (RuntimeError(PRIVATE), "runtime_error"),
            (type(PRIVATE, (Exception,), {})(PRIVATE), "unexpected_error"),
        ):
            with self.subTest(kind=kind):
                self.sink.records.clear()
                before = self.analyzer.reported_errors
                self.assertIsNone(await self.call_stage("text", error))
                self.assertEqual(self.analyzer.reported_errors, before + 1)
                self.assert_safe_record("text", kind)

    async def test_hostile_exception_is_not_formatted(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.sink.records.clear()
                before = self.analyzer.reported_errors
                self.assertIsNone(await self.call_stage(stage, HostileError(PRIVATE)))
                self.assertEqual(self.analyzer.reported_errors, before + 1)
                self.assert_safe_record(stage, "value_error")

    async def test_missing_models_and_empty_summary_remain_normal_skips(self):
        self.analyzer._get_llm_model.return_value = None
        for stage in STAGES[1:]:
            with self.subTest(stage=stage):
                self.assertIsNone(await self.call_stage(stage))
        self.assertIsNone(await self.analyzer._create_unified_summary({"text_analysis": self.good}, None))
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.client.analyze.assert_not_awaited()
        self.assertFalse(any(r.levelno >= logging.ERROR for r in self.sink.records))


if __name__ == "__main__":
    unittest.main()
