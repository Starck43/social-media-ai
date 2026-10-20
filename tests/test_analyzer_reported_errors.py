"""Actual analyzer source with import/call doubles; no app, DB or network.

Run: python tests/test_analyzer_reported_errors.py
Diagnostic-only contract: preserve list/None results, storage and retry behavior.
"""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-PROVIDER-DETAIL"


def load_analyzer():
    # Actual pure compiler import binding; setup only, no test execution.
    from test_scenario_output_contract import load_contract
    schema, _ = load_contract()
    ns = SimpleNamespace
    cost_parser = load_isolated_source(
        "_reported_attempt_journal_cost_parser", ROOT / "app/services/ai/attempt_journal.py"
    )
    # Declared dependency of the analyzer's attempt journal helper; isolated
    # harnesses must declare it so no real import chain or DB is reached.
    attempt_journal = ns(
        record_analysis_attempts=AsyncMock(), provider_cost_evidence=cost_parser.provider_cost_evidence
    )

    async def filter_by_context_receipts(content, *_args):
        return content, set()

    media = type("MediaType", (), {
        "TEXT": ns(db_value="text"), "IMAGE": ns(db_value="image"), "VIDEO": ns(db_value="video")
    })
    utility = load_isolated_source("_reported_attachment_vocabulary", ROOT / "app/utils/content_attachments.py")
    classifier = load_isolated_source("_reported_attachment_classifier", ROOT / "app/services/ai/content_classifier.py",
        {"app.types.enums.llm_types": ns(MediaType=media), "app.utils.content_attachments": utility})
    imports = {
        "app.core.analysis_constants": ns(DEFAULT_ANALYSIS_PARAMS={}),
        "app.core.config": ns(settings=ns(DEBUG=False)),
        "app.models": ns(
            AgentScenario=type("Scenario", (), {}),
            AIAnalytics=type("Analytics", (), {}),
            LLMModel=type("Model", (), {}),
            Source=type("Source", (), {}),
            # The analyzer records the hashes it covered; the isolated run has
            # no receipt store, so the call is a double.
            AnalyzedContentReceipt=type("Receipt", (), {
                "objects": type("Objects", (), {
                    "record_successes": AsyncMock(return_value=None),
                })(),
            }),
        ),
        "app.services.ai.attempt_journal": attempt_journal,
        "app.services.ai.analysis_context": ns(analysis_fingerprint=Mock(return_value="fixture-fingerprint")),
        "app.services.ai.chain_resolver": ns(
            _normalize=Mock(), _token_set_ratio=Mock(), resolve_chain_async=AsyncMock()
        ),
        "app.services.ai.content_classifier": ns(ContentClassifier=ns(
            uncovered_attachment_items=classifier.ContentClassifier.uncovered_attachment_items)),
        "app.services.ai.dedup": ns(
            batch_hash=Mock(), filter_by_context_receipts=filter_by_context_receipts,
            hashes_hash=Mock(), item_hash=Mock()
        ),
        "app.services.ai.scenario_schema": schema,
        "app.services.ai.json_schema_builder": ns(build_pydantic_model=Mock(), validate_with_pydantic=Mock()),
        "app.services.ai.llm_client": ns(LLMClientFactory=ns(create=Mock())),
        "app.services.ai.prompts": ns(PromptBuilder=ns(get_prompt=Mock(return_value="prompt"))),
        "app.services.ai.scenario": ns(build_output_schema=Mock()),
        "app.services.ai.theme_matcher": ns(ThemeMatcher=Mock()),
        "app.types": ns(PeriodType=ns()),
        "app.types.enums.llm_types": ns(MediaType=media),
        "app.utils.date_parsing": ns(universal_date_parser=Mock()),
        "app.utils.enum_helpers": ns(get_enum_value=Mock()),
        "app.utils.translit": ns(translit_slug=Mock()),
    }
    module = load_isolated_source("_reported_analyzer", ROOT / "app/services/ai/analyzer.py", imports)
    module._attempt_journal = attempt_journal
    return module


class ReportedErrorsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_analyzer()
        self.analyzer = self.module.AIAnalyzer()
        self.source = SimpleNamespace(id=1, tenant_id=1, source_type=None)
        self.scenario = SimpleNamespace(
            output_schema={"type": "object"},
            max_tokens=None,
            is_active=True,
            text_llm_model_id=None,
            image_llm_model_id=None,
            video_llm_model_id=None,
            llm_strategy=None,
        )
        self.good = {"parsed": {"analysis": "A measured conclusion"}, "response": {}}
        self.failed = {"parsed": {"analysis": "Error: " + PRIVATE}, "response": {"error": PRIVATE}}
        self.module.ContentClassifier.classify_content = Mock(return_value={"text": [1], "image": [2], "video": []})
        self.analyzer._calculate_content_stats = Mock(return_value={})
        self.analyzer._get_platform_name = AsyncMock(return_value="platform")
        # The real analyzer asks whether a modality has a configured model
        # before calling its stage; this fixture supplies its mocked image stage.
        self.analyzer._get_llm_model = AsyncMock(return_value=SimpleNamespace(id=1))
        self.analyzer._analyze_text = AsyncMock(return_value=self.good)
        self.analyzer._analyze_images = AsyncMock(return_value=self.failed)
        self.analyzer._analyze_videos = AsyncMock(return_value=None)
        self.analyzer._create_unified_summary = AsyncMock(return_value=None)
        self.analyzer._generate_topic_chain_id = AsyncMock(return_value="chain")
        self.analyzer._resolve_chain_label = Mock(return_value="label")
        self.saved = SimpleNamespace(
            id=7, summary_data={"summary": "content"}, response_payload={}
        )
        self.analyzer._save_analysis = AsyncMock(return_value=self.saved)
        self.module.logger = Mock()

    async def base(self, **kwargs):
        return await self.analyzer.base_analyze_content([{"text": "input"}], self.source,
                        force_reanalyze=True, agent_scenario=self.scenario, **kwargs)

    async def test_valid_sibling_cannot_hide_reported_failure(self):
        self.assertIs(await self.base(), self.saved)
        self.assertEqual(self.analyzer.reported_errors, 1)
        # Failed semantic output is cleared without mutating the client envelope.
        results = self.analyzer._save_analysis.call_args.args[0]
        self.assertIs(results["text_analysis"], self.good)
        self.assertEqual(results["image_analysis"]["parsed"], {})
        self.assertIs(results["image_analysis"]["response"], self.failed["response"])
        self.assertEqual(self.failed["parsed"]["analysis"], "Error: " + PRIVATE)
        self.assertTrue(self.analyzer._save_analysis.call_args.kwargs["reported_partial"])

    async def test_all_stub_results_remain_unsaved(self):
        self.analyzer._analyze_text.return_value = self.failed
        self.assertIsNone(await self.base())
        self.assertEqual(self.analyzer.reported_errors, 2)
        self.analyzer._save_analysis.assert_not_awaited()

    async def test_failed_structured_output_is_reported(self):
        self.analyzer._analyze_images.return_value = {"parsed": {}, "response": {"error": "invalid_structured_output"}}
        self.assertIs(await self.base(), self.saved)
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_empty_unknown_result_not_inferred_as_failure(self):
        self.analyzer._analyze_images.return_value = {"parsed": {}}
        await self.base()
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_normal_none_not_inferred_as_failure(self):
        self.analyzer._analyze_images.return_value = None
        self.analyzer._analyze_text.return_value = None
        self.assertIsNone(await self.base())
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_empty_input_has_no_failure(self):
        self.assertEqual(await self.analyzer.analyze_content([], self.source), [])
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_normal_filtered_save_none_not_failure(self):
        self.analyzer._analyze_images.return_value = None
        self.analyzer._save_analysis.return_value = None
        self.assertIsNone(await self.base())
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_exact_receipt_hit_is_neutral_and_skips_model_and_save(self):
        self.module.filter_by_context_receipts = AsyncMock(return_value=([], {"covered-hash"}))
        result = await self.analyzer.base_analyze_content([{}], self.source, agent_scenario=self.scenario)
        self.assertIsNone(result)
        self.assertEqual(self.analyzer.completed_hashes, {"covered-hash"})
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.analyzer._analyze_text.assert_not_awaited()
        self.analyzer._save_analysis.assert_not_awaited()

    async def test_summary_failure_reported_without_discarding_saved_row(self):
        self.analyzer._analyze_images.return_value = self.good
        self.analyzer._create_unified_summary.return_value = self.failed
        self.assertIs(await self.base(), self.saved)
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_base_swallowed_exception_has_bounded_signal(self):
        self.analyzer._save_analysis.side_effect = RuntimeError(PRIVATE)
        self.analyzer._analyze_images.return_value = None
        self.assertIsNone(await self.base())
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_cancelled_error_still_propagates(self):
        self.analyzer._analyze_text.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.base()
        self.assertEqual(self.analyzer.reported_errors, 0)
        self.analyzer._save_analysis.assert_not_awaited()

    async def test_counter_is_instance_local_and_cumulative(self):
        await self.base()
        await self.base()
        self.assertEqual(self.analyzer.reported_errors, 2)
        self.assertEqual(self.module.AIAnalyzer().reported_errors, 0)
        self.assertNotIn(PRIVATE, str(self.analyzer.reported_errors))

    async def test_client_error_envelope_counted_once(self):
        self.analyzer._record_result_error(self.failed)
        self.assertEqual(self.analyzer.reported_errors, 1)
        for unknown in (None, [], "Error: unknown", {}, {"parsed": {}}, {"response": {"error": ""}}):
            self.analyzer._record_result_error(unknown)
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_timeout_stub_without_envelope_is_reported(self):
        self.analyzer._record_result_error({"parsed": {"analysis": "Timeout"}})
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_real_text_method_reports_swallowed_exception(self):
        # Exercise the real helper, not its fixture double.
        self.analyzer._get_llm_model = AsyncMock(return_value=SimpleNamespace(name="model"))
        self.module.ContentClassifier.prepare_text_content = Mock(return_value="input")
        self.module.LLMClientFactory.create.side_effect = RuntimeError(PRIVATE)
        result = await self.module.AIAnalyzer._analyze_text(self.analyzer, [{}], None, {}, "p", self.source)
        self.assertIsNone(result)
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_missing_text_model_is_normal_skip(self):
        self.analyzer._get_llm_model = AsyncMock(return_value=None)
        result = await self.module.AIAnalyzer._analyze_text(self.analyzer, [{}], None, {}, "p", self.source)
        self.assertIsNone(result)
        self.assertEqual(self.analyzer.reported_errors, 0)

    async def test_successful_media_does_not_certify_unprocessed_text_rows(self):
        text_item = {"id": "text", "text": "retry this text"}
        image_item = {"id": "image", "media_url": "https://example.invalid/image"}
        self.module.ContentClassifier.classify_content = Mock(return_value={
            "text": [text_item], "image": [image_item], "video": [],
        })
        self.module.item_hash = lambda item: item["id"]
        self.analyzer._analyze_text.return_value = None
        self.analyzer._analyze_images.return_value = self.good

        result = await self.analyzer.base_analyze_content(
            [text_item, image_item], self.source, force_reanalyze=True, agent_scenario=self.scenario,
        )

        self.assertIs(result, self.saved)
        saved = self.analyzer._save_analysis.call_args.kwargs
        self.assertEqual(saved["content_hashes"], ["image"])
        self.assertTrue(saved["coverage_limited"])
        self.assertTrue(saved["text_coverage_limited"])

    async def test_real_image_and_video_helpers_report_caught_failures(self):
        self.analyzer._get_llm_model = AsyncMock(return_value=SimpleNamespace(name="model"))
        self.module.ContentClassifier.get_media_urls = Mock(return_value=["media"])
        self.module.LLMClientFactory.create.side_effect = RuntimeError(PRIVATE)
        for method in (self.module.AIAnalyzer._analyze_images, self.module.AIAnalyzer._analyze_videos):
            self.assertIsNone(await method(self.analyzer, [{}], None, "p"))
        self.assertEqual(self.analyzer.reported_errors, 2)

    async def test_real_summary_reports_caught_failure(self):
        self.analyzer._get_llm_model = AsyncMock(side_effect=RuntimeError(PRIVATE))
        result = await self.module.AIAnalyzer._create_unified_summary(self.analyzer,
                       {"text_analysis": self.good, "image_analysis": self.good}, None)
        self.assertIsNone(result)
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_no_media_urls_is_normal_skip(self):
        self.analyzer._get_llm_model = AsyncMock(return_value=SimpleNamespace(name="model"))
        self.module.ContentClassifier.get_media_urls = Mock(return_value=[])
        for method in (self.module.AIAnalyzer._analyze_images, self.module.AIAnalyzer._analyze_videos):
            self.assertIsNone(await method(self.analyzer, [{}], None, "p"))
        self.assertEqual(self.analyzer.reported_errors, 0)

