# Approval migration `0148` — allocation record for P19-T04

Registry: [ROADMAP](../../ROADMAP.md#priority-p3); the task row and owner live there.
Card: [P19-T04](p19-t04.md)

- **Revision:** `0148`
- **Parent:** `0147` (`down_revision = "0147"`) — the single Alembic head on `dev`; see the numbering history below
- **Granted:** 2026-11-09 by the auditor, at the owner's instruction ("выделяй")
- **Granted to:** `notion-ai-p19-t04`, branch `feat/p19-t04-document-approval`
- **Base at allocation:** `dev` `bca3b969`, Alembic head `0143`

## What this number covers

Exactly the DDL proposed in the P19-T04 card, reviewed and accepted for allocation:

- one table `document_approvals` (tenant-scoped, PII-encrypted comment), with the
  listed FKs, the `status` / hash-pattern / timing CHECKs and the partial unique
  index that permits one open request per document;
- the `contact_pii_marker` trigger reusing `bump_contact_pii_marker()`;
- the model in `app/models/knowledge/`, plus the access-catalog resource
  (`documents.approval`, decided with `CONFIGURE` as proposed) delivered with the
  same migration.

## Conditions

1. **No further tables or shared relations.** New DDL beyond this list needs a new
   allocation; do not extend `0148` silently.
2. **One head.** `0148` must leave `ScriptDirectory.get_heads()` with exactly one
   revision, and `dev`/`public`/`test_schema` stay at `0143` until the merge is
   accepted by this auditor.
3. **Cycle evidence.** Upgrade `0147 → 0148`, downgrade `0148 → 0147` while empty,
   and a refused downgrade while approvals exist — on an isolated schema, not the
   shared `test_schema`.
4. **Number is spent once allocated.** If the task is abandoned, report it; the
   number is not reused by another task without a new allocation.
5. **No provider I/O, no public-schema migration** without a separate owner
   authorization.

## Reservation register

| Revision | Task | State |
|---|---|---|
| `0143` | P16-T02 | spent, in `dev` |
| `0148` | P19-T04 approval | allocated 2026-11-09, not merged |
| `0149` | P15-T08 reply evidence | allocated 2026-11-09, not merged |
| next free | — | `0150` |

## Numbering history (2026-11-09)

- `0146` was allocated first, then collided with P15-T08. The auditor then pointed at `0144`,
  which is really taken by contact-channel latency, and finally at `0147`, which the calendar
  branches (`0146_calendar`, `0147_source_objects`) occupy in `dev`.
- **Root cause:** allocations were made against a stale reading of `migrations/versions/`
  instead of the live `dev` head. The rule below now governs every allocation.
- Final assignment: **`0148`**, parent `0147` (the live head, `ScriptDirectory.get_heads()`
  returns exactly `['0147']`). Run the cycle from that head, never from a number named in an
  older record.

## Allocation rule (applies to every task)

1. Allocate against the **live** `dev` head from `ScriptDirectory.get_heads()`, never against
   a remembered number or a file list read earlier.
2. One number per task; parallel tasks chain (`head → +1 → +2`), never both take the head.
3. A number is announced together with its parent in one message, so the executor never has
   to come back for a correction.

## Superseded numbering (2026-11-09, owner-directed)

- The numbers in this note are **history, not a reservation**. Under the
  [migration numbering rules](../COLLABORATION.md#migration-numbering-and-parallel-tasks)
  a task writes a placeholder revision and the number is assigned at merge.
- P19-T04 and P15-T08 both follow the live `dev` head `0147` and both ship a
  placeholder revision. The agent that integrates them in `dev` assigns the next
  free numbers in integration order, rewrites only `revision`/`down_revision`,
  and records the landed number in the audit note.
- Do not ask the auditor for a number and do not wait for one. A green cycle on
  the task's own isolated schema is the evidence; changing only the numbering
  needs no new cycle, only the single-head check.

## Superseded numbering (2026-11-09, owner-directed)

- The numbers above are **history, not a reservation**. Under the
  [migration numbering rules](../COLLABORATION.md#migration-numbering-and-parallel-tasks)
  a task writes a placeholder revision and the number is assigned at merge.
- Both tasks follow the live `dev` head and ship a placeholder revision. The
  integrator assigns the next free numbers in integration order, rewrites only
  `revision`/`down_revision`, and records the landed number in the audit note.
- Do not ask the auditor for a number and do not wait for one. A green cycle on
  the task's own isolated schema is the evidence; changing only the numbering
  needs no new cycle, only the single-head check.

## Integration record: migration 0148

At the owner's request, the approval schema package is integrated separately to
unblock a dependent task. Revision `0148` follows `0147`; the table, model,
`documents.approval.CONFIGURE` catalog and owner/admin/manager grants are one
package. This is not completion or acceptance of P19-T04, which remains
`IN_PROGRESS`: workflow, notifications, API/UI and end-to-end checks remain.

On a disposable local PostgreSQL 16 database and an exclusively owned schema,
the exact package was upgraded from the real baseline through `0147`, upgraded
to `0148`, downgraded empty to `0147`, upgraded again and refused downgrade with
an approval row (the row and revision remained intact). After cleanup the owned
schema and database were removed, and public and owned-schema inventories
matched their initial state. No provider calls or public-schema migrations.
`tests/knowledge/test_p19_t04_migration_unit.py`: 5 passed; generated reference
check passed. The head check is `['0148']`.
