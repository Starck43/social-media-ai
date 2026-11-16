"""P10-T07 / AUD-07: one immutable scope for every digest report section.

No DB/providers/transports: ORM entry points are faked or pre-loaded row lists
are passed directly. Covers the audit's acceptance points: two sources/two
scenarios, boundary dates, future dates, daily vs weekly/monthly rows,
section agreement, rollup double-count prevention and rollup write selection.
"""

import os
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("SECRET_KEY", "p10-t07-test-key")

from app.services.ai.reporting import ReportAggregator, ReportScope  # noqa: E402
from app.types import PeriodType  # noqa: E402

TODAY = date.today()


def make_row(
    source_id=1,
    scenario=None,
    analysis_date=TODAY,
    period_type=PeriodType.DAY,
    score=0.9,
    chain=None,
    tenant_id=1,
):
    summary = {"multi_llm_analysis": {"text_analysis": {"sentiment_score": score, "main_topics": ["россия"]}}}
    if scenario is not None:
        summary["scenario_metadata"] = {"scenario_id": scenario}
    return SimpleNamespace(
        source_id=source_id,
        summary_data=summary,
        analysis_date=analysis_date,
        period_type=period_type,
        topic_chain_id=chain,
        chain_label=None,
        tenant_id=tenant_id,
        provider_type="test",
        request_tokens=1,
        response_tokens=1,
        estimated_cost=0,
        llm_model="m",
        media_types=["text"],
    )


class FakeQuery:
    """Records filter kwargs, applies them to canned rows, awaits to a list."""

    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls

    def filter(self, *args, **kwargs):
        self.calls.append(kwargs)
        rows = self.rows
        if "analysis_date__gte" in kwargs:
            rows = [r for r in rows if r.analysis_date >= kwargs["analysis_date__gte"]]
        if "analysis_date__lte" in kwargs:
            rows = [r for r in rows if r.analysis_date <= kwargs["analysis_date__lte"]]
        if "period_type" in kwargs:
            rows = [r for r in rows if r.period_type == kwargs["period_type"]]
        if "source_id__in" in kwargs:
            wanted = set(kwargs["source_id__in"])
            rows = [r for r in rows if r.source_id in wanted]
        if "tenant_id" in kwargs:
            rows = [r for r in rows if r.tenant_id == kwargs["tenant_id"]]
        return FakeQuery(rows, self.calls)

    def order_by(self, *args):
        return self

    def __await__(self):
        async def _rows():
            return list(self.rows)

        return _rows().__await__()


def fake_objects(rows, calls):
    return SimpleNamespace(
        filter=lambda *a, **k: FakeQuery(rows, calls).filter(*a, **k), all=lambda: FakeQuery(rows, calls)
    )


