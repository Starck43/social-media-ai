"""Standalone actual-source digest caveat checks; no app/DB/network bootstrap.

Run: python tests/test_digest_data_completeness.py.
Query/LLM infrastructure doubles do not prove PostgreSQL or live delivery.
"""

import asyncio
import unittest
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 10)
PRIVATE = "PRIVATE-JOB-ERROR-PAYLOAD"


def load(name, path, imports=None):
    return load_isolated_source(name, ROOT / path, imports)


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.ordering = None
        self.limit_value = None
        self.failure = None

    def order_by(self, *args):
        self.ordering = args
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def __await__(self):
        async def resolve():
            if self.failure:
                raise self.failure
            return self.rows[:self.limit_value]

        return resolve().__await__()


def job(*, status="done", job_type="collect", result=None, tenant_id=31):
    return SimpleNamespace(status=status, job_type=job_type, result=result, tenant_id=tenant_id, error=PRIVATE)


class DigestCompletenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tenant_id = 31
        self.bypass = False
        self.query = Query([])
        self.manager = SimpleNamespace(filter=Mock(return_value=self.query))
        self.job_model = SimpleNamespace(
            objects=self.manager,
            finished_at=SimpleNamespace(desc=lambda: "finished_desc"),
            id=SimpleNamespace(desc=lambda: "id_desc"),
        )
        self.coverage = load(
            "_digest_coverage",
            "app/services/digest/coverage.py",
            {
                "app.core.tenant_context": SimpleNamespace(
                    current_tenant_id=lambda: self.tenant_id, is_bypass=lambda: self.bypass
                ),
                "app.models": SimpleNamespace(Job=self.job_model),
            },
        )
        self.render = load(
            "_digest_coverage_render",
            "app/services/digest/render.py",
            {
                "app.channels.max": SimpleNamespace(MAX_TEXT_LEN=4000),
                "app.channels.telegram": SimpleNamespace(split_message=Mock()),
            },
        )
        self.splitter = load("_digest_coverage_splitter", "app/services/digest/html_parts.py")
        self.aggregator = SimpleNamespace(
            get_sentiment_trends=AsyncMock(return_value=[]),
            get_top_topics=AsyncMock(return_value=[]),
            get_content_mix=AsyncMock(return_value={"total": 0}),
            get_engagement_metrics=AsyncMock(return_value={}),
            get_llm_provider_stats=AsyncMock(return_value={}),
            generate_digest_brief=AsyncMock(return_value="stored brief"),
        )
        self.client = SimpleNamespace(analyze=AsyncMock(return_value={"parsed": {"summary": "LLM summary"}}))
        self.model = SimpleNamespace(name="test-model")
        contracts = load("_digest_coverage_contracts", "app/services/ai/output_contracts.py")
        sanitizer = load("_digest_coverage_sanitizer", "app/services/ai/prompt_sanitizer.py")
        self.builder = load(
            "_digest_coverage_builder",
            "app/services/digest/builder.py",
            {
                "app.core.config": SimpleNamespace(settings=SimpleNamespace()),
                "app.services.ai.llm_client": SimpleNamespace(
                    LLMClientFactory=SimpleNamespace(create=Mock(return_value=self.client))
                ),
                "app.services.ai.reporting": SimpleNamespace(
                    ReportAggregator=Mock(return_value=self.aggregator), normalize_digest_axis=lambda value: value
                ),
                "app.services.ai.output_contracts": contracts,
                "app.services.ai.prompt_sanitizer": sanitizer,
                "app.services.digest.coverage": self.coverage,
                "app.services.digest.render": self.render,
                "app.services.tenancy.resolver": SimpleNamespace(
                    current_daily_cost_limit=AsyncMock(return_value=0), daily_cost_today=AsyncMock(return_value=0)
                ),
                "app.models": SimpleNamespace(
                    LLMModel=SimpleNamespace(
                        objects=SimpleNamespace(resolve_default_model=AsyncMock(return_value=self.model))
                    )
                ),
            },
        )
        self.builder.period_bounds = lambda period: (DAY, DAY)

    async def note(self, **kwargs):
        return await self.coverage.job_coverage_note(DAY, DAY, **kwargs)

    async def test_query_has_explicit_workspace_types_status_window_order_and_bound(self):
        await self.note()
        self.manager.filter.assert_called_once_with(
            tenant_id=31,
            job_type__in=["collect", "analyze"],
            status__in=["done", "failed"],
            finished_at__gte=datetime(2026, 10, 10, tzinfo=timezone.utc),
            finished_at__lt=datetime(2026, 10, 11, tzinfo=timezone.utc),
        )
        self.assertEqual(self.query.ordering, ("finished_desc", "id_desc"))
        self.assertEqual(self.query.limit_value, 201)

    async def test_missing_or_bypass_context_does_not_query_global_history(self):
        for tenant_id, bypass in ((None, False), (31, True), (True, False), (0, False)):
            self.tenant_id, self.bypass = tenant_id, bypass
            self.assertIn("недоступна без выбранного", await self.note())
            self.manager.filter.assert_not_called()

    async def test_no_records_cannot_establish_no_publications_or_full_coverage(self):
        self.assertEqual(await self.note(), self.coverage.COVERAGE_UNKNOWN)

    async def test_successful_or_legacy_skipped_results_cannot_establish_coverage(self):
        self.query.rows = [job(result={"error": 0}), job(job_type="analyze", result={"skipped": 2})]
        self.assertEqual(await self.note(), self.coverage.COVERAGE_UNKNOWN)

    async def test_collect_counter_and_terminal_analyze_failure_are_observed_runs(self):
        self.query.rows = [job(result={"error": 2}), job(job_type="analyze", status="failed")]
        note = await self.note()
        self.assertIn("подтверждёнными ошибками: 2", note)
        self.assertIn("UTC", note)
        self.assertNotIn(PRIVATE, note)

    async def test_staged_and_source_errors_count_one_run_not_sum_of_counts(self):
        self.query.rows = [job(job_type="analyze", result={"error": 2, "staged_errors": 1})]
        self.assertIn("подтверждёнными ошибками: 1", await self.note())

    async def test_staged_only_error_is_observed(self):
        self.query.rows = [job(job_type="analyze", result={"staged_errors": 1})]
        self.assertIn("подтверждёнными ошибками: 1", await self.note())

    async def test_selected_source_needs_attributable_per_source_error(self):
        self.query.rows = [job(result={"error": 1, "per_source": [{"source_id": 7, "outcome": "error"}]})]
        self.assertIn("подтверждёнными ошибками: 1", await self.note(source_ids=[7]))
        self.assertEqual(await self.note(source_ids=[8]), self.coverage.COVERAGE_UNKNOWN)

    async def test_selected_source_does_not_use_names_aggregate_payload_or_current_task(self):
        row = job(status="failed", result={"error": 1, "error_sources": [PRIVATE]})
        row.payload = {"source_ids": [7], "scenario_id": 4}
        row.agent_task_id = 9
        self.query.rows = [row]
        self.assertEqual(await self.note(source_ids=[7]), self.coverage.COVERAGE_UNKNOWN)

    async def test_auth_required_and_boolean_staged_marker_are_source_errors(self):
        self.query.rows = [
            job(result={"per_source": [{"source_id": 7, "outcome": "auth_required"}]}),
            job(job_type="analyze", result={"per_source": [{"source_id": 7, "staged_error": True}]}),
        ]
        self.assertIn("подтверждёнными ошибками: 2", await self.note(source_ids=[7]))

    async def test_legacy_malformed_counters_and_untyped_ids_are_not_coerced(self):
        for value in (True, "2", PRIVATE, -1, 0, 1.5, None, [], {}):
            self.query.rows = [job(result={"error": value, "staged_errors": value})]
            self.assertEqual(await self.note(), self.coverage.COVERAGE_UNKNOWN)
        self.query.rows = [job(result={"per_source": [{"source_id": True, "error": True}]})]
        self.assertEqual(await self.note(source_ids=[1]), self.coverage.COVERAGE_UNKNOWN)

    async def test_invalid_selection_is_not_silently_widened(self):
        for source_ids in ([True], [1, True], [0], [-1], ["7"]):
            with self.assertRaises(ValueError):
                await self.note(source_ids=source_ids)
        self.manager.filter.assert_not_called()

    async def test_scenario_filter_has_no_provable_historical_scenario_identity(self):
        self.query.rows = [job(status="failed")]
        note = await self.note(source_ids=[7], scenario_id=4)
        self.assertIn("не подтверждает охват выбранного сценария", note)
        self.assertNotIn("подтверждёнными ошибками:", note)
        self.manager.filter.assert_not_called()

    async def test_scope_backstop_and_scheduled_skip_do_not_report_errors(self):
        self.query.rows = [
            job(tenant_id=99, result={"error": 1}),
            job(job_type="digest", status="failed"),
            job(status="running", result={"error": 1}),
            job(result={"status": "skipped", "error": 1}),
        ]
        self.assertEqual(await self.note(), self.coverage.COVERAGE_UNKNOWN)

    async def test_later_success_does_not_prove_recovery_of_missing_content(self):
        self.query.rows = [job(result={"error": 0}), job(result={"error": 1})]
        self.assertIn("подтверждёнными ошибками: 1", await self.note())

    async def test_limited_history_is_not_presented_as_exhaustive(self):
        self.query.rows = [job(result={"error": 0}) for _ in range(200)] + [job(status="failed")]
        note = await self.note()
        self.assertIn("только последние 200", note)
        self.assertNotIn("подтверждёнными ошибками:", note)

    async def test_read_failure_preserves_digest_but_discloses_unavailable_history(self):
        self.query.failure = RuntimeError(PRIVATE)
        with self.assertLogs(self.coverage.logger, level="WARNING") as logs:
            note = await self.note()
        self.assertIn("История выполнения задач недоступна", note)
        self.assertNotIn(PRIVATE, note + str(logs.output))
        self.assertIsNone(logs.records[0].exc_info)

    async def test_cancelled_read_propagates(self):
        self.query.failure = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.note()

    async def test_aggregate_passes_actual_window_and_scope_to_coverage(self):
        self.builder.job_coverage_note = AsyncMock(return_value="deterministic caveat")
        data, start, end = await self.builder.aggregate("day", [7], scenario_id=4)
        self.builder.job_coverage_note.assert_awaited_once_with(DAY, DAY, [7], 4)
        self.assertEqual(data["coverage_note"], "deterministic caveat")
        self.assertEqual((start, end), (DAY, DAY))
        self.assertEqual(data["stats"]["content_items"], 0)

    async def test_caveat_survives_no_summary_and_html_plain_rendering(self):
        self.query.rows = [job(result={"error": 1, "error_messages": [PRIVATE], "per_source": []})]
        data, _, _ = await self.builder.aggregate("day")
        for text in (self.render.render_digest(data), self.render.render_plain(data)):
            self.assertIn("Полнота сбора и анализа источников не подтверждена", text)
            self.assertIn("подтверждёнными ошибками: 1", text)
            self.assertNotIn(PRIVATE, text)

    async def test_note_is_escaped_and_precedes_narrative_and_long_brief(self):
        data = {
            "period_start": DAY,
            "period_end": DAY,
            "coverage_note": "caveat < > &",
            "brief": "long body " * 1200,
        }
        text = self.render.render_digest(data, summary="LLM narrative")
        self.assertIn("caveat &lt; &gt; &amp;", text)
        self.assertLess(text.index("caveat"), text.index("LLM narrative"))
        parts = self.splitter.split_digest_html(text)
        self.assertGreater(len(parts), 1)
        self.assertIn("caveat &lt; &gt; &amp;", parts[0])
        self.assertEqual(unescape("".join(parts)).count("caveat < > &"), 1)

    async def test_llm_context_contains_caveat_even_when_brief_exceeds_cap(self):
        data = {"brief": "BRIEF " * 1500, "coverage_note": self.coverage.COVERAGE_UNKNOWN}
        summary, _ = await self.builder._summarize(data)
        self.assertEqual(summary, "LLM summary")
        prompt = self.client.analyze.await_args.args[0]
        self.assertIn(self.coverage.COVERAGE_UNKNOWN, prompt)
        self.assertLess(prompt.index(self.coverage.COVERAGE_UNKNOWN), prompt.index("BRIEF"))
        self.assertIn("не обещай полный охват", prompt)

    async def test_llm_failure_cannot_remove_deterministic_warning(self):
        self.client.analyze.side_effect = RuntimeError(PRIVATE)
        data, _, _ = await self.builder.aggregate("day")
        with self.assertLogs(self.builder.logger, level="ERROR"):
            summary, info = await self.builder._summarize(data)
        self.assertIsNone(summary)
        self.assertEqual(info["error"], "llm_call_failed")
        self.assertIn(self.coverage.COVERAGE_UNKNOWN, self.render.render_digest(data, summary))

    async def test_legacy_render_input_without_note_is_unchanged(self):
        data = {"title": "legacy", "period_start": DAY, "period_end": DAY}
        self.assertNotIn("Ограничения данных", self.render.render_digest(data))
        self.assertNotIn("Ограничения данных", self.render.render_plain(data))


if __name__ == "__main__":
    unittest.main()
