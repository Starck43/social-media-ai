# Analytics Chains — grouping themes over time

Topic chains are the one cross-row link between `ai_analytics` rows
(`topic_chain_id` + `chain_label`). Everything else — web widgets, digest
sections, chat-agent answers — is a **query-time grouping** of the same flat
rows. `summary_data` is never restructured; a chain is identified, not derived.

## Chain resolution flow

Where a row gets its chain:

1. `AIAnalyzer.base_analyze_content()` classifies content, runs the LLM, then
   calls `_generate_topic_chain_id(source, main_topics, agent_scenario,
   analyze_by)` unless the caller passed a `topic_chain_id` (the
   `monitored_users` mode builds one per author up front).
2. The chain id is **deterministic** (no LLM call): the top topic (or `general`
   when there are none) is lowercased, stripped of non-alphanumerics and
   truncated to 20 chars, then combined with the source and scenario ids.
   Modes shape it further:
   - `sources` → `src_{source.id}_{scn}_all` (stable per source)
   - `monitored_users` → `src_{source.id}_{scn}_users` (per person)
   - otherwise → `src_{source.id}_scn_{scn.id}_{normalized_topic}` — an evolving
     theme keeps the same chain across runs.
3. `_resolve_chain_label()` writes a human-readable label (top topic, else the
   analysis title, else "Общая тема"; truncated to the 255-char column). This
   becomes `chain_label` on the saved row.
4. `_find_matching_topic_chain()` (normalization + 50% overlap matching over
   the last N rows) and `_auto_link_to_existing_theme()` (via the theme matcher)
   are the re-link paths — they point a fresh analysis at an existing chain when
   its topics still match.

The `chain_label` column was added by migration `0081` (nullable `String(255)`).

> Note: the `analyze_type` enum on `agent_scenarios` is being replaced by
> orthogonal grouping axes (`group_by` + `time_breakdown`). The analyzer's
> internal `analyze_by` parameter (used for chain ID generation) will follow
> in a subsequent refactor.

## Relevance filtering

`_save_analysis()` reads the scenario's `scope`:

- `scope.relevance_filter` (default `false`) — when true, a row whose LLM
  answer says `is_meaningful == false`, or whose `confidence <
  scope.min_confidence` (default `0.6` from `DEFAULT_ANALYSIS_PARAMS`), is
  skipped BEFORE any write: no row, no dedup, the batch stays retryable.
- Missing model fields default to meaningful (`is_meaningful=true`,
  `confidence=1.0`), so an LLM that omits them never wipes the workspace.
- The counter `AIAnalyzer.filtered_skipped` is bumped per skipped row for job
  results/logs; every skip is also logged with the reason.
- Templates that ship filtering enabled: `brand_monitoring`,
  `customer_support`. The scope keys are top-level (scenario-level), not
  per-analysis-type — `expand_template` carries them through.

## Grouping dimensions

| View | Key | What it shows |
|------|-----|---------------|
| Chains | `topic_chain_id` + `chain_label` | one theme's timeline |
| Themes | `main_topics` across rows | ranked topics |
| Days | `analysis_date` | per-day dynamics (via `time_breakdown`) |
| Sources | `source_id` | per-source activity |
| Entities | `summary_data->'entities'` | brands, persons, orgs |

Web widgets, digest sections, and chat-agent answers all run a different
**query-time grouping** over the same flat `ai_analytics` rows — no data
duplication. The old `analyze_type` enum is being replaced by `group_by` +
`time_breakdown` parameters.

Web widgets (sentiment chart, top topics, entity mentions) each run a different
aggregation over the same `ai_analytics` rows — grouping lives at query time.

## Web display architecture

- `/app/analytics` — aggregate page (sentiment trend, top topics, engagement,
  content mix, LLM cost, activity). Read-only, cheap, no LLM calls.
- `/app/analytics/chains` — every chain, sorted by its latest analysis
  (`?sort=desc|asc`); each chain expands into a retrospective timeline.
- `/app/analytics/chains/{chain_id}` — one chain's full history.
- `/app/analytics/{analysis_id}` — a single saved analysis.
- Delete (single row / whole chain) is `aianalytics.delete` via `guard_web`.
- API: `GET /api/v1/dashboard/topic-chains` and `/topic-chains/{chain_id}`
  (permission `aianalytics.view`), plus the aggregate endpoints under
  `/analytics/aggregate/*` that `ReportAggregator` backs.

## Chat-agent tools

- `analytics_chains(source_id?, days=7, entity_type?)` — chain list with
  `entry_count`, `date_range`, `avg_sentiment` (permission `aianalytics.view`).
- `analytics_chain_detail(chain_id)` — chronological entries of one chain.

## Digest integration

`ReportAggregator.generate_digest_brief()` adds:

- `## Цепочки` — top chains by entry count in the period, with date range and
  average sentiment (independent of the `group_by` axis).
- `## Динамика цепочек` — new chains (`Новая цепочка: "Отпуск" (3 записи)`)
  and continued ones (`Продолжение: "Новый проект" (+2 записи)`), compared
  against a **non-overlapping** previous window.

## Tests

- `tests/test_chain_resolver.py` — stable per-mode chain ids, label resolution,
  re-linking on topic overlap.
- `tests/test_relevance_filter.py` — meaningful/confidence filtering, off by
  default, absent fields never drop a row.
- `tests/test_digest_brief.py` — the "Цепочки" and "Динамика цепочек" sections.
- `tests/test_reporting_contract.py` — the new schema fields (`topic_hint`,
  `confidence`, `is_meaningful`, `entities`) stay claimed.