class ScopedRowsTests(unittest.IsolatedAsyncioTestCase):
    """The single bounded read behind the scope: window, row kind, selection."""

    async def run_query(self, scope, canned):
        calls = []
        with patch("app.services.ai.reporting.AIAnalytics") as model:
            model.objects = fake_objects(canned, calls)
            model.analysis_date = SimpleNamespace(asc=lambda: "analysis_date")
            rows = await ReportAggregator().scoped_rows(scope)
        return rows, calls

    async def test_inclusive_window_base_rows_and_source_set(self):
        scope = ReportScope(start=TODAY - timedelta(days=6), end=TODAY, source_ids=frozenset({1, 2}), scenario_id=None)
        canned = [
            make_row(source_id=1, analysis_date=scope.start),  # boundary: first day
            make_row(source_id=2, analysis_date=scope.end),  # boundary: last day
            make_row(source_id=1, analysis_date=scope.start - timedelta(days=1)),  # before window
            make_row(source_id=1, analysis_date=scope.end + timedelta(days=1)),  # future
            make_row(source_id=3, analysis_date=TODAY),  # unselected source
            make_row(source_id=1, analysis_date=TODAY, period_type=PeriodType.WEEK),  # saved rollup
            make_row(source_id=2, analysis_date=TODAY, period_type=PeriodType.MONTH),
        ]
        rows, calls = await self.run_query(scope, canned)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.source_id for r in rows}, {1, 2})
        merged = {k: v for call in calls for k, v in call.items()}
        self.assertEqual(merged["analysis_date__gte"], scope.start)
        self.assertEqual(merged["analysis_date__lte"], scope.end)
        self.assertEqual(merged["period_type"], PeriodType.DAY)
        self.assertEqual(merged["source_id__in"], [1, 2])

    async def test_day_window_starts_today_not_yesterday(self):
        # AUD-07 regression: the legacy query used `today - days`, so a day
        # report included yesterday. period_bounds and the scope must agree.
        from app.services.digest.builder import period_bounds

        start, end = period_bounds("day")
        scope = ReportScope(start=start, end=end)
        self.assertEqual(scope.start, TODAY)
        self.assertEqual(scope.end, TODAY)
        rows, calls = await self.run_query(scope, [make_row(analysis_date=TODAY - timedelta(days=1))])
        self.assertEqual(rows, [])
        merged = {k: v for call in calls for k, v in call.items()}
        self.assertEqual(merged["analysis_date__gte"], TODAY)

    async def test_scenario_filter_keeps_only_the_selected_scenario(self):
        scope = ReportScope(start=TODAY, end=TODAY, scenario_id=7)
        canned = [
            make_row(scenario=7),
            make_row(scenario=8),
            make_row(scenario=None),  # pre-metadata rows match no scenario
        ]
        rows, _ = await self.run_query(scope, canned)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].summary_data["scenario_metadata"]["scenario_id"], 7)

    async def test_previous_window_is_same_shape_and_never_overlaps(self):
        scope = ReportScope(start=TODAY - timedelta(days=6), end=TODAY, source_ids=frozenset({1}))
        prev = scope.previous()
        self.assertEqual(prev.end, scope.start - timedelta(days=1))
        self.assertEqual(prev.days, scope.days)
        self.assertEqual(prev.source_ids, scope.source_ids)
        self.assertLess(prev.end, scope.start)

    async def test_tenant_and_scenario_filters_compose_with_source_selection(self):
        scope = ReportScope(start=TODAY, end=TODAY, source_ids=frozenset({1}), scenario_id=7, tenant_id=1)
        selected = make_row(source_id=1, scenario=7, tenant_id=1)
        rows, _ = await self.run_query(
            scope,
            [
                selected,
                make_row(source_id=2, scenario=7),
                make_row(source_id=1, scenario=8),
                make_row(source_id=1, scenario=7, tenant_id=2),
            ],
        )
        self.assertEqual(rows, [selected])


