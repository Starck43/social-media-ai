# Approval migration `0147` — allocation record for P19-T04

Registry: [ROADMAP](../../ROADMAP.md#priority-p3); the task row and owner live there.
Card: [P19-T04](p19-t04.md)

- **Revision:** `0147`
- **Parent:** `0143` (`down_revision = "0143"`) — the single Alembic head on `dev`; see the renumber note below
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
   allocation; do not extend `0147` silently.
2. **One head.** `0147` must leave `ScriptDirectory.get_heads()` with exactly one
   revision, and `dev`/`public`/`test_schema` stay at `0143` until the merge is
   accepted by this auditor.
3. **Cycle evidence.** Upgrade `0143 → 0147`, downgrade `0147 → 0143` while empty,
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
| `0147` | P19-T04 approval | allocated 2026-11-09, not merged |
| `0146` | P15-T08 reply evidence | allocated 2026-11-09, not merged |
| next free | — | `0147` |

## Renumber note (2026-11-09)

- The first allocation gave this task `0146`. P15-T08's reply-evidence request then took the
  same number, and `0146` stayed with P15-T08 because it had not written its file yet.
- The auditor then wrongly pointed at `0144` as free. It is not: `0144_contact_channel_latency`
  is a committed revision on `dev` (`0143 → 0145 → 0144 → 0142`, head `0143`), and reusing the
  identifier would have produced a duplicate and a broken graph. The executor caught it.
- Final assignment: **`0147`**, parent `0143`. No rehearsal exists at this revision, so the
  full cycle must be run from scratch: `0143 → 0147`, empty downgrade `0147 → 0143`, refused
  downgrade with approval rows present.
