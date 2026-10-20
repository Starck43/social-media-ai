# P5 — Safe AI boundaries (gate PRD-07)

Goal: invalid typed output cannot gain authority; partial evidence coverage is
explicit; useful siblings remain available; failed outputs are persisted
safely.

## Tasks

- [ ] P5-T01 — select and close one bounded typed-output, hostile-input,
  evidence-coverage or failed-output persistence gap; accept: gap verified in
  current code, fix keeps boundary invariants, focused tests via R10;
  depends: none; owns: per selection; owner: unassigned; status: TODO

- [x] P5-T02 — audit the collection-to-analysis path for task 1582 and scenario
  8, including source 2939; trace task/source conditions through collection,
  prompt construction, model output, persistence and analysis UI using existing
  models; inspect current public data read-only; compare the current prompt with
  one that carries the configured target brands/hashtag on a bounded control
  set using model 11, temperature 0 and at most 1024 output tokens per call;
  retain normal usage/cost accounting and do not modify task, raw-content or
  AIAnalytics records; document evidence and prioritized fixes in
  docs/audits/2026-10-17-content-relevance.md; accept: report distinguishes
  verified historical facts from controlled comparison, records prompt/output
  relevance and false inclusions, and identifies limits where source material
  is unavailable; evidence: docs/audits/2026-10-17-content-relevance.md,
  source SHA b70911d6c1e0c6a71e5668a502df42f1e73fefa9, LLMAttempt IDs 21 and
  22 with normal usage accounting; protected table snapshots unchanged;
  depends: none; owns: audit/report; owner: codex-content-relevance;
  reviewed by: coordinating Codex on 2026-10-17 against the source SHA above
  and the report diff; status: DONE

- [x] P5-T03 — inspect newly collected public-schema raw records for task 1582
  and source 2939 after the operator's successful collection; verify the actual
  VK request selection, requested period and origin from existing task/source
  configuration; review content relevance against configured brands/hashtags
  without exposing raw text in the report; inspect keywords/topics form
  validation and flash behavior that may block “Сохранить и выполнить”; document
  findings and a bounded fix recommendation. Accept: distinguish observed rows
  and request parameters from inferred intent, provide exact task/job/source
  references, explain any validation/flash failure path, and recommend a scoped
  correction. No code, prompt, task, raw-data or analytics changes; no LLM calls
  or tests in this audit. Evidence: [VK raw content audit](../audits/2026-10-18-vk-raw-content.md),
  source SHA `b70911d6c1e0c6a71e5668a502df42f1e73fefa9`. The bounded VK probe
  attempted `utils.resolveScreenName` but timed out before `wall.get`; original
  response and post/media origin therefore remain unverified and are tracked in
  ROADMAP Q-2. The 51 rows are compatible with pagination over a default page
  size of 50. Report diff and bounded evidence reviewed by coordinating Codex
  on 2026-10-18 at the source SHA above; audit status: DONE. Origin verification
  remains OPEN in Q-2. Depends: operator-provided successful collection run;
  owns: audit/report only;
  owner: codex-vk-raw-audit.

- [ ] P5-T04 — remove the web `force_refresh` / `force_reanalyze` bypass of
  duplicate-analysis protection and enforce web skip-by-default with durable,
  context-scoped success receipts. Receipt identity is workspace, source, content
  hash and a versioned fingerprint of the resolved scenario and semantic
  configuration/targets. Receipts have no expiry and survive analytics deletion
  and normal raw cleanup; lookup fails closed, and receipts are written only for
  successfully persisted coverage in the analytics transaction. `AIAnalytics`
  daily uniqueness and generated rollups are scoped by fingerprint. Existing
  normal web source-job admission serializes overlapping source runs; no new
  in-flight receipt claim was added, and simultaneous direct CLI runs are not
  globally serialized by this feature. Web legacy force flags are ignored.
  Legacy analytics remain unattributed: historical merged hashes cannot be
  safely assigned to a scenario, so no unsafe receipt backfill is performed and
  content without a provable receipt may be analyzed once under the new policy.
  Raw cleanup protects staged rows needed by another active/queued context within
  its configured date window; after cleanup a newly introduced context needs
  collection again. The direct CLI `analyze --keep-raw` option is newly added and
  preserves staged text after successful analysis. UI analytics deletion remains
  present and permission guarded. VK L1/L2 requests and media collection are out
  of scope. Verified state: migration 0104 has been applied on user approval to
  the public schema and read-only re-verified; all 26 prior analytics records
  remain and no unsafe receipt backfill was performed; source 2939 still holds
  raw 51 / analytics 23. No-DB acceptance scope passed: 67 tests. Focused
  isolated DB scope on `test_analysis_receipts`: 17 passed, 1 failed; the
  failure
  (`tests/test_web_analytics.py::test_workspace_owner_without_structured_delete_right_cannot_delete_analytics`)
  asserts a workspace owner without a structured delete right sees no delete
  forms even though the owner membership grants workspace permissions — an
  unrelated permission expectation that is not resolved here, so full DB scope
  is not passed. Safari UI check was not completed; by code inspection and
  automated form tests no `force_refresh` / `force_reanalyze` controls remain in
  the web task edit/template form. No VK/LLM calls were made during
  verification. Evidence: implementation on dev SHA
  `c243e39ecc772e8282a51fac026c6a9106bb27aa` (feature/test changes still
  uncommitted); the isolated test snapshot was based on
  `3e5779460aa9cbb277a5b5c8c0a7ab8e27b4c526` and contains the same
  feature/test changes; depends: none; owns: analysis
  receipt design/implementation and focused tests; owner: codex-analysis-receipts;
  status: IN_PROGRESS.

