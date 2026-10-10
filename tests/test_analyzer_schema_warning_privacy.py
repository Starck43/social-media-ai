"""Four actual analyzer schema-warning sinks; stdlib/module-local doubles only.

Run: python tests/test_analyzer_schema_warning_privacy.py
No real schema/DB/provider/bootstrap; preserve existing unvalidated fallback.
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
        self.module.ContentClassifier.prepare_text_content = Mock(return_value=PRIVATE)
        self.module.ContentClassifier.get_media_urls = Mock(return_value=[PRIVATE])
        self.client = SimpleNamespace(analyze=AsyncMock(return_value=self.fixture.good))
        self.module.LLMClientFactory.create.return_value = self.client
        self.module.validate_with_pydantic.side_effect = lambda parsed, model, strict: parsed

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
        warnings = [r for r in self.sink.records if r.levelno >= logging.WARNING]
        self.assertEqual(len(warnings), 1)
        record = warnings[0]
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual(record.getMessage(), f"analysis_schema_build_failed stage={stage} error_kind={kind}")
        self.assertEqual(record.args, (kind,))
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)
        rendered = logging.Formatter("%(levelname)s %(message)s").format(record)
        self.assertNotIn(PRIVATE, rendered)
        self.assertNotIn("Traceback", rendered)
        self.assertNotIn(PRIVATE, str(vars(record)))

    async def fallback(self, stage):
        f = self.fixture
        self.module.build_pydantic_model.side_effect = ValueError(PRIVATE)
        result = await self.invoke(stage)
        self.assert_safe_warning(stage, "value_error")
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.module.validate_with_pydantic.assert_not_called()
        if stage == "save":
            self.assertIs(result, f.existing)
            self.assertEqual(result.summary_data["multi_llm_analysis"]["text_analysis"], f.good["parsed"])
            self.assertEqual(result.content_hash, "batch")
            self.assertEqual(result.summary_data["content_hashes"], ["item"])
            self.assertEqual((result.request_tokens, result.response_tokens), (10, 4))
            f.create.assert_awaited_once()
        else:
            self.assertIs(result, f.good)
            self.client.analyze.assert_awaited_once()
            self.assertIsNone(self.client.analyze.call_args.kwargs["pydantic_model"])
        self.module.build_pydantic_model.assert_called_once_with(f.scenario)

    async def success(self, stage):
        model = object()
        self.module.build_pydantic_model.return_value = model
        result = await self.invoke(stage)
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))
        if stage == "save":
            self.assertIs(result, self.fixture.existing)
            self.module.validate_with_pydantic.assert_called_once_with(self.fixture.good["parsed"], model, strict=True)
        else:
            self.assertIs(result, self.fixture.good)
            self.assertIs(self.client.analyze.call_args.kwargs["pydantic_model"], model)

    async def cancellation(self, stage):
        self.module.build_pydantic_model.side_effect = asyncio.CancelledError(PRIVATE)
        with self.assertRaises(asyncio.CancelledError):
            await self.invoke(stage)
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.client.analyze.assert_not_awaited()
        self.fixture.create.assert_not_awaited()
        self.fixture.update.assert_not_awaited()
        self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_text_fallback(self):
        await self.fallback("text")

    async def test_image_fallback(self):
        await self.fallback("image")

    async def test_video_fallback(self):
        await self.fallback("video")

    async def test_save_fallback(self):
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
                self.module.build_pydantic_model.side_effect = HostileSchemaError(PRIVATE)
                self.assertIsNotNone(await self.invoke(stage))
                self.assertEqual(self.analyzer.reported_errors, 0)
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
                self.module.build_pydantic_model.side_effect = error
                self.assertIs(await self.invoke("text"), self.fixture.good)
                self.assert_safe_warning("text", kind)

    async def test_absent_schema_does_not_build_or_warn(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.setUp()
                self.fixture.scenario.output_schema = None
                self.assertIsNotNone(await self.invoke(stage))
                self.module.build_pydantic_model.assert_not_called()
                self.assertFalse(any(r.levelno >= logging.WARNING for r in self.sink.records))

    async def test_validation_rejection_is_not_a_schema_build_warning(self):
        self.module.build_pydantic_model.return_value = object()
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
