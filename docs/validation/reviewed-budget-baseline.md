# Reviewed day-baseline provisioning — sandbox evidence, UNWIRED

[Draft PR #197](https://github.com/Starck43/social-media-ai/pull/197).
[Reviewed packet/fence and unapplied migration contract](../design/reviewed-budget-baseline.md).
Implementation and tests: `b3cdc0fb2bded7a4ed67a235cf1823afffd2584d`.
Dev base: `9c7bf4d146d8086d279ee9d1f80a950af9cdff43`.
Later documentation-only successors do not relabel this tested implementation.

## Executed remote isolated checks

Clean sandbox checkout at the published SHA; separate `/data/baseline-venv`,
not the owner's project .venv. Actual source loaded with module-local explicit
import doubles; no app startup, real sessions, root conftest, .env, credentials,
DB/provider/runtime/HTTP calls. Alembic operation capture uses a double, not a
migration runner. Dependencies installed within repository manifest ranges.

Python 3.13.14; pytest9.1.1; pytest-asyncio1.4.0; SQLAlchemy2.1.4; Alembic1.20.0.
Changed-file formatting: Black26.10.1, isort9.0.2. No unrelated formatting.

```sh
/data/baseline-venv/bin/python -m pytest tests/test_reviewed_budget_baselines.py --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

Exit0: **36 passed; 122 subtests passed; failed0; skipped0; warnings0** (0.36s).
An earlier local sandbox preparation at `45301070470db462fe8bbd1cef52ba4435c953a2`
passed the same36 tests/122 subtests with four configuration/collection warnings;
the published successor adds strict fence-version type checking, renames the
non-test ORM base and installs manifest-compatible pytest-asyncio. The final
run above is the authoritative published pin, not a relabel of the earlier run.
No accepted PR180 checks were rerun.

Coverage: complete explicit packet/no zero defaults; exact USD limits; mandatory
initial freeze; payer/UTC/counters/review/digest/fence syntax; owned two-insert
mock atomicity; payer→day lock order and scoped SQL; named identity collisions;
exact replay after changed live balances/counters/freeze; conflicting packets;
legacy/manual day and orphan provenance refusal; unrelated integrity rollback;
synthetic pre/post-commit acknowledgement failure, replay commit failure,
rollback error and cancellation; required enabled guard probe; model/definition
column/check/FK/unique parity; schema-qualified immutable and initial-binding
DDL; refused destructive downgrade; no client/HTTP wiring or mutable-day edits.
These are **mock/source/DDL-capture checks**, not real session/PG proofs.
Synthetic acknowledgement loss is not real network loss.

Static review: `git diff --check` exit0; `reserve_receipt` AST unchanged vs base.
`budget_admission.py`, migrations0093–0095 and the original
`llm-budget-pg-runner.md` evidence are byte-unchanged. Published tree was compared
to the sandbox proposal before checkout/testing. No independent reviewer approval
is inferred from this source review.

## Still required — not executed or authorized by this package

Keep Draft pending separately approved real PostgreSQL acceptance through local
sequential Kilo R10: same-payer two-session provisioning/replay/conflict races,
provisioning-vs-admission lock ordering, distinct payer isolation, actual guard/
constraint/catalog parity, second-insert rollback, owned session and commit/readback
uncertainty, normal0095→0096 rehearsal on an explicitly approved target with
protected evidence/catalog manifests. No original social_manager migration,
create_all/reset/drop/stamp/bootstrap, runtime/provider/deploy or cleanup.
Environment-level model/permission registration effects during normal Alembic
execution also require review; operation capture does not exercise migrations/env.py.

No unfreeze/activation, baseline inference/import, reviewed-bound production,
receipt/outcome reduction, settlement/release, rollover/refund or full accounting
matrix is delivered. All new days are frozen. Syntax/digest validation is neither
operator authorization nor verified external fencing. Worker/payer-wide in-flight
coverage and prior liabilities must be inspected in the retained evidence before
submitting the trusted preparatory packet or considering a later activation.

## Preserve historical acceptance

[Original evidence](llm-budget-pg-runner.md) stays at its existing pins and caveats:
remote `d10bde4c3e63a5308e5ae61b8f46b87e63cf3c71` (57 no-DB PASS; one isolated PG
node /14 scenarios PASS; retained11 receipts/10 reservations/18 days; sequence
advancement19/15/18), and owner-reported normal0094→0095 at
`f4c14c4da0fc212591ca0f88c35db3f8a20d87d3` on the approved legacy clone only.
Keep ck_tenants_plan variance, reconstructed before0093, measured before0095,
private evidence not remotely inspected, PR137 and old31/6 caveats. This new
package neither reruns them nor certifies a production/universal baseline.
PR180 remains merged, closed and UNWIRED; PR194 closeout is not reopened.
