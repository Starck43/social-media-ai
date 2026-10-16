# AI pipeline, scenarios and prompts

This document owns the current analysis-methodology, prompt and typed-output
contract. API shapes are documented in [API](API.md); stored fields and relations
are documented in [MODELS](MODELS.md).

## Concepts

- `AgentScenario` defines an analysis methodology: content types, analysis types,
  scope, prompts, optional output schema and model preferences.
- `AgentTask` defines when that methodology runs and the sources, runtime payload,
  reaction and delivery targets for that run.
- A future multi-step business workflow is a separate concept. Do not encode its
  permissions, schedule or action state inside a scenario prompt.

Methodology belongs in `AgentScenario.scope`. Concrete run targets such as brands,
competitors or named people belong in `AgentTask.payload`. The parameter registry
in `app/services/ai/param_registry.py` enforces that separation.

## Analysis flow

1. Collection produces normalized text and attachment snapshots with source and
   workspace identity.
2. The analyzer selects eligible content and preserves explicit coverage gaps.
3. `PromptBuilder` builds a media prompt for text, image or video.
4. Model resolution uses an explicit scenario model or the shared strategy and
   model-level default rules. The selected model's provider supplies the client.
5. Provider output is parsed and validated against the compiled scenario contract.
6. A unified summary combines valid modality results.
7. The result is saved to `AIAnalytics` with source/date, structured payload,
   usage projection, analyzed media types, content hashes and optional topic chain.

Useful valid siblings may be saved when another modality is unavailable or
invalid. The row must then preserve incomplete coverage; missing media or an empty
model result must not certify the parent content hash as fully analyzed.

## Prompt assembly

`app/services/ai/prompts.py` owns prompt composition. It combines:

- system-owned context such as platform, media type, count and output instructions;
- the scenario's selected prompt (`text_prompt`, `image_prompt`, `video_prompt`,
  `audio_prompt` or `unified_summary_prompt`);
- scenario methodology variables from `scope`;
- concrete run variables from `AgentTask.payload`;
- structured-output instructions derived from the scenario contract.

Variable precedence is system-owned values, then task payload, then scenario scope.
Lower-trust values cannot replace system-owned instructions. Unknown placeholders
are reported as validation warnings, not errors: the scenario API and the agent's
`scenario_validate_prompt` tool name every `{variable}` outside the registry, the
prompt is still saved, and an unresolved placeholder stays literal at runtime.
Collected content, documents, messages and provider output are untrusted data, not
instructions that can grant tools or permissions. The unified-summary prompt frames
the text, image and video analysis results it receives as untrusted data, in both
the default template and a custom `summary_prompt` (including nested paths such as
`{image_analysis.mood}`). Substitution resolves placeholders in one pass, so text
inserted for one variable is never re-scanned for further placeholders.

Typical calls:

```python
PromptBuilder.get_prompt(
    MediaType.IMAGE,
    scenario=scenario,
    count=len(media_urls),
    platform_name=platform_name,
    task_payload=task_payload,
)

PromptBuilder.get_unified_summary_prompt(
    text_analysis,
    image_analysis,
    video_analysis,
    scenario=scenario,
    task_payload=task_payload,
)
```

## Structured output

`app/services/ai/json_schema_builder.py` compiles common fields plus the fields for
the selected `analysis_types`. Scope supplies methodology limits and vocabularies.
An explicit `AgentScenario.output_schema` is used when present; otherwise the
runtime derives the schema from `analysis_types` and `scope`.

Provider output is validated before persistence. Unknown analysis types are not
guessed. Empty typed objects, malformed media output and incompatible field types
remain validation failures. Validation does not authorize a write: tenant scope,
actor permissions and manager rules still apply at the operation boundary.

## Relevance and coverage

When `scope.relevance_filter` is enabled, the analyzer applies the configured
minimum confidence before saving the result. Submitted text sampling and media
availability are tracked separately from output validity:

- omitted eligible text remains available for later analysis;
- recognized but unavailable image/video attachments keep the parent incomplete;
- unknown or malformed attachments cannot create false coverage;
- cancellation or a failed save cannot retire hashes as completed work;
- a partial result cannot overwrite a stronger existing daily result merely to
  retain usage data.

## Staged raw-data progress contract

