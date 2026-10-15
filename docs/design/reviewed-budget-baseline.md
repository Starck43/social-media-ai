# Reviewed UTC-day baseline provisioning — unwired

## Minimal contract, before storage implementation

`LLMBudgetManager.provision_reviewed_day(record)` is a standalone trusted-internal
API, not an HTTP endpoint, accounting importer, reviewer or worker fence. The
payer comes from explicit tenant scope and must match the packet. An active
payer is locked before its day, exactly as admission does. No external session.

The packet supplies every field, with no monetary/counter defaults:
- `tenant_id`, `utc_day` (a date, never a datetime), `baseline_version` (a label);
- initial `settled_usd`, `reserved_usd` (exact Decimal USD, Numeric(18,9)),
  `undefined_count`, `unbounded_count` (nonnegative int32), `frozen` (explicit bool);
- `reviewed_by` (redacted stable operator ID), `review_ref` (opaque review record ID),
  `evidence_sha256` (lowercase SHA256 of a canonical redacted evidence manifest);
- `cutoff_at`, `reviewed_at` (aware UTC timestamps; day start <= cutoff <= day end,
  cutoff <= review <= now); `fence_ref` (opaque retained fence record ID),
  `fence_version = quiesced-accounted-v1` (the explicit attestation below).

IDs are bounded opaque identifiers, not arbitrary URLs, prose or private paths.
Neither a version string, digest nor a syntactically valid packet proves review.
Only the authorized internal operator may submit a packet after inspecting the
retained manifest/review/fence evidence. This API validates and stores the claim;
it does not authenticate an operator, fetch evidence, stop workers or prove it.
Tenant scope is payer isolation, NOT operator authorization. No public route or
client call is added; exposure would require a separate authorization contract.

## Required review and old-worker/in-flight fence

`quiesced-accounted-v1` asserts that **all payer paths** capable of billable
attempts are stopped/fenced (including fallback, retry, tools and other workers),
and stay stopped from cutoff through a separately approved activation. No old
worker can resume or an in-flight request escape accounting during this interval.
The reviewer inventories attempts through cutoff from day start; structured
provider USD arbitrates, explicit zero alone is FREE, missing price is UNDEFINED.
Projection zeros or absence of rows are never evidence of zero spend. A zero
snapshot requires affirmative coverage/no-charge evidence in the manifest.

The manifest includes inventory/coverage, known settled amounts, outstanding
bounded liabilities, separately counted undefined/unbounded liabilities, evidence
for no-charge exclusions and dedup decisions, plus prior-day liability inventory.
Unknown in-flight exposure must remain conservatively reserved/unbounded and
**frozen**, never declared settled zero or refunded. If an exact representable
snapshot cannot be established, do not submit it. In this preparatory package
**all new days start frozen**; provision requires `frozen=True`. This does not
supply admission-ready activation or an unfreeze method. Initial freeze is a
safe contract boundary, not claimed worker fencing. Reviewed unfrozen activation
and unresolved/prior-day liability handling require a separate package.

Amounts are an absolute initial snapshot, not a journal sum or an instruction to
reset another day. Each selected day needs its own reviewed packet; no automatic
UTC rollover, liability reset, expiry, refund, release or settlement. New days
do not reconcile previous days. Never sum projections with these snapshots.

## One-time storage and transaction result

One owned transaction inserts the missing day and its immutable review packet.
`True` is returned only after commit acknowledgement. `False` means exact packet
replay, not new insertion, dispatch permission or activation. Replay compares the
**initial immutable snapshot**, not current counters/freeze (which may later
change through separately reviewed APIs). It never changes any day balance.
Any packet difference conflicts. A legacy/manual day without provenance is a
conflict even if its amounts/version match; it is never adopted or overwritten.
An orphan provenance row or named insert collision fails closed. Inactive/absent
payer, missing guards, unrelated integrity errors, rollback failures, cancellation
and commit uncertainty propagate, never True/False success. Readback after an
uncertain commit is a separate same-packet replay, never a blind fresh write.

## Minimal migration definition — NOT APPLIED

Revision `0097`, parent `0096`, adds only `llm_budget_baselines`: unique payer/day,
RESTRICT FK to the existing day, original snapshot and review/fence fields.
Schema comes from `settings.DB_SCHEMA`; no old migration edits or backfill.
Checks bound amounts/counts/IDs/UTC cutoff ordering and require initial freeze.
Schema-qualified UPDATE/DELETE/TRUNCATE guards make the packet immutable; a
BEFORE INSERT guard binds its initial snapshot to the day. Manager checks enabled
schema-bound triggers before writing (metadata-only bootstrap fails closed).
Privileged DDL/trigger replacement and direct SQL operator authorization are
outside this threat model. Ordinary downgrade refuses evidence deletion; an
explicit evidence-preserving rollback/retention plan is required before rollout.

Definition preparation is NOT permission to run Alembic, create_all, reset,
bootstrap or touch the original social_manager. Real PG catalog/constraint/guard
parity, rollback/commit ambiguity and two-session provisioning/admission races
remain deferred for an explicitly approved target via sequential local R10.
Mock tests are not real PG acceptance or production-baseline certification.
Existing PR180/194 evidence remains at its original SHAs and is not rerun.


## Access-model compatibility — current contract

Authoritative dev `eb6cad6c56e78768b6482eba312082b849d24712` separates
platform/workspace roles, canonical LLM models and explicit resource permissions.
PR197 follows that contract; it does not restore legacy roles, implicit ownership,
auto-permission registration or module-level manager binding. Canonical provenance
model is `app.models.llm.llm_budget_baseline`; old analysis path is only an alias.
Existing bootstrap bindings and platform/workspace authorization remain unchanged.
The baseline is internal storage, intentionally NOT a resource in access_catalog;
no ModelType, Permission or role grant is introduced for it. Public facades expose
the class but no generic baseline manager; trusted provisioning remains on the
existing budget manager. Tenant scope is still payer isolation, not user authority.

The old unpublished-in-dev baseline0096 definition is superseded by baseline0097;
access0096 belongs to dev and is untouched. Prior pins/evidence remain historical.
Do not run any old PR1970095→0096 instruction. Baseline acceptance now requires an
independently reviewed access0096 target and then separately approved0096→0097.
A target reported at0095 is not sufficient. Access0096 migrates roles/permissions
and needs its own target-specific preservation/cutover approval; this baseline
package grants none. If any target applied the old baseline0096 or is ambiguous,
STOP for catalog/version/evidence reconciliation, never stamp/reset/reapply.

For local pytest follow current AGENTS.md/.agent/rules.md: primary OpenCode route,
Kilo secondary only if primary BLOCKED, DB checks sequential. Historical Kilo
instructions below/in old evidence are not the current routing authority.


Latest checkpoint: authoritative dev `edbe0fc02e6ea2d78302129e51febfa0e87464ab`
includes read-only pytest schema/reference validation and separately accepted
local access behavior. That acceptance is not baseline0097 certification. Follow
[the current local assignment](../validation/reviewed-budget-baseline-local.md),
not old PR180 runners or historical baseline0096 instructions. No default pytest
bootstrap/seed is available or authorized to repair a missing baseline table.
