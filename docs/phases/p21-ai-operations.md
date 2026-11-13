# P21 — AI model operations and dispatch safety

[Product direction](../product/PRODUCT_PLAN.md#foundation-directions) · [ROADMAP](../../ROADMAP.md#p21)

## Goal and owner decisions

One managed catalog plus personal/BYOK catalogs, safe dispatch and attributable
usage. The owner decision of 2026-11-13 (OpenCode `ses_e2fa3690affea2bSCgS5dlSVmg`)
sets one user tariff across owned workspaces, configurable module grants, token
limits (including explicit unlimited) and an owned-workspace count limit.
[P2-T17](../tasks/p2-t17.md) owns that tariff/holder contract and remaining
period/counting/BYOK-applicability/overage decisions.

Invited members use the host owner's configured models and functionality subject
to member/object permissions, never their own tariff as an override. Any workspace
role is denied host tariff/catalog/provider/key/model/default management. BYOK
delegation needs explicit owner consent; no secret is exposed to a member.
Accepted personal payer bindings survive until an explicit approved migration.
Corporate billing P2-T08 remains deferred. No code, deployment, live provider,
payment or public-migration authorization is inferred from this plan.

## Data-shape recommendation

| Relation | Rationale |
| --- | --- |
| New `llm_catalogs` | Explicit platform/personal custody within the existing provider/model schema. |
| New `plan_model_grants` | Reusable plan/capability/model availability, default and ordering. |
| New `llm_rate_buckets` | Shared upstream quota state across tenants; social connection buckets have the wrong ownership/FK. |
| Optional `llm_model_health` | Only if measurements justify separating hot health writes from static catalog rows. |
| Existing `llm_*`, scenarios, tenant policy, billing and secret stores | Reuse, preserve accepted evidence and migrate consumers before removing legacy fields. |

Expected baseline: three additional relations and removal of a legacy selection
column only after inventory, mapping and approval. A fourth operational relation
needs measurement. Scope/reference/capability columns may increase; accidental
duplicate configuration should decrease. No existing table is scheduled for deletion.
Every schema change needs named consumers, constraints, migration and rollback.

## Execution order and contracts

Task IDs below follow prerequisite order. P0 security surveys come first;
P1 tariff/storage/runtime foundations follow. Independent P0 surveys may run
concurrently. P2-T17 can proceed alongside them, but its holder/policy deliverable
must exist before the dependent consumers. ROADMAP alone owns status/priority.

| Order / task | Prerequisite deliverable | Required handoff |
| --- | --- | --- |
| 1. [P21-T10](../tasks/p21-t10.md) Prompt/tool safety | None | Source-backed entry-point/tool denial matrix; fixes are separate scoped work. |
| 2. [P21-T11](../tasks/p21-t11.md) Credentials | None | Custody/version/egress contract and regression evidence; no new encryption scheme. |
| External: [P2-T17](../tasks/p2-t17.md) User tariff | Accepted P2 entitlement/configuration foundations | Holder binding, policy/version/expiry and token counting/period contract; checkout excluded. |
| 3. [P21-T12](../tasks/p21-t12.md) Dispatch contract | T10 findings, T11 custody, P2-T17 holder interface | Typed contexts and dormant adapter seam with explicit extension interfaces. |
| 4. [P21-T13](../tasks/p21-t13.md) Catalog/grants | T12 contract and P2-T17 tariff storage | Backfilled catalog/grant constraints and owner-only management; dispatch stays off. |
| 5. [P21-T14](../tasks/p21-t14.md) Shared rate control | T12 seam, T13 provider/catalog identity | Stable quota pools, leases, cancellation policy and admission lock order. |
| 6. [P21-T15](../tasks/p21-t15.md) Atomic admission | T14 handoff, P2-T17 allowance policy | Token/USD reservation UOW, immutable evidence and settlement-state contract. |
| 7. [P21-T16](../tasks/p21-t16.md) Settlement/reconciliation | T15 admission storage and contracts | Idempotent accounting, crash recovery and integrated mocked phase check. |

Storage/contract acceptance never depends on later production activation. T12
tests later services through explicit fakes; T13 does not activate managed routing;
T14 proves rate acquisition without requiring a completed T15 ledger. T15 defines
and delivers the state contract T16 consumes. No circular dependency is hidden
behind a requirement to turn on HTTP before its safety foundations exist.

## Storage responsibilities

| Responsibility | Owner |
| --- | --- |
| User plan, holder, module grants and allowance definition | P2-T17; no workspace copies of a subscription. |
| Catalog/provider/model custody, FK migration, capabilities/prices/defaults | T13, consuming T11 secret-source/version contract. |
| Stable upstream pool, fairness and recoverable concurrency leases | T14; token quotas are not provider TPM. |
| Immutable dispatch snapshot and token/USD reservation | T15; reuse personal USD ledger, name missing tenant settlement state. |
| Mutable settlement/reconciliation state and outcome evidence | T16; immutable receipts cannot contain mutable terminal/lease fields. |
| Optional hot model health state | T14 only after measured contention; no fixed table-count target. |

## Phase verification and activation gate

T16 records the integrated mocked route matrix for chat, analysis, digest, tools,
learning, prompt suggestions and probes: fresh membership/holder/module/grant/
consent/key-version checks, no-HTTP denials, same-class/payer fallback, cross-space
owner token races, rate contention and crash/replay/rollover reconciliation.
Each executor records actual source/documents read, named consumers, focused
commands, tested SHA, isolated-schema migration/retained-data/cleanup evidence
and rollback in its own card/PR. No surrogate coordination task is required.
Owner activation decisions and P2-T09 live verification remain separate;
an integrated mocked pass is not production or capacity certification.

## Previous plan numbering

The earlier numbered plan named the same work; ROADMAP status and the execution
order above are authoritative.

1. P21-T01 — **DONE 2026-11-12**: planning package accepted and integrated; no implementation activation.
2. P21-T02 — one dispatch, access, payer and fallback contract.
3. P21-T03 — atomic admission and allowance reservations.
4. P21-T04 — settlement, orphan detection and reconciliation.
5. P21-T05 — shared-key rate limits, concurrency and backpressure.
6. P21-T06 — credential custody, egress and rotation safeguards.
7. P21-T07 — untrusted-input/tool safety coverage audit.
8. P21-T08 — shared catalog/grants and validated generation policy.

T02 is normative. T03/T04 and T05 integrate its boundaries; T06/T07 can audit
independently. T08 migrates catalog structure without dispatch activation, then
enables managed routing only after its runtime/credential predecessors pass.
Coordinate overlapping files through task branches; independent cards are not
authorization for conflicting writers. Existing P2 acceptance and production-only
P2-T09 remain intact; P2-T08 shared corporate billing stays owner-deferred.

## Identifier continuity and planning provenance

The accepted [P21-T01 audit](../audit/p21/p21-t01.md) retains its original numbering
and pinned historical commands. On owner request, open implementation cards were
reordered using new unused IDs, not recycled published IDs:

| Historical ID | Current ID |
| --- | --- |
| P21-T07 | P21-T10 |
| P21-T06 | P21-T11 |
| P21-T02 | P21-T12 |
| P21-T08 | P21-T13 |
| P21-T05 | P21-T14 |
| P21-T03 | P21-T15 |
| P21-T04 | P21-T16 |

The P21-T09 documentation claim was published in `fc35c553`; on owner request
its temporary card/row is removed, not marked as an accepted implementation.
Its decisions now live here/P2-T17, agent instructions in the implementation
cards, and source findings beside the affected storage work. Previous corrections
are retained in Git commit `5b4c5ec2`; the task branch is temporary and may be
removed only after integration. No accepted T01/P2 result is rewritten.

Planning documents read: README/AGENTS/DESIGN/DOCS_INDEX; P21 cards/T01 audit;
P2-T08/T09 and P2 phase; MODELS base isolation; MODEL_DELETION consequences;
TENANCY plans/personal catalog; ENTITLEMENTS/SUBSCRIPTIONS; COLLABORATION task and
decision rules. Source inspected: LLM declarations and budget manager, personal
billing/ledger, tenant/scenario policy, social rate budget and credential fields.
These are source observations, not deployed-schema evidence. Initial scoped
link/queue/whitespace checks passed. The validator below checks the renumbered
links, queue, prerequisite DAG, retired paths and preservation of foreign rows.
Renumbered planning check: PASS (exit 0); `git diff --check origin/dev`: PASS.
No application/DB tests or migrations ran for this documentation change.

```sh
python3 - <<'PY'
from pathlib import Path
import re, subprocess
paths = subprocess.check_output(['git', 'diff', '--name-only', 'origin/dev'], text=True).splitlines()
paths += subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], text=True).splitlines()
for name in sorted(set(paths)):
    assert name == 'ROADMAP.md' or name.startswith('docs/'), name
    file = Path(name)
    if not file.is_file() or file.suffix != '.md':
        continue
    body = re.sub(r'```.*?```', '', file.read_text(), flags=re.S)
    for target in re.findall(r'\]\(([^)]+)\)', body):
        if re.match(r'(https?://|mailto:)', target):
            continue
        relative = target.split('#', 1)[0]
        assert (file.parent / relative if relative else file).resolve().is_file(), (name, target)
queue = Path('ROADMAP.md').read_text()
ids = re.findall(r'^- \[([A-Z0-9]+-\d+|P\d+-T\d+)\]', queue, re.M)
assert len(ids) == len(set(ids))
expected = [f'P21-T{i}' for i in range(10, 17)]
assert [task for task in ids if task.startswith('P21-')] == expected
for i in range(2, 10):
    assert not Path(f'docs/tasks/p21-t{i:02}.md').exists()
for task in expected:
    body = Path(f'docs/tasks/{task.lower()}.md').read_text()
    assert body.startswith(f'# {task} ')
    deps = re.search(r'Prerequisites: (.*?)(?:\n\n)', body, re.S).group(1)
    for dep in re.findall(r'\[(P\d+-T\d+)\]', deps):
        assert dep in ids
        if dep.startswith('P21-'):
            assert expected.index(dep) < expected.index(task), (task, dep)
old = subprocess.check_output(['git', 'show', 'origin/dev:ROADMAP.md'], text=True)
def foreign(text):
    return [r for r in text.splitlines() if r.startswith('- [') and not re.match(r'- \[(P21-T\d+|P2-T17)\]', r)]
assert foreign(queue) == foreign(old)
subprocess.run(['git', 'diff', '--check', 'origin/dev'], check=True)
print('PASS: links, queue order, acyclic prerequisites, retired paths and foreign rows')
PY
```