Progress surfaces in the source detail "Последние анализы" table and the task
"Выполнить сейчас" modal describe staged raw-data coverage, not the analysis
itself. The following invariants hold (`app/jobs/progress.py`,
`app/web/analysis_progress.py`, `app/models/managers/collected_item_manager.py`):

- The denominator (`raw_total`) is the fixed staged window for the source inside
  the task's content window (`count_for_source` with `start_date`/`end_date`). It
  includes rows that exhausted `give_up_after_attempts` and content beyond the
  per-run batch cap; it is stable across the run and never a live queue count.
- The numerator (`raw_processed`) counts only persisted, matched coverage:
  the number of selected **rows** (duplicates count) whose stored hash is in
  the set the analysis actually saved, capped by the rows actually deleted
  (`min(deleted, covered_selected_rows)` — the count is rows, not unique
  hashes). Unanalysed rows stay staged and are not counted; a partial result
  never implies the rest is done.
- `raw_selected` is how many rows fit in the batch this run, not a processed
  count. No in-flight percentage is invented: a percentage renders only when
  processed and total are known integers and `total > 0`; otherwise the UI shows
  "—" or "прогресс пока неизвестен". An empty window is "Нет данных", never 0%.
- An unknown heartbeat is distinct from stopped: a missing or stale (> 90 s)
  lock on a running job is "Статус не подтверждён" (`unconfirmed`), while
  "Остановлено" (`cancelled`) means explicit operator cancellation. Loss of the
  progress claim stops the handler at the next source boundary but is not shown
  as an unknown heartbeat. Progress is a display hint, not an outcome: the
  final `result` replaces it, and a failed progress write never fails the run.
- Privacy-safe diagnostics only: ordinary users see the source's own aggregate
  counts and the simple running/stopped state; per-object or cross-workspace
  failure detail stays on admin/superuser surfaces. This contract adds no
  migrations or batching changes.

## Scenario creation

Scenarios can be created through the existing admin, web/API or agent tools. Reuse
the presets in `app/services/ai/scenario_templates.py` and the builder in
`app/services/ai/scenario.py`. Validate enum values, scope keys, prompt variables
and model capability requirements before persistence.

Agent tool confirmation is a user-intent check, not authorization. The tool must
also enforce the current actor, workspace, permissions and normalized arguments at
execution time. Preferences saved in `scenario_prefs` may seed a later draft but
must not silently execute or grant access.

## Usage and cost boundary

### Completed analyzer journal foundation

Status: **DONE — integrated foundation, not acceptance of the whole PRD-03 gate**.
Journal `05bf353`, analyzer wiring `5953efb` and contract `9ebe945` are in dev.

- [x] Tenant-scoped `llm_attempts` model and recording manager, with migration
  definition 0092. This completion does not assert that a target database was migrated.
- [x] Capture returned analyzer-stage envelopes before the partial-result skip,
  relevance filter and no-meaningful-results return, independently of the daily row.
- [x] Snapshot the resolved model tariff and calculate Decimal USD at 1e-9 precision;
  distinguish `priced`, `free`, `unknown_tariff` and `unknown_usage`.
- [x] Add isolated regression coverage for identity/pricing/orchestration, preserved
  partial-save decisions, failed envelopes and redacted journal-write warnings.

The owner reports that local tests passed. The closeout report did not include
exact commands, counts or a tested SHA; none is inferred here. Remote completion
review was static and did not rerun Python/DB/provider tests or apply migrations.

The journal records captured analyzer stage envelopes through
`app/services/ai/attempt_journal.py`. Missing usage or an unresolved model tariff
keeps uncomputable actual cost NULL, rather than treating it as zero. A valid
structured provider USD report arbitrates actual cost, including an explicit
zero and regardless of whether the tariff/usage is available. Without a report,
two explicit zero table rates qualify as FREE even if token counts are missing;
non-zero rates require known counts only for their billable components.
New analyzer envelopes carry a client-owned `accounting_usage` block separately
from legacy numeric `usage` projections. Missing, non-integer, negative or
out-of-column-range token counts remain NULL evidence. The journal treats this
block as authoritative, even when malformed; it never recovers certainty from
projection zeros or provider-supplied replacement fields. OpenAI-compatible and
custom adapters read `prompt_tokens`/`completion_tokens`; Anthropic reads
`input_tokens`/`output_tokens`. Totals and response text do not imply missing
token fields. Legacy envelopes without this block retain their old usage fallback.

