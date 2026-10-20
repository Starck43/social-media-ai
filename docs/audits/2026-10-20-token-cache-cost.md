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

Integration with fresh dev and acceptance at the resulting revision remain pending.