class Query:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *args):
        return self

    def limit(self, *args):
        return self

    def __await__(self):
        async def resolve():
            return self.rows
        return resolve().__await__()


class StagedSignalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = SimpleNamespace(id=1, name="source", external_id="source", tenant_id=1)
        scenario = SimpleNamespace(id=1, is_active=True)
        self.staged = [SimpleNamespace(as_agent_item=Mock(return_value={"text": "input"}))]
        self.fresh = [SimpleNamespace(id=2, summary_data={"summary": "content"}, response_payload={})]
        self.analyzer = SimpleNamespace(reported_errors=1, analyze_content=AsyncMock(return_value=self.fresh))
        self.factory = Mock(return_value=self.analyzer)
        self.row = SimpleNamespace(id=4, summary_data={"summary": "content"}, response_payload={})
        dedup = load_isolated_source("_staged_signal_dedup", ROOT / "app/services/ai/dedup.py")
        self.analytics = Mock(return_value=Query([self.row]))
        self.models = SimpleNamespace(
            AgentScenario=SimpleNamespace(objects=SimpleNamespace(get_default_scenario=AsyncMock(return_value=scenario))),
            CollectedItem=SimpleNamespace(objects=SimpleNamespace(
                for_source=AsyncMock(return_value=self.staged),
                count_for_source=AsyncMock(return_value=len(self.staged)),
            )),
            AIAnalytics=SimpleNamespace(objects=SimpleNamespace(filter=self.analytics),
                                        created_at=SimpleNamespace(desc=lambda: None)),
            BotAction=SimpleNamespace(objects=Mock()),
        )
        self.trigger_evaluator = SimpleNamespace(
            should_analyze=AsyncMock(side_effect=lambda content, task: content)
        )
        self.module = load_isolated_source(
            "_staged_analyzer_signal", ROOT / "app/jobs/handlers.py",
            {
                "app.services.social.credentials": SimpleNamespace(AuthorizationRequired=type("Auth", (Exception,), {})),
                "app.models": self.models,
                "app.models.managers.agent_task_manager": SimpleNamespace(AgentTaskManager=lambda: SimpleNamespace(
                    # A taskless run with no owning-task scenario falls through to the
                    # tenant default — the resolution these tests assert.
                    get_source_scenario=AsyncMock(return_value=None))),
                "app.services.ai.dedup": SimpleNamespace(analysed_hashes=dedup.analysed_hashes),
                "app.services.ai.trigger_evaluator": SimpleNamespace(trigger_evaluator=self.trigger_evaluator),
                "app.services.social.guards": SimpleNamespace(extract_target_user=Mock(), guards_checker=Mock()),
                "app.types": SimpleNamespace(BotActionStatus=Mock()),
                "app.services.ai.analyzer": SimpleNamespace(AIAnalyzer=self.factory),
            },
        )
        self.module.logger = Mock()
        self.module._load_task = AsyncMock(return_value=None)
        self.module._task_payload = Mock(return_value={})
        self.module._llm_failure_code = AsyncMock(return_value=None)
        self.module._resolve_content_window = Mock(return_value={})
        self.module._resolve_action_type = Mock(return_value=None)
        self.module._resolve_sources = AsyncMock(return_value=[self.source])
        self.module._retire_staged = AsyncMock(return_value=1)
        self.module._count_failed_staged = AsyncMock(return_value=0)

    async def run_handler(self):
        return await self.module.handle_analyze({})

    def assert_error_once(self, result):
        self.assertEqual((result["error"], result["staged_errors"]), (1, 1))
        self.assertEqual(result["error_sources"], ["source"])
        self.assertTrue(result["per_source"][0]["error"])
        self.assertTrue(result["per_source"][0]["staged_error"])
        self.assertNotIn("status", result)
        self.assertNotIn(PRIVATE, repr(result))

    async def test_reported_partial_preserves_existing_analysis_and_retirement(self):
        result = await self.run_handler()
        self.assert_error_once(result)
        self.assertEqual((result["analyzed"], result["actions_created"]), (1, 0))
        self.module._retire_staged.assert_awaited_once_with(
            self.fresh, 1, 1, completed_hashes=set(), analysis_fingerprint=None
        )
        self.module._count_failed_staged.assert_awaited_once_with(1, self.staged, set())
        self.assertIs(self.analyzer.analyze_content.return_value, self.fresh)

    async def test_multiple_reported_calls_count_source_once(self):
        self.analyzer.reported_errors = 5
        self.assert_error_once(await self.run_handler())

    async def test_later_retirement_failure_does_not_double_staged_error(self):
        self.module._retire_staged.side_effect = RuntimeError(PRIVATE)
        self.assert_error_once(await self.run_handler())
        self.module._count_failed_staged.assert_not_awaited()

    async def test_later_attempt_failure_does_not_double_staged_error(self):
        self.module._count_failed_staged.side_effect = RuntimeError(PRIVATE)
        self.assert_error_once(await self.run_handler())

    async def test_later_outer_failure_does_not_double_source_error(self):
        self.trigger_evaluator.should_analyze.side_effect = RuntimeError(PRIVATE)
        self.assert_error_once(await self.run_handler())
        self.trigger_evaluator.should_analyze.assert_awaited_once()

    async def test_missing_diagnostic_is_unknown_not_error(self):
        del self.analyzer.reported_errors
        result = await self.run_handler()
        self.assertEqual((result["error"], result["staged_errors"]), (0, 0))

    async def test_nonpositive_and_malformed_diagnostics_are_not_coerced(self):
        for value in (0, -1, True, False, 1.0, "1", None, {}, []):
            with self.subTest(value=value):
                self.analyzer.reported_errors = value
                result = await self.run_handler()
                self.assertEqual((result["error"], result["staged_errors"]), (0, 0))

    async def test_failed_empty_fresh_results_still_warn(self):
        self.analyzer.analyze_content.return_value = []
        self.analytics.return_value = Query([])
        result = await self.run_handler()
        self.assert_error_once(result)
        self.assertEqual((result["analyzed"], result["skipped"]), (0, 1))

    async def test_second_source_is_independent(self):
        second = SimpleNamespace(id=2, name="second", external_id="second", tenant_id=1)
        self.module._resolve_sources.return_value = [self.source, second]
        clean = SimpleNamespace(reported_errors=0, analyze_content=AsyncMock(return_value=self.fresh))
        self.factory.side_effect = [self.analyzer, clean]
        result = await self.run_handler()
        self.assert_error_once(result)
        self.assertEqual(result["analyzed"], 2)
        self.assertNotIn("error", result["per_source"][1])

    async def test_cancelled_analyzer_still_propagates(self):
        self.analyzer.analyze_content.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_handler()
        self.module._retire_staged.assert_not_awaited()
        self.module._count_failed_staged.assert_not_awaited()
        self.analytics.assert_not_called()

    async def test_no_staged_rows_does_not_invent_signal(self):
        self.models.CollectedItem.objects.for_source.return_value = []
        result = await self.run_handler()
        self.assertEqual((result["error"], result["staged_errors"]), (0, 0))
        self.factory.assert_not_called()

    async def test_duplicate_hashes_count_each_covered_staged_row(self):
        self.staged = [
            # The projection carries the identity the progress counter matches
            # against saved coverage (`as_agent_item()` does this for real rows).
            SimpleNamespace(
                content_hash="same-hash",
                as_agent_item=Mock(return_value={"text": "same", "content_hash": "same-hash"}),
            ),
            SimpleNamespace(
                content_hash="same-hash",
                as_agent_item=Mock(return_value={"text": "same", "content_hash": "same-hash"}),
            ),
        ]
        self.models.CollectedItem.objects.for_source.return_value = self.staged
        self.models.CollectedItem.objects.count_for_source.return_value = 2
        self.analyzer.reported_errors = 0
        self.analyzer.analyze_content.return_value = [
            SimpleNamespace(summary_data={"content_hashes": ["same-hash"]})
        ]
        self.module._retire_staged.return_value = 2

        result = await self.run_handler()

        per = result["per_source"][0]
        self.assertEqual((per["raw_selected"], per["raw_total"], per["raw_processed"]), (2, 2, 2))

    async def test_optional_count_failure_keeps_running_staged_analysis(self):
        self.models.CollectedItem.objects.count_for_source.side_effect = RuntimeError(PRIVATE)

        result = await self.run_handler()

        self.models.CollectedItem.objects.for_source.assert_awaited()
        self.analyzer.analyze_content.assert_awaited()
        self.assertIsNone(result["per_source"][0]["raw_total"])

    async def test_real_analyzer_partial_signal_reaches_handler(self):
        # Reuse analyzer call fixtures, not the analyzer/handler implementations.
        fixture = ReportedErrorsTests()
        fixture.setUp()
        fixture.scenario.id = 1
        fixture.scenario.is_active = True
        self.models.AgentScenario.objects.get_default_scenario.return_value = fixture.scenario
        fixture.module.filter_by_context_receipts = AsyncMock(side_effect=lambda content, *_args: (content, set()))
        fixture.analyzer._auto_link_to_existing_theme = AsyncMock()
        self.factory.return_value = fixture.analyzer
        result = await self.run_handler()
        self.assert_error_once(result)
        self.assertEqual(result["analyzed"], 1)
        self.module._retire_staged.assert_awaited_once_with(
            [fixture.saved], 1, 1, completed_hashes=set(), analysis_fingerprint="fixture-fingerprint"
        )


if __name__ == "__main__":
    unittest.main()
