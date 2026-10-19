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

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