- [ ] P5-T05 — bounded-output map-reduce analysis pipeline so any text model,
  including small output caps (observed: `zai-org/GLM-5.3-Flash` truncated at
  2048 completion tokens), can process any staged volume: per-item map
  extraction with a fixed compact output contract, a bounded reduce/aggregation
  stage, truncation visibility (`finish_reason`/effective max tokens) in the
  attempt journal, output-budget chunk descent with per-chunk retry, drain loop
  bounded by a per-run token/cost/time budget; the whole-batch single request
  path stays until all slices land. Design: docs/design/analysis-map-reduce.md;
  accept: truncated response no longer zeroes the whole batch, per-item coverage
  is visible in job progress, focused tests via R10 (mocked LLM), costs stay
  journaled and capped; depends: P5-T04 (receipts), P8-T22 (shared
  analyzer/handler files); owns: design doc, app/services/ai/analyzer.py,
  app/services/ai/attempt_journal.py, app/jobs/handlers.py; owner: $local
  (opencode); status: IN_PROGRESS (design)

- [ ] P5-T06 — theme-chain similarity search works against `ai_analytics.main_topics`
  (json column): the raw SQL silently failed on every call since Oct 1 — an
  uninterpolated `{settings.DB_SCHEMA}` placeholder plus text[]-only array
  operators (`&`, `|`) that PostgreSQL does not define for json or any array
  type — so analyses never auto-linked into topic chains and each run logged
  `ProgrammingError` noise. Fix: expand the json column into a text[]
  per row (`json_array_elements_text` with a `json_typeof` array guard),
  compute the Jaccard ratio via `unnest` INTERSECT/UNION counts, keep only the
  provider-valid overlap operator `&&`, cast array parameters `::text[]`.
  Verified read-only on live data: record 1131 self-match similarity 1.0,
  record 1132 found at 0.67 for the live prompt theme set; 77 no-DB tests
  pass via `pytest --noconftest` (new `tests/test_theme_matcher_sql.py` pins
  the SQL contract); full DB pytest scope still blocked by the shared
  test-schema sync refusal (separate issue, R8). Evidence: fix diff in
  `app/services/ai/theme_matcher.py`, SQL probe outputs vs live workspace;
  depends: none; owns: app/services/ai/theme_matcher.py,
  tests/test_theme_matcher_sql.py; owner: $local (opencode); status: READY

- [ ] P5-T07 — analysis runs to completion for text-only scenarios: staged
  attachments without a usable URL are not media for analysis (collector
  found none — VK raw-content audit Q-2), so a post retires on its text
  coverage instead of being withheld forever; a media modality without a
  configured model is ignored rather than a coverage gap; the text stage
  walks a bounded queue of active capable models when the scenario model
  fails (failover verified live: GLM/River refused → Qwen/LLM Tech
  succeeded); a fuller same-fingerprint run merges its coverage hashes into
  the existing daily partial row instead of being dropped, so staged records
  retire and the run stops burning attempts; source page shows one merged
  backlog card with a single action button. Evidence: live CLI runs —
  raw_processed 100/100 then backlog window drained to 0 (`in_window: 0`);
  debug-traced failover path (`text_analysis_attempt_unusable` → completed
  via fallback); new no-DB tests
  `tests/test_media_coverage_semantics.py` (86 total pass via
  `pytest --noconftest`); depends: P5-T04, P8-T22; owns:
  app/services/ai/content_classifier.py, app/services/ai/analyzer.py,
  app/models/managers/llm_model_manager.py,
  app/models/managers/ai_analytics_manager.py, app/services/ai/theme_matcher.py,
  app/web/templates/web/source_detail.html,
  tests/test_media_coverage_semantics.py, tests/test_theme_matcher_sql.py;
  owner: $local (opencode); status: READY