class SectionParityTests(unittest.IsolatedAsyncioTestCase):
    """Every displayed section reads the same pre-loaded scope rows."""

    async def test_aggregate_feeds_identical_rows_to_all_sections(self):
        from app.services.digest import builder

        scope_rows = [make_row(source_id=1, scenario=7), make_row(source_id=2, scenario=7, score=0.1)]
        seen = {}

        def spy(name, orig):
            async def wrapped(*args, **kwargs):
                seen[name] = kwargs.get("rows")
                return await orig(*args, **kwargs)

            return wrapped

        scenario = SimpleNamespace(analysis_types=None)
        with (
            patch("app.models.AgentScenario.objects", SimpleNamespace(get=AsyncMock(return_value=scenario))),
            patch.object(ReportAggregator, "scoped_rows", AsyncMock(return_value=scope_rows)) as query,
            patch.object(
                ReportAggregator, "get_sentiment_trends", spy("sentiment", ReportAggregator.get_sentiment_trends)
            ),
            patch.object(ReportAggregator, "get_top_topics", spy("topics", ReportAggregator.get_top_topics)),
            patch.object(ReportAggregator, "get_content_mix", spy("mix", ReportAggregator.get_content_mix)),
            patch.object(
                ReportAggregator, "get_engagement_metrics", spy("engagement", ReportAggregator.get_engagement_metrics)
            ),
            patch.object(
                ReportAggregator, "get_llm_provider_stats", spy("llm", ReportAggregator.get_llm_provider_stats)
            ),
            patch.object(
                ReportAggregator, "generate_digest_brief", spy("brief", ReportAggregator.generate_digest_brief)
            ),
            patch.object(builder, "job_coverage_note", AsyncMock(return_value="note")),
        ):
            from app.core.tenant_context import tenant_scope

            with tenant_scope(1):
                data, start, end = await builder.aggregate("day", source_ids=[1, 2], scenario_id=7)

        current, previous = [call.args[0] for call in query.await_args_list]
        self.assertEqual(current.tenant_id, 1)
        self.assertEqual(current.source_ids, frozenset({1, 2}))
        self.assertEqual(current.scenario_id, 7)
        self.assertEqual(previous, current.previous())
        for name in ("sentiment", "topics", "mix", "engagement", "llm", "brief"):
            self.assertIs(seen[name], scope_rows, f"{name} did not reuse the scoped rows")
        # Sections reflect exactly the two scoped rows, nothing workspace-wide.
        self.assertEqual(data["stats"]["analyses"], 2)
        self.assertEqual(data["sentiment"]["distribution"], {"positive": 1, "neutral": 0, "negative": 1})

    async def test_empty_scope_rows_never_fall_back_to_a_query(self):
        agg = ReportAggregator()
        forbidden = AsyncMock(side_effect=AssertionError("must not re-query when rows are supplied"))
        with patch.object(ReportAggregator, "_analytics_query", forbidden):
            self.assertEqual(await agg.get_top_topics(rows=[]), [])
            self.assertEqual(await agg.get_sentiment_trends(rows=[]), [])
            self.assertEqual((await agg.get_content_mix(rows=[]))["total_analyses"], 0)
        forbidden.assert_not_awaited()

    async def test_brief_sections_and_dynamics_share_the_scope(self):
        agg = ReportAggregator()
        scope = ReportScope(start=TODAY, end=TODAY, source_ids=frozenset({1}), scenario_id=7)
        rows = [make_row(source_id=1, scenario=7, chain="src_1_user_9")]
        scoped_calls = []

        async def fake_scoped(self, s):
            scoped_calls.append(s)
            return [make_row(source_id=1, scenario=7, score=0.2)]  # previous window rows

        scenario = SimpleNamespace(analysis_types=None)
        with (
            patch("app.models.AgentScenario.objects", SimpleNamespace(get=AsyncMock(return_value=scenario))),
            patch.object(ReportAggregator, "scoped_rows", fake_scoped),
        ):
            brief = await agg.generate_digest_brief(period="day", source_ids=[1], scenario_id=7, scope=scope, rows=rows)
        # Both dynamics reuse the same preceding window, never a fresh
        # unscoped today-window or a second materialization of its JSON rows.
        self.assertEqual(len(scoped_calls), 1)
        self.assertTrue(all(s == scope.previous() for s in scoped_calls))
        self.assertIsInstance(brief, str)

    async def test_all_specialized_sections_reuse_rows_and_scope_header(self):
        from contextlib import ExitStack

        agg = ReportAggregator()
        scope = ReportScope(start=TODAY - timedelta(days=10), end=TODAY - timedelta(days=10), scenario_id=7)
        rows = [make_row(scenario=7)]
        methods = {name: AsyncMock(return_value={}) for _, _, name in agg._specialized_sections()}
        enabled = [name for name, _, _ in agg._specialized_sections()]
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.models.AgentScenario.objects",
                    SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(analysis_types=enabled))),
                )
            )
            previous = stack.enter_context(patch.object(agg, "scoped_rows", AsyncMock(return_value=[])))
            for name, method in methods.items():
                stack.enter_context(patch.object(agg, name, method))
            brief = await agg.generate_digest_brief(scope=scope, rows=rows)
        for method in methods.values():
            method.assert_awaited_once()
            self.assertIs(method.await_args.kwargs["rows"], rows)
            self.assertEqual(method.await_args.kwargs["scenario_id"], 7)
        self.assertIn(f"{scope.start.isoformat()} — {scope.end.isoformat()}", brief)
        previous.assert_awaited_once_with(scope.previous())

    async def test_standalone_brief_uses_two_bounded_windows(self):
        agg = ReportAggregator()
        with patch.object(agg, "scoped_rows", AsyncMock(side_effect=[[make_row()], []])) as query:
            await agg.generate_digest_brief(period="week", source_ids=[1, 2])
        current, previous = [call.args[0] for call in query.await_args_list]
        self.assertEqual(current.days, 7)
        self.assertEqual(current.source_ids, frozenset({1, 2}))
        self.assertEqual(previous, current.previous())

    async def test_scoped_dynamics_keep_selected_comparison_and_chain_movement(self):
        agg = ReportAggregator()
        scope = ReportScope(start=TODAY, end=TODAY)
        current = [
            make_row(score=0.9, chain="continued"),
            make_row(score=0.9, chain="continued"),
            make_row(score=0.9, chain="new"),
        ]
        previous = [make_row(score=0.2, chain="continued")]
        for row in current + previous:
            row.chain_label = row.topic_chain_id
        with patch.object(agg, "scoped_rows", AsyncMock(side_effect=AssertionError("must reuse"))):
            sentiment = await agg._brief_dynamics(1, scope=scope, current_rows=current, previous_rows=previous)
            chains = await agg._chain_dynamics(1, scope=scope, current_rows=current, previous_rows=previous)
        self.assertIn("0.20 → 0.90", "\n".join(sentiment))
        self.assertIn("new", "\n".join(chains))
        self.assertIn("continued", "\n".join(chains))


