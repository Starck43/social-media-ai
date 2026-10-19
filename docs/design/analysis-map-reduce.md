# Map-reduce analysis pipeline (bounded output for any model)

Design for P5-T05 ([phase file](../phases/p5-ai-boundaries.md)). Problem,
verified evidence and acceptance live in the phase task; this doc is the
implementation guidance. It follows PRD-07 invariants: typed output cannot gain
authority, partial coverage is explicit, failed outputs stay persisted safely.

## Problem

The current text stage sends every selected raw item of one run in a single
request (`AIAnalyzer._analyze_text` → one `client.analyze` call) and expects one
JSON document satisfying the scenario output contract for the whole batch.

- Required output size grows with the number and length of collected items,
  while the model output cap is a hard, asymmetrically small resource. Verified
  incident: job 1751 / task 1582 / source 2939 — prompt 5106 tokens, answer
  truncated at exactly 2048 completion tokens (`zai-org/GLM-5.3-Flash` on
  River, `max_tokens=4096` configured), one batch of 17 items, zero results.
- A truncated response and a chat-style refusal are indistinguishable
  afterwards: both collapse to `outcome=failed_output` with no `finish_reason`
  recorded in the `llm_attempts` journal.
- One failed call zeroes the batch (`parsed={}` → `_validate_stage_result`
  error), even though retire-by-completed-hash already supports partial drains
  and keeps unanalysed rows staged.

## Goal

Any text model (whatever its output cap) can process any staged volume, so a
run's completeness is limited only by the configured budget, not by the model's
output ceiling. Cost, accounting and dedup invariants stay intact.

Non-goals: changing collectors, VK/media pipelines (map/redefine media stages
follows the same split later), digest/learn/reflect, scenario editing UX.

## Architecture

Two stages, same entry points (`AIAnalyzer.analyze_content`,
`handle_analyze`), LLM calls only via the existing clients and DB-managed
model config, per-usage accounting preserved end to end.

### Map — per-item extraction with a fixed small output

- Extract per item: `{content_hash, sentiment_score, sentiment_label,
  keywords[], topics[], entities[], summary}` plus task-relevant target fields
  fed from `task_payload` (brands/hashtags stay prompt-side, never in schema).
- The map contract is static, compact and item-independent: `maxLength` on
  strings (supported by `scenario_schema`), no free-text repetition of post
  content. One item → a few hundred output tokens, so any capped model works.
- Items are packed into map requests by **input** token budget (tails of long
  posts trimmed by the existing text content preparation); each item's verdict
  is validated individually, so one malformed item is retried or dropped by the
  give-up ceiling instead of sinking the batch. Reported failures count per
  item and drive the existing `reported_errors`/`_count_failed_staged` paths.
- Refusals keep the existing treatment ("недостаточно данных" → no results,
  batch stays staged) but at item granularity.

### Reduce — bounded aggregation into the scenario contract

- Reduce consumes compact map rows (hash references), never raw posts, so its
  input stays small regardless of collected volume. It clusters rows into
  themes (reusing the topic-chain resolution), builds the event-level summary
  and emits the scenario's output contract (validated exactly as today, with
  the same `output_contract.model`).
- Reduce output is bounded by schema limits, not by raw content volume.
- Map rows are persisted as an internal envelope of the produced
  `AIAnalytics` (same pattern as the private `_submitted_text_hashes`
  coverage evidence), so a rerun/audit can reuse rows without re-paying the
  map stage for successfully covered hashes (P5-T04 receipts remain the
  duplicate-spend gate for the whole pipeline).

### Run-level drain and budgets

- `handle_analyze` loops the staged queue in chunks pulled by
  `ANALYZE_STAGE_BATCH` until the window queue is empty or the run budget is
  exhausted; the ledger already summarises per-source counts.
- Budget = remaining `max_tokens`-based estimate driven by measured journal
  numbers, admission-clamped (personal-billing `__personal_max_output_tokens`
  is already visible in the client and is honored when sizing), plus explicit
  per-call and per-run ceilings in settings.
- Truncation-triggered descent: map/reduce requests suspected truncated
  (`finish_reason=length` or schema failure) are retried with a smaller pack;
  per-item map degrades to single-item calls; reduce failure of one cluster
  keeps sibling clusters' results.

### Visibility

- `llm_attempts` gains bounded, non-secret envelope fields: requested vs
  effective max tokens, `finish_reason` classed as
  `succeeded|length|content_filter|stop|unknown` where the provider reports
  one; unknown stays unknown, never coerced. Diagnostics pages use these for
  the failed-run "why".
- Job progress (`read_snapshot`) keeps growing per-item `raw_processed`
  across chunks, so the run-now modal and source page show real coverage
  («N из M») instead of 0% until a lone final response.

## Key decisions

- **Split by failure mode, not by fashion.** The failure we verified is output
  truncation; map makes output item-bounded, reduce keeps aggregation typed.
- **Reuse the hash-based ledger.** Retire-by-completed-hash, receipts (P5-T04)
  and attempt ceilings already implement partial success; the pipeline only
  stops wasting them on whole-batch failure.
- **No new transport or framework.** Same clients, same journal, same job
  infra; no Redis/Celery in the active path.
- **Budgeted, not unbounded.** A drain loop without a per-run cap would turn
  650 staged rows into an unpriced sprint; budgets keep dry-run safety and
  cost discipline.

## Incidents this changes

- Task 1582 with the same model: 17 staged items → 17 map calls capped at
  2048 tokens each (fits easily) → reduce over 17 compact rows → results, or
  a partial result with unused rows staged — never a silent all-or-nothing.
- A chat-style or refusing model burns single-item attempts up to the give-up
  ceiling instead of poisoning the whole source's run.

## Increments

1. Journal envelope: finish-reason class + effective/requested tokens;
   diagnostics on the source page read it (no behavior change).
2. Map stage behind a payload flag, parallel to `_analyze_text`; selected by
   new tasks, legacy path untouched. Unit tests with mocked clients (no real
   LLM calls, R10).
3. Drain loop + budgets in `handle_analyze`; per-source progress wiring.
4. Reduce aggregation into the existing `_save_analysis` path with bounded
   schemas; disabled legacy path; modal/coverage nothing new to render.

Ordering constraint: P5-T04 owns the receipt/dedup layer and P8-T22 owns
handler/web work right now — implementation slices rebase on their landing;
the design doc and journal slice have no file conflict.

## Risks

- More calls per item (N+1 shape) — mitigated by input-budget packing, per-run
  ceilings, rate limiter, and journal-driven cost visibility.
- Map rows are derived content — keep them out of user-facing raw views and
  retention pruning rules (they are internal envelopes, pruned with their
  analytics).
- Prompt-injection surface unchanged (same texts reach the model); strict
  schema validation unchanged; no authority gained by map rows.