- [ ] P5-T08 — **task selection scope; `monitored_users` removed end to end.**
  The task, not a source and not a scenario, decides which collected items are
  analysed. Design: `docs/design/task-selection.md`. Selection is applied in
  order — sources (`agent_task_sources`) → `payload.tracked_users`
  (`platform:external_id`, author filter only) → text scope
  (`keywords_list` + `keywords_match`, `topics_list`) — before the provider
  call, in the staged `analyze` path and in every inline path. Content types
  come from the scenario only: without `tracked_users` the source's own items
  are analysed, with `tracked_users` only those authors' items of the same
  content types — selection never widens or narrows `content_types`.

  Staged execution plan (one task, one PR, commits in this order):

  1. **Pure selection core** (`app/services/monitoring/selection.py` new):
     `parse_tracked_users` / normalise to `platform:external_id`, reject
     malformed entries, `select_items(items, scope) -> (eligible, reasons)` with
     per-layer counters (`no_source`, `author_not_tracked`, `no_text_match`,
     `unknown_tracked_user`), phrase matching (word-bounded, case-insensitive,
     `ё`=`е`, `any`/`all`). Unit tests only, no DB, no platform.
  2. **Task payload contract** (`AgentTaskManager.build_selection_payload` /
     `parse_selection`): one writer/reader used by web, CLI, chat and API so the
     same JSON is produced everywhere; `monitored_users` is rejected with a
     named error (not silently ignored). `analysis_context` fingerprint:
     `tracked_users` and the text scope become semantic keys (different people
     ⇒ different analysis context), the old `monitored_users` exclusion goes.
  3. **Runtime**: `handle_collect` loses the `monitored_users` branch and calls
     `collect_monitored_users` nowhere; `handle_analyze` runs `select_items`
     over the staged batch per source and passes only eligible items to
     `AIAnalyzer`, reporting selection counters in job progress/result.
     Delete `ContentCollector.collect_monitored_users`, `Source.params`
     fallback, and the API route `POST /monitoring/collect/monitored` with its
     schema. Behaviour check: a source's own wall is never replaced by a
     person's wall.
  4. **Teardown**: remove `monitored_users` from `Source.params` readers/writers,
     from `analysis_context._OPERATIONAL_KEYS`, and from reporting fallbacks.
     Alembic migration (new revision): strip `monitored_users` from
     `agent_tasks.payload` and `sources.params`; do not fabricate
     `tracked_users` — the pre-production note in the design doc governs the
     one-off data decision. Downgrade restores nothing (documented).
  5. **Surfaces in this order** (each one commit, each with focused tests):
     - CLI: `--tracked` and `--keywords` replace `--monitored` in `task add`,
       `task update`, `collect` and one-off `task <job>`;
     - API: task schemas gain `tracked_users`/text scope, the monitored route
       is gone (`docs/API.md`);
     - chat tools: `task_add`/`task_update` take `tracked_users`,
       `keywords_match`; the prompt section explains the three layers and
       forbids inventing identities;
     - web UI: task editor/detail/list — one "Кого отслеживаем" field
       (platform + id), one "Ключевые слова" field with any/all, source form
       loses "Отслеживаемые пользователи" entirely (no legacy hint);
     - admin: sqladmin help text for `agent_tasks.payload` and `sources.params`,
       source form params help, and the audit doc reference.

  Accept: no reference to `monitored_users` outside the design doc's history
  section and tests that assert its removal; a mocked run proves a tracked
  author's item reaches the provider and another author's item does not; the
  same task produces the same payload through web, CLI and chat; job
  diagnostics show the selection split; docs (`task-selection`,
  `COLLECTION`, `AGENT_TASKS`, `CLI`, `API`, `DIGEST`,
  `ANALYTICS_AGGREGATION_SYSTEM`) describe the shipped behavior. Depends:
  P5-T05 (analyzer/handler files), P8-T22. Owns: app/services/monitoring/,
  app/services/social/, app/jobs/handlers.py, app/services/ai/analysis_context.py,
  app/models/managers/agent_task_manager.py, app/web/tasks.py,
  app/web/sources.py, templates, app/agent/toolset/tasks.py, cli/, app/api,
  app/admin/views.py, alembic revision, tests. Owner: unassigned; status: TODO