class RollupSelectionTests(unittest.IsolatedAsyncioTestCase):
    """Rollup writes follow the digest's source selection; base reads exclude them."""

    async def run_rollups(self, source_ids, daily_rows):
        from app.models.managers.ai_analytics_manager import AIAnalyticsManager

        manager = AIAnalyticsManager()
        created = []
        calls = []

        def fake_filter(**kwargs):
            calls.append(kwargs)
            rows = daily_rows
            if "source_id__in" in kwargs:
                wanted = set(kwargs["source_id__in"])
                rows = [r for r in rows if r.source_id in wanted]
            if "period_type" in kwargs:
                rows = [r for r in rows if r.period_type == kwargs["period_type"]]
            qs = FakeQuery(rows, [])

            async def _first():
                return None

            qs.first = _first
            return qs

        manager.filter = fake_filter
        manager.create = AsyncMock(side_effect=lambda **kw: created.append(kw))
        manager.update_by_id = AsyncMock()
        written = await manager.build_period_rollups(PeriodType.WEEK, TODAY - timedelta(days=6), TODAY, source_ids)
        return written, created, calls

    async def test_selected_digest_rolls_up_only_selected_sources(self):
        daily = [make_row(source_id=1), make_row(source_id=2)]
        written, created, calls = await self.run_rollups([1], daily)
        self.assertEqual(written, 1)
        self.assertEqual([c["source_id"] for c in created], [1])
        self.assertEqual(calls[0]["source_id__in"], [1])

    async def test_unselected_digest_keeps_whole_workspace_rollups(self):
        daily = [make_row(source_id=1), make_row(source_id=2)]
        written, created, calls = await self.run_rollups(None, daily)
        self.assertEqual(written, 2)
        self.assertNotIn("source_id__in", calls[0])

    async def test_rollup_rows_never_reenter_scoped_reads(self):
        # A saved WEEK rollup inside the window is invisible to base-row reads:
        # no double count of the daily inputs it folded.
        scope = ReportScope(start=TODAY - timedelta(days=6), end=TODAY)
        canned = [
            make_row(source_id=1, analysis_date=TODAY),
            make_row(source_id=1, analysis_date=TODAY - timedelta(days=6), period_type=PeriodType.WEEK),
        ]
        calls = []
        with patch("app.services.ai.reporting.AIAnalytics") as model:
            model.objects = fake_objects(canned, calls)
            model.analysis_date = SimpleNamespace(asc=lambda: "analysis_date")
            rows = await ReportAggregator().scoped_rows(scope)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].period_type, PeriodType.DAY)


if __name__ == "__main__":
    unittest.main()
