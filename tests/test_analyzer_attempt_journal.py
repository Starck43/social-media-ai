"""Analyzer journaling call sites with the real source; no DB or providers.

Run: python tests/test_analyzer_attempt_journal.py
Exercises the actual analyzer helper and both call sites (the partial-B skip
and the no-meaningful-results return) through the isolated analyzer harness;
the attempt journal service itself is covered by test_llm_attempt_journal.py.
"""

import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source
from test_analyzer_reported_errors import load_analyzer

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-JOURNAL-WRITE-FAILURE"

DAY = date(2026, 10, 12)

ENVELOPE = {
    "request": {"model": "text-m", "prompt": "safe-prompt", "provider": "prov"},
    "response": {"usage": {"prompt_tokens": 120, "completion_tokens": 40}},
    "parsed": {"analysis": "A measured conclusion"},
}
SUMMARY = {
    "request": {"model": "summary-m", "prompt": "safe-prompt", "provider": "prov"},
    "response": {"usage": {"prompt_tokens": 60, "completion_tokens": 20}},
    "parsed": {"analysis": "A unified conclusion"},
}
FAILED = {
    "request": {"model": "vision-m", "prompt": "safe-prompt", "provider": "prov"},
    "response": {"error": "provider_failure", "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
    "parsed": {"analysis": "Error: provider_failure"},
}


class AnalyzerAttemptJournalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_analyzer()
        self.module.logger = Mock()
        self.module.PeriodType.DAY = SimpleNamespace(db_value="day")
        self.module.AIAnalytics = SimpleNamespace(
            objects=SimpleNamespace(
                filter=Mock(return_value=SimpleNamespace(first=AsyncMock(return_value=SimpleNamespace(
                    id=42,
                    summary_data={"summary": "existing body", "content_hashes": ["prior-hash"]},
                )))),
                update_by_id=AsyncMock(),
                create=AsyncMock(),
            )
        )
        self.analyzer = self.module.AIAnalyzer()
        self.source = SimpleNamespace(id=1, tenant_id=1, source_type=None)
        self.scenario = SimpleNamespace(output_schema={"type": "object"}, max_tokens=None, is_active=True)
        self.analyzer._get_llm_model = AsyncMock(return_value=SimpleNamespace(id=8))
        self.record = self.module._attempt_journal.record_analysis_attempts

    async def save(self, **kwargs):
        kwargs.setdefault("task_payload", {"job_id": 9, "agent_task_id": 3})
        kwargs.setdefault("analysis_date", DAY)
        kwargs.setdefault("content_hashes", ["incoming-hash"])
        return await self.analyzer._save_analysis(
            {"text_analysis": ENVELOPE}, SUMMARY, self.source, {}, "platform", reported_partial=True, **kwargs
        )

    async def test_partial_collision_still_records_attempts_and_skips_the_row(self):
        result = await self.save()
        self.assertIsNone(result)
        self.record.assert_awaited_once()
        args, kwargs = self.record.call_args
        self.assertEqual(set(args[0]), {"text_analysis"})
        self.assertIs(args[0]["text_analysis"], ENVELOPE)
        self.assertIs(args[1], SUMMARY)
        self.assertEqual(kwargs["source"], self.source)
        self.assertEqual(kwargs["analysis_date"], DAY)
        self.assertEqual(kwargs["task_payload"], {"job_id": 9, "agent_task_id": 3})
        # Keep the existing body; only preserve its known coverage hashes.
        self.module.AIAnalytics.objects.update_by_id.assert_awaited_once_with(
            42,
            summary_data={"summary": "existing body", "content_hashes": ["prior-hash"]},
        )
        self.module.AIAnalytics.objects.create.assert_not_awaited()

    async def test_journal_runs_before_the_skip_decision(self):
        journal_seen = []

        async def recorder(*args, **kwargs):
            journal_seen.append(True)
            return 2

        self.record.side_effect = recorder
        result = await self.save()
        self.assertIsNone(result)
        self.assertEqual(journal_seen, [True])

    async def test_no_meaningful_results_still_records_attempts(self):
        self.module.ContentClassifier.classify_content = Mock(return_value={"text": [1], "image": [2], "video": []})
        self.analyzer._calculate_content_stats = Mock(return_value={})
        self.analyzer._get_platform_name = AsyncMock(return_value="platform")
        self.analyzer._analyze_text = AsyncMock(return_value=FAILED)
        self.analyzer._analyze_images = AsyncMock(return_value=FAILED)
        self.analyzer._analyze_videos = AsyncMock(return_value=None)
        self.analyzer._create_unified_summary = AsyncMock(return_value=None)
        self.analyzer._generate_topic_chain_id = AsyncMock(return_value="chain")
        self.analyzer._resolve_chain_label = Mock(return_value="label")

        result = await self.analyzer.base_analyze_content(
            [{"text": "input"}],
            self.source,
            force_reanalyze=True,
            agent_scenario=self.scenario,
            task_payload={"job_id": 4},
        )
        self.assertIsNone(result)
        self.record.assert_awaited_once()
        args, kwargs = self.record.call_args
        self.assertEqual(set(args[0]), {"text_analysis", "image_analysis"})
        self.assertIsNone(args[1])
        self.assertEqual(kwargs["source"], self.source)
        self.assertEqual(kwargs["task_payload"], {"job_id": 4})
        # The summary is never created on this path, so nothing after it runs.
        self.analyzer._create_unified_summary.assert_not_awaited()

    async def test_journal_failure_never_breaks_the_flow(self):
        self.record.side_effect = RuntimeError(PRIVATE)
        result = await self.save()
        self.assertIsNone(result)
        self.module.logger.warning.assert_called_once_with("attempt_journal_write_failed source_id=%s", 1)
        logged = repr(self.module.logger.warning.call_args)
        self.assertNotIn(PRIVATE, logged)


if __name__ == "__main__":
    unittest.main()
