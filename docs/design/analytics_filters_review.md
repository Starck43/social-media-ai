# Analytics axes and cross-filter implementation review

## Scope and compatibility

Base: dev commit `e7d512c973107e17cdba6c56acb53ccafb3b611e`. Target: dev only.
No database migration: GroupingAxis is a read-time Python enum; Alembic head stays
0086. Sentiment/content-mix metrics remain available. Public grouped endpoints
reject removed axes, but saved digest payloads normalize them to themes.

## Acceptance

- Main web switcher performs exactly five groupings; active result is reused.
- Removed sentiment/content_type axes return HTTP 400 with supported values.
- Sentiment=negative plus media=image on entities works in web and API.
- Intent with one group is hidden unless active.
- Chain risk sorting is ascending average sentiment, unknown last, filters retained.
- Report tools return top 10 compact aggregates; explicit limit=0 returns all.
- Report previews do not call LLM or write weekly rollups. Existing agent cycle
  uses cost-efficient routing; digest narrative uses configured quality defaults.
- Active providers, workspace tier restrictions, cost caps and permissions remain.
- Documentation synchronized; full-suite result recorded below.

## Validation

Enum-refactor collection succeeded. Focused checks: 109 tests passed before the
final routing regressions; 33 routing/report regressions passed after correcting
a test-only monkeypatch isolation issue. Web/API/filter and risk-sort checks passed.

Full pytest completed on a freshly reset local PostgreSQL `axes_test` schema:

- `920 passed, 1 skipped, 10 warnings in 179.76s (0:02:59)`
- Command: `python -m pytest --no-cov -q`.
- Collection: 921 tests; no enum/import failures.
- Existing SQLAlchemy/Pydantic deprecation warnings remain.
- `git diff --check` passed; no production DB reset or live provider calls.

## File-by-file changes

| File | Change |
| --- | --- |
| `app/agent/prompts.py` | Always-appended aggregate-first context rule; valid axes and explicit full-list limits. |
| `app/agent/runtime.py` | Cost-efficient text preference for the existing chat loop, without an extra narrative call. |
| `app/agent/toolset/reports.py` | Zero-LLM, allowlisted top-N groups and daily chain detail; preview has no digest/rollup writes. |
| `app/api/v1/endpoints/dashboard.py` | Valid axes, sentiment/media params; await grouping, enforce window and optional source filter. |
| `app/models/managers/llm_model_manager.py` | Tariff-first cost-efficient ordering; configured-default quality ordering. |
| `app/services/ai/grouping.py` | Remove two axis branches; shared sentiment extraction and cross-filters before all aggregation. |
| `app/services/ai/llm_client.py` | Preferred active model with tier-safe economical fallbacks. |
| `app/services/ai/reporting.py` | Canonical bucket boundaries, legacy axis normalization and scoped zero-LLM grouping service. |
| `app/services/digest/builder.py` | Legacy saved axis fallback to themes; quality-model digest narrative. |
| `app/types/enums/bot_types.py` | Remove SENTIMENT and CONTENT_TYPE; six Python enum members, no migration. |
| `app/web/analytics.py` | Five count aggregations, active/degenerate tab logic, URL filters and chain risk sorting. |
| `app/web/templates/web/analytics.html` | Remove two tabs; sentiment/media selects, preserved URLs and canonical badge thresholds. |
| `app/web/templates/web/analytics_chains.html` | Keep query filters when changing chain sort. |
| `app/web/templates/web/digests.html` | Offer only supported digest axes. |
| `cli/commands/direct.py` | Supported digest grouping help, including topic_chains. |
| `cli/main.py` | Supported digest grouping help. |
| `docs/AGENT.md` | Compact report contracts, limits, permissions and narrative routing. |
| `docs/AGENT_TASKS.md` | Current supported grouping values. |
| `docs/ANALYTICS_AGGREGATION_SYSTEM.md` | Six-member enum, filters, five-count switcher and token-free/report narrative boundaries. |
| `docs/API.md` | Grouped axes and filters, removed-axis 400 and invalid-filter 422. |
| `docs/DIGEST.md` | Supported axes, saved-task fallback and quality narrative. |
| `docs/MODELS.md` | Current axes and cost-efficient versus quality resolution semantics. |
| `docs/design/competitive_analysis.md` | Align CA-04 resolution description with tariff-first routing. |
| `docs/CLI.md` | Current digest axis values and legacy payload compatibility. |
| `tests/test_compact_reports.py` | Top-N/all limits, no raw data/no digest calls, exact window/chain scope and tier-safe routing. |
| `tests/test_digest_brief.py` | Both removed axes fall back to themes for saved digest briefs. |
| `tests/test_digest_builder.py` | Daily cap prevents quality-model resolution and narrative calls. |
| `tests/test_grouping.py` | Removed axes, canonical boundaries, combined filters and containment semantics. |
| `tests/test_llm_reliability.py` | Cost-efficient picks cheap; quality respects active configured defaults. |
| `tests/test_web_analytics.py` | Web/API negative entities, URL persistence, five counts, auto-hide and risk-sort integration. |

## Limitations and explicit design choices

- No live billable provider calls or production database changes were made.
- Zero-LLM aggregation runs on stored, scoped rows/JSON; it is not a new SQL
  materialization, probe job or embedding platform.
- Cost-efficient means cheapest configured combined input/output tariff. Quality
  means configured defaults, not a new benchmark or an inference from price.
- Headline page widgets remain period-level metrics; new filters affect groups,
  their badges and the chain list. Sentiment display uses the same 0.4/0.6 bounds.
- Raw payloads are excluded from the modified analytics tools. Historical chat
  sessions are not retroactively scrubbed; aggregate-first prompt applies going
  forward. Other operational tools and analyzer strategies are not removed.
- No external roadmap features or master updates are included.