Missing, malformed, negative, non-finite or unrepresentable tariff rates produce
`undefined` and NULL cost without a valid provider report, rather than defaulting to zero or losing the
record through a Decimal exception. Valid snapshot fields and model identity
remain available when the other rate is unknown. Costs beyond the existing
numeric column range likewise remain NULL/unknown, not silently clamped.
Structured-output rejection stays `failed_output` even when usage is unknown.

**Provider-price implementation: local verification pending for this package.**
The analyze clients own `accounting_cost_usd` evidence extracted only from recognized
USD API cost fields (`usage.total_cost`, `usage.cost`, top-level totals, or complete
input/output cost components). An explicit zero is preserved; generated content,
parsed model text, caller keywords and numeric projection zeros are not reports.
Non-USD or malformed reports are not converted or guessed. A valid numeric
report with unresolved currency or only partial cost components stays
reported-but-unresolved, not a fallback to a free table. Valid totals take
precedence over component sums; no global model tariff is edited from a response.
An unrepresentable positive report retains a reported-but-unresolved marker;
it must not fall back to a free table rate. Extreme component exponents likewise
remain unresolved rather than underflowing into a fabricated zero. A malformed
legacy numeric projection tariff cannot discard the analyzer's paid response:
the numeric adapter keeps its zero fallback, while journal evidence is independent.

New monetary journal rows use `priced`, `free` or `undefined`; the two old constant
names remain import aliases, not new persisted labels. Provider reports arbitrate
before table pricing; a positive sub-precision report stays `priced` even if its
rounded USD amount is zero. Transient report evidence is removed before insertion
into the existing schema. No historical row is rewritten.

`LLMAttemptManager.accounting_totals()` supplies tenant-scoped captured-journal
statistics with known Decimal USD and separate priced/free/undefined counts.
Historical unknown labels and NULL actual costs remain visible as undefined;
a zero known subtotal never certifies that those calls were free. Optional UTC
filtering uses journal creation time, not a certified dispatch timestamp.
These statistics are a partial-journal read API, not a replacement cap, a UI
rollout, every-path capture or durable provider-report provenance.

Journal persistence suppresses only the named `uq_llm_attempts_key` constraint.
A duplicate is acknowledged only when its effective tenant and every persisted
evidence field match (NULL is not zero); changed usage, tariff, price or outcome
raises a redacted `AttemptEvidenceConflict`, never rewrites the row. Other
integrity, read and commit failures propagate. The owned transaction must commit
before the manager acknowledges an insert or exact replay. This is a persistence
contract, not PostgreSQL race evidence or a durable pre-HTTP admission receipt.
The analyzer's existing bounded write warning still applies: it is not a recovery
queue and must not be interpreted as successful durable accounting.

The existing USD/USD-cent projection adapters and chat paths remain compatible.
Known rates with missing billable counts still have undefined actual cost; the
known tariff snapshot must continue to govern reservation/reconciliation, not be
reclassified as an unreservable admission merely because usage is missing.
Source/job/task execution references are plain integers, not foreign keys, so
source, queue or transcript cleanup does not cascade through those references.
Daily `ai_analytics`, `jobs.llm_cost`, `digest_runs.llm_cost` and model counters
remain mutable projections, not the ledger.

### Non-analyzer journal capture

Status: **DONE — journal/projection separation extended to every non-analyzer
caller; not admission or whole-gate acceptance**.

- [x] `chat_with_fallback` journals each provider try separately, including a
  failed try followed by a fallback success; every try gets a fresh local
  `dispatch_id`. Callers label purpose: `agent_chat`, `learn`, `reflect`,
  `prompt_suggest`; unlabelled callers record `chat`. The `accounting_purpose`
  keyword is consumed locally and is never sent to the provider.
- [x] The agent `llm_model_test` tool (`model_test`) and the digest summary
  (`digest`, stage `digest_summary`) record their direct client attempts.
- [x] Token evidence is read only from the raw provider usage fields
  (`prompt_tokens`/`completion_tokens`; Anthropic `input_tokens`/`output_tokens`),
  never from the chat numeric projection that defaults missing counts to zero.
  Pricing reuses the analyzer three-state rules: provider report arbitrates,
  explicit zero is FREE, missing price is UNDEFINED with NULL cost.