- [ ] P5-T12 — VK repost author snapshot: `wall.get` must request
  `copy_history` (and `extended=1` with the needed `fields`) so each stored item
  carries its carrier (`collected_items.author` = `{id, name, platform}` from
  `from_id` low 31 bits / `signer_id`) and a deterministic `external_id` per
  carrier (`{carrier}_{wall_id}_{post_id}`). Without it `tracked_users` cannot
  match anyone on a community that only reposts. Keep one staged identity per
  carrier (a second carrier stages its own row), and repeat reposts must not
  duplicate analysis. Accept: fixture with two carriers of one repost yields two
  row identities and no duplicate analytics; malformed `copy_history` cannot
  fabricate an author; a tracked author's repost is selected while an untracked
  one is not. Depends: P5-T08. Owns: app/services/social/vk_client.py,
  app/models/managers/collected_item_manager.py, tests. Owner: unassigned;
  status: TODO

- [ ] P5-T13 — VK search sources and safe collection bounds: add
  `SourceType.SEARCH` (`newsfeed.search` with `q`, date window and optional
  `owner_ids`), `groups.getById` screen-name resolution (since
  `utils.resolveScreenName` answers 1051), author/permalink snapshots on every
  VK item, inflection-aware text scope (`utils/ru_stem.py`), a per-source
  1000-row ceiling (`SOCIAL_COLLECT_LIMIT`), `--dry-on`/task `dry_run`, and the
  agent `sources_search` tool. Remove the retired `excluded_users`,
  `blacklist` and `whitelist` from algorithms, forms, CLI and chat tools.
  Accept: a SEARCH source builds `newsfeed.search` params with the date window
  and owner filter; wall collection drops out-of-window items (VK ignores
  `wall.get` dates); «Италия» selects «Италию»/«Италии» but not «дизайнер» for
  «дизайн»; a dry run writes no `collected_items`, calls no model and leaves
  `last_checked` unchanged; more than 1000 rows per source is impossible;
  web/CLI/chat expose the same payload without the removed fields. Live VK
  evidence: task 2027 dry-run on sources 3145/3160 (see
  `docs/audits/2026-10-20-vk-search-and-task-2027.md`). Depends: P5-T08.
  Owns: `app/services/social/`, `app/services/monitoring/`, `app/jobs/handlers.py`,
  `app/web/tasks.py`, templates, `app/agent/toolset/`, `cli/`,
  `app/models/scheduling/agent_task.py`, migration 0106, tests. Owner:
  $local (opencode); status: READY.
  Evidence: implementation `app/services/social/vk_client.py`
  (`SEARCH`/`newsfeed.search`, `groups.search`, `users.search`,
  `database.getCities`, `groups.getById` resolution, author/permalink,
  `owner_ids`), `app/services/social/base.py` (1000-row ceiling, client-side
  date window), `app/services/monitoring/collector.py` (`dry_run` + preview),
  `app/services/monitoring/selection.py` + `app/utils/ru_stem.py`,
  `app/jobs/handlers.py`, `app/web/tasks.py` + templates, `cli/`,
  `app/agent/toolset/sources.py` (`sources_search`, SEARCH `source_add`),
  migration `0106`. Tests: `tests/test_vk_search.py` (19),
  `test_collect_limit.py` (5), `TestStemmedWordForms` (6),
  `test_cli_selection_flags.py`, `test_cli_collect.py`,
  `test_cli_username_parsing.py` (101 no-DB), DB suites
  `test_handle_collect*`, `test_handle_analyze_selection`,
  `test_web_task_*`, `test_admin_task_form`, `test_admin_task_run_now`,
  `test_api_task_selection`, `test_analysis_fingerprint_selection`,
  `test_collected_items` (all green; 6 pre-existing `test_task_activity`
  failures and loop-bound DB tests are unrelated). Live: task 2027 dry-run
  (4 sources, 11 items fetched, 0 rows written, 0 LLM), `wall.get` date window
  verified, VK search methods return 1051 (Q-5). Audit:
  `docs/audits/2026-10-20-vk-search-and-task-2027.md`. UI follow-up: the
  `run_now` button no longer loses its value to `x-bind:disabled` (a click now
  queues the job), the admin diagnostics lists every source with its outcome,
  counts and a bounded platform code (`api_1051`), a finished run removes the
  stale «выполняется» badge on the next poll, and a dry run gets its own
  «Тестовый прогон» label/note plus a selection preview («прошли фильтр N из
  M») over the full fetched batch, without staging or calling a model.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
