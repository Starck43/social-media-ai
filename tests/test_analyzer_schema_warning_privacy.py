"""Actual analyzer fail-closed schema sinks; module-local doubles only.

Run: python tests/test_analyzer_schema_warning_privacy.py
Prepared semantic alignment: no unvalidated fallback; execution deferred. No DB/provider/bootstrap.
"""

import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import test_partial_analysis_coverage as storage_fixtures

PRIVATE = "PRIVATE-schema-value-token-url-prompt"
STAGES = ("text", "image", "video", "save")


class RecordSink(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class HostileSchemaError(ValueError):
    def __str__(self):
        raise AssertionError("Schema exception must not be formatted")

    def __repr__(self):
        raise AssertionError("Schema exception must not be repr'd")


class AnalyzerSchemaWarningPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reuse fixture setup only, never execute its historical tests.
        self.fixture = storage_fixtures.PartialCoverageTests()
        self.fixture.setUp()
        self.module = self.fixture.module
        self.analyzer = self.fixture.analyzer
        self.sink = RecordSink()
        self.module.logger = logging.Logger("isolated_schema_warning", logging.DEBUG)
        self.module.logger.addHandler(self.sink)
        self.analyzer._get_llm_model = AsyncMock(return_value=SimpleNamespace(name="model"))
        self.module.ContentClassifier.select_text_content = Mock(side_effect=lambda items: items)
        self.module.ContentClassifier.prepare_text_content = Mock(return_value=PRIVATE)
        self.module.ContentClassifier.get_media_urls = Mock(return_value=[PRIVATE])
        self.client = SimpleNamespace(analyze=AsyncMock(return_value=self.fixture.good))
        self.module.LLMClientFactory.create.return_value = self.client
        self.module.validate_with_pydantic.side_effect = lambda parsed, model, strict: parsed
        self.contract = SimpleNamespace(model=object(), schema={"type": "object"})
        self.module.compile_scenario_output = Mock(return_value=self.contract)

    async def invoke(self, stage):
        f = self.fixture
        cls = self.module.AIAnalyzer
        if stage == "text":
            return await cls._analyze_text(self.analyzer, [{}], f.scenario, {}, "platform", f.source)
        if stage == "image":
            return await cls._analyze_images(self.analyzer, [{}], f.scenario, "platform")
        if stage == "video":
            return await cls._analyze_videos(self.analyzer, [{}], f.scenario, "platform")
        return await self.analyzer._save_analysis(
            {"text_analysis": f.good}, None, f.source, {"total_posts": 1}, "platform",
            f.scenario, content_hash="batch", content_hashes=["item"],
        )

    def assert_safe_warning(self, stage, kind):
        records = [r for r in self.sink.records if r.levelno >= logging.WARNING]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertEqual(record.getMessage(), f"analysis_failed stage={stage} error_kind={kind}")
        self.assertEqual(record.args, (kind,))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)
        self.assertNotIn(PRIVATE, str(vars(record)))
        self.assertNotIn("Traceback", logging.Formatter().format(record))

    async def fallback(self, stage):
        # Historical name retained for callers; semantic guarantee is now rejection.
        self.module.compile_scenario_output.side_effect = ValueError(PRIVATE)
        if stage == "save":
            with self.assertRaises(ValueError):
                await self.invoke(stage)
            self.fixture.create.assert_not_awaited()
            self.fixture.update.assert_not_awaited()
            self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))
        else:
            self.assertIsNone(await self.invoke(stage))
            self.assert_safe_warning(stage, "value_error")
            self.assertEqual(self.analyzer.reported_errors, 1)
            self.analyzer._get_llm_model.assert_not_awaited()
            self.client.analyze.assert_not_awaited()
        self.module.validate_with_pydantic.assert_not_called()
        self.module.compile_scenario_output.assert_called_once_with(self.fixture.scenario)

    async def success(self, stage):
        result = await self.invoke(stage)
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))
        if stage == "save":
            self.assertIs(result, self.fixture.existing)
            self.module.validate_with_pydantic.assert_called_once_with(
                self.fixture.good["parsed"], self.contract.model, strict=True)
        else:
            self.assertEqual(result["parsed"], self.fixture.good["parsed"])
            self.assertIs(self.client.analyze.call_args.kwargs["pydantic_model"], self.contract.model)
        self.module.compile_scenario_output.assert_called_once_with(self.fixture.scenario)

    async def cancellation(self, stage):
        self.module.compile_scenario_output.side_effect = asyncio.CancelledError(PRIVATE)
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke(stage)
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.client.analyze.assert_not_awaited()
        self.fixture.create.assert_not_awaited()
        self.fixture.update.assert_not_awaited()
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_text_rejection(self):
        await self.fallback("text")

    async def test_image_rejection(self):
        await self.fallback("image")

    async def test_video_rejection(self):
        await self.fallback("video")

    async def test_save_rejection(self):
        await self.fallback("save")

    async def test_text_schema_success(self):
        await self.success("text")

    async def test_image_schema_success(self):
        await self.success("image")

    async def test_video_schema_success(self):
        await self.success("video")

    async def test_save_schema_success(self):
        await self.success("save")

    async def test_text_cancellation(self):
        await self.cancellation("text")

    async def test_image_cancellation(self):
        await self.cancellation("image")

    async def test_video_cancellation(self):
        await self.cancellation("video")

    async def test_save_cancellation(self):
        await self.cancellation("save")

    async def test_hostile_schema_error_is_not_formatted(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.setUp()
                self.module.compile_scenario_output.side_effect = HostileSchemaError(PRIVATE)
                if stage == "save":
                    with self.assertRaises(HostileSchemaError):
                        await self.invoke(stage)
                    self.fixture.create.assert_not_awaited()
                    self.fixture.update.assert_not_awaited()
                else:
                    self.assertIsNone(await self.invoke(stage))
                    self.assertEqual(self.analyzer.reported_errors, 1)
                    self.assert_safe_warning(stage, "value_error")

    async def test_categories_reuse_existing_allowlist(self):
        for error, kind in (
            (TimeoutError(PRIVATE), "timeout"),
            (ConnectionError(PRIVATE), "connection_error"),
            (OSError(PRIVATE), "io_error"),
            (RuntimeError(PRIVATE), "runtime_error"),
            (type(PRIVATE, (Exception,), {})(PRIVATE), "unexpected_error"),
        ):
            with self.subTest(kind=kind):
                self.setUp()
                self.module.compile_scenario_output.side_effect = error
                self.assertIsNone(await self.invoke("text"))
                self.assert_safe_warning("text", kind)

    async def test_absent_custom_schema_uses_derived_contract(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.setUp()
                self.fixture.scenario.output_schema = None
                self.assertIsNotNone(await self.invoke(stage))
                self.module.compile_scenario_output.assert_called_once_with(self.fixture.scenario)
                self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_validation_rejection_is_not_a_schema_build_warning(self):
        self.module.compile_scenario_output.return_value = self.contract
        error = ValueError(PRIVATE)
        self.module.validate_with_pydantic.side_effect = error
        with self.assertRaises(ValueError) as caught:
            await self.invoke("save")
        self.assertIs(caught.exception, error)
        self.fixture.create.assert_not_awaited()
        self.fixture.update.assert_not_awaited()
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))


if __name__ == "__main__":
    unittest.main()