- [x] A try that raised after possible dispatch is `failed_transport` with
  undefined cost. Pre-dispatch refusals (no API key, custom model without
  endpoint) are not journaled as paid attempts. Cancellation is not journaled.
- [x] The payer is the ambient tenant passed explicitly on every row, so a
  bypass context cannot charge the bootstrap workspace; no tenant scope means
  no row and a redacted `attempt_journal_payer_missing` warning.
- [x] Journaling is best-effort: failures emit a redacted bounded warning and
  never change the chat/digest result. Chat return values, `AgentMessage.cost`,
  `digest_runs.llm_cost`, `jobs.llm_cost`, model counters and the cap metric
  (`daily_cost_today`) are unchanged projections; the journal is not summed into
  them, so nothing is counted twice.

No schema change: `purpose`/`stage` are existing free-text columns. Regression:
`tests/test_scoped_attempt_journal.py`. Admin connection tests have no trusted
tenant/operator payer and are not journaled.

### Remaining acceptance boundary

Fresh OpenAI-compatible, Anthropic and custom `analyze()` invocations allocate a
local UUID before their HTTP attempt. The same `request.dispatch_id` (32 lowercase
hex characters) is retained in returned success, timeout, HTTP-error and validation-
failure envelopes. It is metadata, not a generated provider payload field or an
idempotency header. Caller keywords and provider response fields cannot replace it.

For these envelopes, the journal hashes a versioned identity bound to the tenant,
purpose, stage and dispatch UUID. Separate invocations with identical model/usage
remain distinct; persistence retries of one envelope retain its key even if usage,
tariffs or projection/execution references change, but only identical persisted
evidence is accepted as a replay. Re-resolving a changed tariff is a conflict, not
permission to re-price history; dispatch-time snapshots remain to implement.
No new schema fields are added:
the UUID contributes to the existing `attempt_key`, rather than being a new column.
Malformed non-null IDs are rejected, not replaced with a freshly minted identity.

Envelopes missing an ID (including legacy workers) retain the original content-
derived key exactly. Their identical calls can still collapse; no historical
backfill or complete mixed-version accounting is claimed. The UUID is not durable
before the HTTP call and does not turn cancellation/process loss into a journal
receipt. Tariffs are resolved when journaling, not captured
at dispatch time. Capture occurs after returned stage envelopes, and a journal
write failure emits a bounded warning while analysis continues; durable recovery
from that failure is not implemented by this slice.

