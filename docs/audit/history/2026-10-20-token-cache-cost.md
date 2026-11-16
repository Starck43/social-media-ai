# Token cost, cache and daily-limit audit

Date: 2026-10-20. Branch: `audit/token-cache-cost`.
Reviewed code: `de9fe319fd951d959c890da3be7b0cb50ed8e6c1` (local dev).
Scope: static code audit and official provider documentation. No runtime code,
DB, provider requests, pytest, migrations, commits or integration changes.
The active dev checkout has concurrent OpenCode edits, excluded from this audit.

## Verdict

Cache-aware exact accounting is possible when the provider reports a documented
final charge, or returns every billable usage category with matching tariffs.
The current tariff fallback cannot provide that guarantee. Some paths overstate
cost, others understate it; `priced` currently does not establish invoice accuracy.
Provider totals already improve the new ledger, but do not repair old projections.

## Confirmed findings

1. **High: cache usage disappears from accounting normalization.**
   `app/services/ai/llm_client.py:308-323` preserves only prompt/completion counts;
   Anthropic maps `input_tokens` directly to prompt tokens at :690-714.
   `attempt_journal.py:355-385` and `outcome_events.py:88-100` apply two flat rates.
   OpenAI-style aggregate input includes cached input, so discounted reads are
   overcharged by the fallback. Anthropic input excludes cache reads/writes, so
   ignoring them omits actual charges. A missing cache field must not universally
   mean zero: that depends on the provider's documented usage contract.

2. **High: provider total and displayed/projected spend can disagree.**
   `attempt_journal.py:105-143,360-367` accepts structured total USD before tariffs;
   `outcome_events.py:66-100` likewise honors the client-owned provider amount.
   However `llm_client.py:190-203,280-292` recomputes legacy usage.cost from model
   tariffs, ignoring the reported total; unknown tariffs contribute zero.
   `analyzer.py:1179-1207,1604-1615` separately prices analytics in cents.
   `app/services/tenancy/resolver.py:117-128` sums message/digest/job projections
   for the workspace daily cap. Consequently a correct personal settlement can
   coexist with an incorrect workspace cap or analytics value. This contradicts
   any interpretation of IMPLEMENTATION_STATUS's journal completion as complete
   end-to-end accurate spend; that document explicitly keeps the whole gate open.

3. **High: old journal tariff identity is ambiguous across providers.**
   `attempt_journal.py:389-413` selects by string model_id + active only, despite
   provider-specific models. Wrong-provider rates, including explicit free rates,
   can price an attempt. The personal receipt path additionally checks model and
   provider identity; do not generalize this defect to every billing path.

4. **High: reservation safety depends on a complete reviewed price envelope.**
   `personal_llm_billing_manager.py:55-89` reserves the context/output envelope
   at max(input rate, output rate). This is conservative for discounted cache
   reads, but is not proven for every cache-write surcharge, service tier,
   context tier or extra fee. A cache write rate above both stored rates is a
   counterexample to a universal upper-bound claim. Settlement :658-715 releases
   reservations for priced/free events, retains unknown obligations, and freezes
   on detected overruns. Missing cache charges labeled priced evade that unknown
   safeguard. An actual concurrent cap bypass was not exercised in this audit.

5. **Attribution: amount corruption propagates without proving identity leakage.**
   Personal admission binds tenant, payer, task and initiator
   (`personal_llm_billing_manager.py:121-281`; `services/billing/payer.py`).
   No cross-user/workspace disclosure or reassignment defect was established.
   Correct identity bindings still receive incorrect amounts from the pricing
   paths above. Reports must aggregate the same immutable dispatch costs by payer
   and workspace, distinguishing initiator from fixed task payer and avoiding
   double counting ledger rows plus projections.

## Documented calculation approach

Use this precedence per dispatched attempt:

1. Documented final provider/account charge in a known currency, including explicit
   zero; retain its provenance and request identity. A provider-specific adapter
   must establish units and semantics. Do not universally interpret arbitrary
   `cost` fields as USD or substitute upstream cost for the customer's charge.
2. Otherwise compute Decimal cost from disjoint billable token categories and a
   dispatch-time, provider/model-specific tariff snapshot:

       cost = (ordinary_input * input_rate + cache_read * read_rate
               + cache_write_short * short_write_rate
               + cache_write_long * long_write_rate
               + output * output_rate) / rate_unit

   Rates may be per 1K or 1M; normalize explicitly. Include all applicable tier,
   mode, media, tool and other fees or classify the result as incomplete.
   OpenAI-compatible total input generally needs subtraction of cache categories;
   Anthropic input is already the ordinary category. Never subtract twice or add
   reasoning tokens again when already included in billed output.
