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
  of scope. Code and migration 0104 are prepared; focused isolated DB validation
  remains pending, and public migration application requires separate owner
  approval after review. Do not record test or migration-application success
  until performed. Evidence: implementation on dev SHA
  `3e5779460aa9cbb277a5b5c8c0a7ab8e27b4c526`; depends: none; owns: analysis
  receipt design/implementation and focused tests; owner: codex-analysis-receipts;
  status: IN_PROGRESS.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