Admin connection-test capture (needs an operator payer), durable
dispatch receipts and timestamps, failure recovery, and atomic budget admission
with reservation settlement remain OPEN under
[business readiness](BUSINESS_PRODUCTION_READINESS.md#prd-03--complete-bounded-economics).
No whole-gate, target-database or production acceptance is claimed.

### Preparation-receipt storage foundation (not activated)

The new `LLMDispatchReceipt` model and migration **definition** 0093 describe an
immutable preparation receipt. No client or caller is connected to this storage
boundary; the definition is not permission to apply a migration or activate it.
The existing analyzer journal and legacy projections remain unchanged.

`dispatch_receipts.prepare_dispatch_receipt()` takes its payer from explicit
trusted tenant scope, never caller/provider payload or implicit operator
bootstrap. It allocates a fresh raw UUID and freezes the selected DB model and
provider identities, purpose/stage, plain execution references, aware UTC
preparation time/day, pricing-policy version and representable Decimal tariff
rates. Missing/invalid rates stay NULL. `known`, `free` and `undefined` describe
the **tariff basis**, not actual provider price; a later structured provider
report remains the arbiter. Invalid raw tariff values are not preserved here.
No prompt, credentials, raw response, token estimate, actual cost or reservation
ceiling is stored by this foundation.

The manager acknowledges insertion or exact payer-bound persistence replay only
after owned commit. Other integrity/read/commit errors and evidence conflicts
propagate. A fresh preparation cannot treat a duplicate identity as permission
for another HTTP request. This API does **not** grant dispatch: future admission
must insert the receipt and reserve budget in the same owned transaction, then
acknowledge that transaction before any network call. No lock may span HTTP.

Schema-qualified migration definitions reject UPDATE, DELETE and TRUNCATE and
restrict payer deletion; execution/model deletion has no cascading foreign keys.
The manager refuses to store receipts without both enabled, schema-bound trigger
guards of the expected types/function, so metadata-only bootstrap fails closed.
These controls are not a privileged-DDL threat model. Metadata `create_all` is
not a substitute for the reviewed migration: it does not install the immutable
triggers. Automatic bootstrap/pytest schema fixtures remain prohibited without
separate approval. New receipts may block tenant deletion/offboarding; retaining
evidence and the offboarding policy require review before rollout.

Ordinary downgrade of 0093 is deliberately refused, even for an empty table,
instead of deleting preparation evidence. An evidence-preserving rollback/
retention plan must be reviewed before schema application or release. No old
worker fence, old-day baseline, admission, terminal events, reconciliation,
UTC-rollover liability handling, all-path cutover or migration/rollback proof
is supplied by this storage-only package.

### Immutable outcome-event storage foundation (not activated)

Status: **Definition and standalone storage API only; caller wiring, PostgreSQL
evidence and activation pending**. `outcome_events.py` builds one normalized
observation against a frozen receipt. `LLMOutcomeEventManager.record_event`
requires explicit trusted payer scope, reads that payer's persisted receipt,
validates price/usage evidence, and acknowledges only an owned commit or an
exact scoped replay. Only the named event-identity constraint suppresses an
insert; differing/inaccessible evidence and other write/commit errors propagate.
An uncertain commit is not a dispatch or settlement authorization.

Only client-owned `accounting_usage`, `accounting_cost_reported` and
`accounting_cost_usd` fields are read. Generated text, legacy projection zeros
and current model tariffs are not monetary evidence. Structured provider price
arbitrates; a reported but unresolved amount blocks tariff fallback. Explicit
provider zero or both explicit frozen zero rates are FREE. Missing price/usage
remains NULL/UNDEFINED. Zero observed usage at a nonzero tariff is priced zero,
not explicit FREE. Not-dispatched evidence is a separate NULL-cost state.

This new, unwired builder requires exact representability in Numeric(18,9);
unrepresentable positive amounts are never rounded to zero/FREE. Safe normalized
decimal provenance is retained when possible, not raw billing payloads or signed
provider proof. This differs from the existing journal's rounding adapter and
must be reconciled deliberately before activation, not by rewriting history.
Tariff calculation uses the receipt snapshot, never a later model price.

Timeout, cancellation and unknown observations remain uncertain even if cost
is known. A later reconciliation uses another event UUID and never mutates
the earlier event. Observed UTC time may cross midnight without changing the
receipt's preparation day. No reducer/settlement is provided: multiple events
for a receipt must not be summed as multiple paid attempts; this API cannot
release a reservation or unresolved liability.

Migration definition 0094 adds only the event table, RESTRICT foreign keys,
constraints/index and schema-bound mutation/binding guards. It does not alter
0093 or existing evidence. The manager requires both receipt and event guards;
it never creates them. The insert guard checks payer/policy/time and explicit
free/provider consistency; full tariff recomputation is manager validation, not
a general direct-SQL authorization guarantee. Privileged DDL is outside this
guard threat model.

0094 is **NOT APPLIED**. Prior test-only permission for 0093 does not authorize
0094 or pytest's metadata/bootstrap/permission registration. Before commit,
rollback is transactional; after application, preserve events, receipts and
guards. Ordinary downgrade is refused, even when empty. PostgreSQL definition/
model/catalog parity, guard rollback, trusted-payer isolation, exact replay/
collision, owned commit/ack faults and real two-session races remain required.
Production migrator, cutover, runtime and full PRD-03 are not certified.

### Unwired atomic reservation boundary — merged foundation

The merged bounded boundary ([PR #180](https://github.com/Starck43/social-media-ai/pull/180),
merge `df06b1e17d5d13d7f3e7171dff6d7a317c87cca2`) is `reserve_dispatch_receipt` /
`LLMBudgetManager.reserve_receipt`; it is not called by any provider client.
One owned transaction locks the active payer and the pre-provisioned UTC-day
budget row, resolves the existing plan/override/global cap from that locked payer,
and appends the receipt plus immutable reservation while changing the balance.
Only a fresh acknowledged commit returns success; exact replay returns False
and cannot authorize another HTTP request. Standalone receipts cannot be upgraded.

Migration definition 0095 adds two tables and reservation binding/immutability
guards. A real owner-local normal0094→0095 rehearsal passed on the approved
legacy0092 clone advanced through actual0093/0094; this is not an application
to the source/production database. No runtime baseline provisioning, bootstrap,
backfill or production cutover is delivered by this API. A missing, frozen or unreviewed day baseline fails closed, including
for unlimited payers. Reviewed-bound versions are trusted internal inputs,
not estimates from character counts/max-output settings or HTTP arguments.
Known paid attempts without a bound are allowed only for unlimited payers and
are explicitly unbounded, never zero. FREE has an explicit zero tariff basis;
UNDEFINED uses `owner-undefined-allow-v1`, a separate counter and zero cap ceiling
that is not an actual cost. Undefined admissions bypass priced-cap arithmetic,
not payer, schema, baseline or freeze checks. Amounts must be exact Decimal USD
at 1e-9; unrepresentable legacy caps fail closed rather than being rounded.

There is no release/settlement or expiry path. Outstanding known/free/unbounded
older-day reservations conservatively block capped priced admissions; current-day
unbounded liability also blocks them. Unlimited and owner-allowed undefined
attempts keep their separate accounting states. Original cap/receipt/bound are
preserved on replay. The reservation balance is not summed with analytics.
Evidence now includes57 focused no-DB checks and one real isolated PostgreSQL
node/all14 journaled scenarios at `d10bde4c3e63a5308e5ae61b8f46b87e63cf3c71`,
plus the separate owner-local normal migration report at
`f4c14c4da0fc212591ca0f88c35db3f8a20d87d3`. Original test SHAs and retained-data
manifests are not relabelled; [exact evidence and limitations](validation/llm-budget-pg-runner.md).
This is not production-baseline or whole-gate acceptance. Durable day-baseline
provisioning, reviewed bound production, client/caller wiring, cap-overrun
settlement, release, all-path accounting and rollout stay OPEN.

### Proposed all-path accounting and admission contract

Status: **Owner pricing decision recorded; admission implementation and evidence
still pending — not activated**. This is the full-gate contract, not a new backlog
or a release claim. Analyzer accounting certainty is integrated in PR #118;
provider-reported analyzer pricing and the three-state monetary mapping are
integrated in PR #125 (accepted head `08c53e1`, merge `d2b687d`); owner-local
checks were accepted, while PostgreSQL and full suite were NOT RUN at that original
revision. Preparation receipts (#137/#154) and immutable outcome storage (#168)
are now integrated but UNWIRED. The #180 atomic receipt/reservation manager is
also merged but UNWIRED. All-path admission/activation, settlement and release
remain open; do not relabel their original evidence.

**Payer and coverage.** Every actual HTTP attempt in all three clients must pass
one accounting/admission boundary, whether reached by analysis, digest, agent
chat, learning/reflection, prompt suggestion, an internal tool, fallback or retry.
Trusted execution context binds tenant or explicitly configured operator payer,
purpose and execution references; prompts, provider output and caller keywords
cannot choose a payer. Missing scope is denied, never silently charged to a
bootstrap workspace. Each fallback/retry gets a fresh dispatch identity; replay
of one persistence event retains its identity. Record local validation failures
as not-dispatched without claiming a paid attempt.

**Durable preflight and evidence.** Before HTTP, use the existing manager/owned
transaction conventions to atomically reserve budget and append an immutable
receipt: payer, UUID, purpose/execution, provider/model, UTC day and preparation
time, exact tariff snapshot, conservative reservation ceiling and policy version.
Only an acknowledged commit grants dispatch. No DB lock spans network I/O.
Preparation time is not proof that the provider received the request: a crash
between commit and HTTP is an unresolved receipt, not a fabricated paid success.
Terminal/uncertainty events append to the same identity; do not rewrite the
insert-only journal or re-price old attempts from current model tariffs. The
raw dispatch identity/time must be durable, not only an irreversible key hash.
References to jobs, messages and results must not cascade-delete evidence.

**Recorded pricing policy and admission contract.**

- Keep the existing UTC day and resolved workspace/global cap semantics; do not
  silently redefine plan ceilings or the existing unlimited sentinel.
- Monetary authority is Decimal USD at the documented precision. Legacy USD and
  USD-cent columns remain adapters, never summed again on top of ledger spend.
- Owner-decided pricing truth (2026-10-12), replacing the earlier proposal that a
  zero default tariff is unknown: FREE only on an explicit zero in a structured
  provider USD report or an explicit zero tariff. Structured provider price,
  never generated model text, arbitrates actual cost; a non-zero tariff is the
  known-price basis. Missing/unresolved price is UNDEFINED: never implicitly free
  or zero, separately recorded in statistics and
  related accounting actions; dispatch is not blocked by it, but it is
  unreservable and outside cap-bounded spend by construction. Known token counts
  remain distinct from missing usage. Preserve the approved tariff snapshot for
  each dispatch.
- For a capped payer, dispatch requires a reviewed upper-bound reservation
  for all billable inputs/outputs, including media and provider-specific extras.
  A character count or provider max-output setting alone is not such a bound.
  An unresolvable price (UNDEFINED) does not fail closed: dispatch proceeds with a
  zero ceiling and the undefined state is recorded. An unlimited payer
  may bypass only the cap, not payer validation, receipts or outcome accounting.
- Lock one payer/day budget row in an owned transaction. Admit only when settled
  spend plus existing reservations plus the new ceiling fits the current cap;
  insert receipt/reservation in that same transaction. Distinct tenants isolate
  balances; a changed cap is checked at admission. Denial cannot trigger fallback
  to bypass the same payer's limit. Intended overshoot is zero.
- Settle known actual cost and release the unused reservation atomically and
  idempotently with terminal evidence. Unexpected actual cost above its ceiling
  records the full liability, freezes further admission and requires review;
  do not clamp it to the reservation or claim the cap was never exceeded.
- Missing usage, timeout, cancellation after possible dispatch, process loss or
  uncertain settlement retains liability and blocks capped admission until
  reconciled, including across UTC-day rollover. Release only with known cost
  or proof of no dispatch/no charge. A TTL is not proof and never grants a refund.
  Unknown evidence remains auditable even for explicitly unlimited payers.

**Recovery and rollout.** A bounded reconciler retries append/settlement with the
same event IDs, records unresolved states without raw secrets, and never resends
an LLM request to repair accounting. Duplicate suppression must match the exact
unique constraint; other integrity/commit failures remain visible. Use reviewed
schema-qualified Alembic definitions only; no runtime-created tables, migration
application, schema reset or deployment is authorized by this proposal. Before
cutover, approve an existing-day baseline and old-worker/in-flight fence so
projection-only spend is neither erased nor counted twice. Disable admission if
the required schema/accounting service is unavailable; staged rollout must not
start a second scheduler/listener/migration owner.

**Full-gate acceptance evidence required, all still open.**

- [ ] Mocked all-client/all-caller path matrix: every observed HTTP attempt has a
  payer-bound receipt and distinct identity, including fallback, retry, tool,
  failed output and failed transport; no double-recording or scope injection.
- [ ] Owner-local PostgreSQL races: concurrent reservations at the cap, separate
  tenants, cap changes, insert races, rollback and lost commit acknowledgements.
  No request starts without committed admission; reserve/settle/replay is atomic.
- [ ] Failure matrix: missing/invalid tariffs (UNDEFINED, dispatch with recorded
  state), explicit zero tariffs (FREE), structured provider prices, unknown/partial usage,
  unexpected overrun, cancelled tasks, crash windows, DB outage and UTC rollover;
  unresolved liability cannot silently become free or admit further capped work.
- [ ] Durable recovery and cleanup: repeated reconciliation adds no duplicate
  cost; transcript/job/result cleanup retains immutable receipts/outcomes.
- [ ] Operator-reviewed migration/cutover/rollback and baseline packet against an
  explicitly approved isolated test target; focused unit plus affected integration
  suites, exact SHAs and redacted transcripts. No automatic schema operations.

The recorded owner pricing decision and development task permit preparing code
and migration definitions, not applying them or enabling live dispatch. Gate acceptance requires
this evidence, not merely merging the proposal or the analyzer patches.

## Key implementation files

- `app/services/ai/analyzer.py` — modality analysis, validation, coverage and save flow.
- `app/services/ai/prompts.py` — prompt assembly and variables.
- `app/services/ai/json_schema_builder.py` — output schema/instruction compiler.
- `app/services/ai/scenario.py` and `scenario_templates.py` — scenario construction.
- `app/services/ai/llm_client.py` and `llm_provider_resolver.py` — clients and model selection.
- `app/services/ai/attempt_journal.py` — per-attempt journal builder, pricing and recording.
- `app/services/ai/param_registry.py` — methodology versus run-target parameters.