3. Missing applicable prices, usage, currency semantics or billable categories
   means unknown/incomplete, never zero. A conservative upper bound is a budget
   reservation, not actual expenditure. Reconcile with provider evidence later.

Illustrative synthetic arithmetic (USD per 1K, not advertised model prices):
OpenAI-style input=1000, read=800, output=100, rates input=1/read=0.1/output=2:
old fallback=1.20; full calculation=0.48. Anthropic ordinary input=200,
read=800, write=100, output=100, write rate=1.25: fallback=0.40 versus 0.605.
These are formula examples, not executed application tests.

Official sources consulted on 2026-10-20:

- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching):
  usage category fields and ordinary-input subtraction; model-specific cache
  behavior/rates require versioned configuration, not universal multipliers.
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching):
  ordinary input, cache read and cache creation are separate; write TTL changes
  pricing. Rate multipliers differ by model; consult the applicable tariff.
- [OpenRouter official usage accounting source](https://github.com/OpenRouterTeam/docs/blob/main/cookbook/administration/usage-accounting.mdx):
  usage.cost is the account charge; upstream_inference_cost is distinct; cache
  read/write details and retrieval by generation ID support reconciliation.

## Bounded correction plan, in dependency order

1. Define provider-specific normalized usage, final-charge provenance and pricing
   completeness; preserve cache fields and distinguish missing from explicit zero.
2. Extend model/receipt tariff snapshots with applicable cache rates and pricing
   version/tier/mode. Bind journal lookup to provider and exact model row. This
   likely requires new Alembic migrations; no existing migration should be edited.
3. Make one canonical attempt-cost result feed settlement and message/digest/job/
   analytics projections with explicit USD/cents conversion. Preserve idempotency,
   fixed payer and tenant scoping; never sum projections with the ledger.
4. Revalidate reservation bounds for all enabled billable categories; unknown
   bounds deny capped dispatches. Reconcile unknown outcomes without releasing
   obligations or retroactively claiming unverified historical precision.
5. Present separate confirmed/reconstructed/estimated/unknown cost states plus
   reserved amount. Until fixed, label old tariff-based figures as estimates
   without cache accounting; do not advertise strict invoice accuracy or a
   universally guaranteed monetary cap. Historical repair requires sufficient
   retained raw usage or provider request evidence; otherwise mark incomplete.
6. Delegate focused mocked checks: cache hit/miss/write/TTL, authoritative zero
   and total, malformed/missing details, provider identity collisions, rate-unit
   conversion, projection parity, payer/workspace totals, idempotent settlement,
   concurrent cap admission and unknown obligations. PostgreSQL scopes sequential
   via OpenCode; wait for the active dev pytest owner. No live paid calls needed.

## Validation and integration limits

Code paths and official contracts reviewed; arithmetic examples independently
follow the documented formulas. No pytest or live invoice reconciliation run.
No production accuracy certification. Findings are ready for correction scoping;
implementation is not part of this audit. Shared registry/phase edits are scoped
to the audit task; reconcile with fresh dev before any future publish/merge.

## Implementation follow-up: provider totals in projections

P2-T11, branch `audit/token-cache-cost`, based on audit commit `5146696`:
resolved structured provider USD totals now take precedence in the client usage
projections consumed by chat, digest and learning, and in analyzer modality cost
projections. Explicit provider zero must be retained, including in analytics
storage when every included stage reports zero. Missing token counts or a missing
model row must not discard an otherwise resolved provider amount. The existing
provider-cost parser and its currency/precision checks remain the source of this
price evidence; generated text and legacy normalized projection cost are excluded.

This is a bounded correction of finding 2, not whole billing acceptance. Analytics
still excludes the separate unified-summary attempt from its modality aggregate;
the journal records that attempt separately. Without a resolved provider report,
legacy tariff estimates still ignore cache categories, and unknown values can
still contribute zero in old projections. Full usage/tariff snapshots, provider
identity binding, conservative reservation bounds and honest incomplete-spend
reporting remain open as P2-T12/P2-T13/P2-T14. No migration is included or applied.

Validation retry (2026-10-20): the OpenCode exact-model route recovered.
The first focused run had 94 passed / 10 failed. Pristine baseline `de9fe31`
reproduced 8 of those failures in obsolete journal fixtures (11 passed / 8 failed,
19 collected). The new transport fixtures also needed the real provider's
`auth_header` default. Fixtures now load the actual price-floor helper, provide
active scenarios and existing coverage/body data, and assert that partial updates
preserve prior body/coverage rather than suppressing the current update contract.

Repairing the AST fixture also exposed a source-confirmed duplicate
`kwargs.pop("accounting_purpose", "chat")`: the second pop discarded explicit
`learn` labels. The duplicate is removed; the existing strict forwarding/journal
purpose assertions remain intact. These are bounded accounting and harness
corrections, not changes to payer identity, tariff selection or reservation policy.

Final audit-worktree TEST RUN PASS via OpenCode build,
`QwenPlan/deepseek-v4-flash-0731`: collected 104; executed 104 passed,
0 failed/skipped/errors, 43 subtests; collection and execution exit 0.
Base `51466965fd80c37344f361c9bd1acb828f5e6ce3` plus prepared source digest below.
No DB or live provider calls. Independent code review and `git diff --check` passed.

Exact positional scope, collection (`--collect-only`) then execution:
`tests/test_llm_usage_cost.py`, `tests/test_analyzer_provider_cost.py`,
`tests/test_analyzer_pricing.py`, `tests/test_provider_accounting_price.py`,
`tests/test_scoped_attempt_journal.py`, `tests/test_analyzer_reported_errors.py`,
`tests/test_analyzer_attempt_journal.py`; flags
`--noconftest -o addopts= -q --tb=short --no-header`, worktree `.venv/bin/python -m pytest`.
Transport/admission/observation/model lookups/storage are local doubles; required
configuration is synthetic, without private environment or paid calls.

Prepared source reference: combined SHA-256 of the seven changed Python files
(sorted paths, each encoded path + NUL + file bytes + NUL):
`6105f007cafc2d460859e921a25fa58429dd0b5eb59de5f61e56688a7b91cbe2`.
Files: `app/services/ai/llm_client.py`, `app/services/ai/analyzer.py`,
`tests/test_llm_usage_cost.py`, `tests/test_analyzer_provider_cost.py`,
`tests/test_analyzer_reported_errors.py`, `tests/test_scoped_attempt_journal.py`,
`tests/test_analyzer_attempt_journal.py`.

Coordinator integration acceptance: candidate `b4f6d39` merged with dev
`adf1d6f`. OpenCode repeated collection and execution on the merged index:
104 collected, 104 passed, 0 failed/skipped, 43 subtests, exit 0.
Merged seven-file source digest:
`0ab5a38d35640fc9682bf89d0c594f2b2a935ebfc57d3031e247aec6c8abe9ab`.
Scoped diff reviewed; P2-T11 accepted as DONE. P2-T12 through P2-T14 remain
open: this acceptance does not certify complete cache pricing or daily caps.

## Model price units and cache tariff follow-up

The owner confirmed model 10's stored input/output amounts `0.12` / `0.4`
are USD per million tokens, with cache-read `0.024` in the same unit. Preserve
those raw amounts; changing the price unit must never rescale them. Existing
unclassified models retain their legacy unit until explicitly reviewed.

The prepared follow-up uses explicit model units and immutable versioned
snapshots of ordinary and cache-read/write rates. The provider's structured
USD amount remains authoritative. Otherwise, cache categories must be disjoint:
OpenAI-compatible inclusive input minus reported cache categories, or Anthropic
ordinary input plus separate read and TTL write buckets. References:
[OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching),
[Anthropic cache token categories](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-caching.md).
An API format label alone does not prove a compatible provider implements every
counter: absent evidence stays unknown, including missing write counters or TTLs.

For one million inclusive input tokens, 800,000 cache reads, explicit zero
writes and 100,000 output tokens, the confirmed rates yield USD `0.0832`.
This arithmetic does not certify complete daily caps or attribution; those
remain separately tracked. Historical v1 amounts are not reinterpreted.

Migration 0107 preserves ordinary price amounts. Its local application and
model 10 configuration are recorded below under owner-authorized activation.

### Follow-up validation

Candidate: `feat/cache-read-tariff`, base `5401dad2f5375e79fd71277d1dafed8e03e4cfe3`.
Coordinator reviewed the scoped source changes and migration definition.
The primary OpenCode exact-model route failed initialization twice with
`Instruction initialization blocked by unavailable sources: core/instructions`;
Kilo was unavailable. The owner-authorized Codex test subagent fallback executed
the following no-DB scopes, each collected and run in a fresh Python process
with `--noconftest -o addopts= -q --tb=short --no-header` and synthetic settings.
Foreign pytest processes were allowed to finish before execution.

| Test file (`tests/`) | Passed |
| --- | ---: |
| `test_cache_pricing.py` | 12 |
| `test_model_cache_tariffs.py` | 8 |
| `test_cache_reservation_bound.py` | 9 |
| `test_llm_usage_cost.py` | 17 |
| `test_llm_attempt_journal.py` | 26 |
| `test_llm_dispatch_receipts.py` | 35 |
| `test_llm_outcome_events.py` | 46 |
| `test_scoped_attempt_journal.py` | 15 |
| `test_personal_billing_transport.py` | 8 |
| `test_analyzer_provider_cost.py` | 6 |
| `test_analyzer_pricing.py` | 6 |
| `test_provider_accounting_price.py` | 25 |
| `test_analyzer_reported_errors.py` | 34 |
| `test_analyzer_attempt_journal.py` | 4 |
| **Total** | **250** |

Final matrix: 249 passed / one stale SQL-shape assertion failed. After correcting
only that assertion to require explicit units for receipt/personal dispatch,
the affected eight-test file collected and passed again; the other 13 scopes
were unchanged. Combined result: 250 passed, zero outstanding failures/skips.
The final sorted-path + NUL + file bytes + NUL SHA-256 over 35 changed/new
Python files, including migration 0107, is
`dc4dae98967782d44c5250a0765d3233fdd942262c1a5133bb9e64431e240768`.
AST parsing and `git diff --check` passed. Captured DDL and fake session checks
are not PostgreSQL persistence or browser acceptance. At that validation point,
no migration, price data
write, paid provider request, commit or feature-to-dev merge was performed.
The OpenCode committer availability retry remained blocked by the same error.
Activation and full cap/report acceptance remain pending.

### Owner-authorized local activation

The owner subsequently authorized applying the migration and committing before
the remaining tests, including the requested Codex worker committer fallback
while OpenCode initialization remains blocked. No new tests were run in this
activation step.

Read-only preflight confirmed localhost:5432/social_manager, public at revision
0106, no active transactions or DDL locks. A private public-schema pg_dump archive
was verified with pg_restore list (637 entries, file mode 0600) before changing
the schema. Alembic applied 0107 successfully; revision and expected columns
were verified afterward. The backup is retained locally pending final checks.

The existing ORM model manager then updated only model 10's pricing unit to
1000000 and its cache-read price to Decimal 0.024. Before/after checks confirmed
the raw input/output amounts remain 0.12 and 0.4. Cache-write prices were not
assumed or populated.

Initially, read-only catalog inspection found no `alembic_version` table and
none of the new unit/cache columns. After explicit owner authorization, a
verified private backup was made and only `test_schema` was restored from its
actual pre-0093 baseline (0092), then advanced through the migration chain to
0108. The historical baseline was verified in a detached worktree; temporary
compatibility adjustments were confined to an external script/copy after the
old SQLAlchemy enum constructor and psycopg bind inference blocked stock
bootstrap/migration execution. No tracked migration was changed. The immutable
receipt guards were verified at 0108; `public` remained at 0107. The PostgreSQL
acceptance run for P2-T14 passed. Browser acceptance was not run: the local app
had no listener on port 8000. P2-T12 persistence/replay and UI checks remain
open.

### P2-T14 spend evidence and acceptance

At commit `626176328c04ac90d7c18f95dd0ed38d571e7f88`, finite personal caps
reject dispatches without a safe bound, unresolved open obligations remain
held, and the payer/day report separates provider-reported, receipt-reconstructed,
reserved, and unknown values. Aggregation uses canonical dispatch receipts and
is scoped by payer and workspace; legacy estimates are not presented as exact
spend. Validation: `tests/test_personal_billing_ledger_db.py` 8 passed on
`test_schema` 0108, and four no-DB scopes passed 56 tests total. The DB run did
not mutate schema. OpenCode failed initialization and Kilo was unavailable, so
the authorized Codex test subagent ran these scopes. Safari/browser rendering
was not part of P2-T14's acceptance and was not tested. The task commit is
merged into local `dev`; pushing its feature branch failed over both SSH and
HTTPS due to GitHub connection errors.

Commit preparation removed one excess EOF blank line from cache_pricing.py;
there was no behavior change. The resulting 35-Python-file candidate digest is
`eb03fb47a9738da7e3bf1589523950e7b107a81a564197ea9f12b4a961eb1612`.
The earlier digest remains the exact reference for the completed 250 no-DB
tests at the candidate.

### Integrated no-DB regression run

At dev `e446c044a863d641b27714a4bffe8563ecbb91dd`, the Codex test subagent
ran the same 14 assigned cache/accounting files sequentially in fresh Python
processes after the primary OpenCode route failed initialization and Kilo was
unavailable. Each file was collected and executed with
`--noconftest -o addopts= -q --tb=short --no-header`. Result: 251 passed,
0 failed, 0 skipped, 183 subtests. No DB setup, migration, provider call,
source edit, or Git change occurred in the test run. This validates the
isolated arithmetic, model, receipt, journal and reservation unit/SQL-shape
contracts at the integrated code revision; it does not validate PostgreSQL
persistence or browser rendering. The required test schema is missing the
0107 columns and Alembic history, so no DB-backed acceptance is claimed.
