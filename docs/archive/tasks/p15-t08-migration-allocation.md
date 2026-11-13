# Reply-evidence migration `0149` — allocation record for P15-T08

Registry: [ROADMAP](../../../ROADMAP.md#priority-p3); the task row and owner live there.
Card: [P15-T08](p15-t08.md) · Request: PR [#448](https://github.com/Starck43/social-media-ai/pull/448) comment 2026-11-09

- **Revision:** `0149`
- **Parent:** `0148` (`down_revision = "0148"`) — P19-T04 approval, merged first
- **Granted:** 2026-11-09 by the auditor, at the owner's instruction to let P15-T08 continue whole
- **Granted to:** `notion-ai-p15-t08`, branch `p15-t08-thread-reply`
- **Base at allocation:** `dev` `77b1a07a`, Alembic head `0143`

## What this number covers

The DDL requested in #448, reviewed and limited to:

1. `outbox_messages.reply_evidence_required BOOLEAN NOT NULL DEFAULT false` — set once at
   creation, never flipped afterwards; `true` blocks scheduling and dispatch when evidence is absent,
   including after the source row is deleted.
2. `outbox_reply_evidence` — encrypted exact `In-Reply-To`/`References` JSON plus the tenant,
   outbox item, thread and inbound message references; `UNIQUE (tenant_id, outbox_message_id)`;
   the three composite foreign keys; the two indexes; the `contact_pii_marker` trigger on the existing
   `bump_contact_pii_marker()`.
3. The model, its PII-rotation registration, and the matching downgrade that refuses while evidence
   rows or `reply_evidence_required = true` rows exist.

## Conditions

1. **Final DDL before the file is written.** Re-send the exact column list (the PR comment truncated
   the `outbox_reply_evidence` column block after `inbound_message_id`) and the auditor confirms it, so
   the migration file matches one reviewed statement rather than a reconstruction of it.
2. **No shared-relation changes beyond item 1.** No `business_commands.payload` overloading, no new
   rights, no queue, no attachment columns; attachments stay in [P15-T14](../../audit/p15-t14.md).
3. **One head.** `0149` leaves exactly one Alembic head.
4. **Cycle evidence** on an isolated schema: `0148 → 0149`, empty downgrade `0149 → 0148`, and a
   refused downgrade with evidence present.
5. **`reply_evidence_required` is not authorization.** Direct writes or a flipped flag never grant D15;
   the service rechecks rights and evidence at dispatch.
6. **No public-schema migration** without separate owner authorization.

## Reservation register

| Revision | Task | State |
|---|---|---|
| `0143` | P16-T02 | spent, in `dev` |
| `0149` | P15-T08 reply evidence | allocated 2026-11-09, not merged |
| next free | — | `0150` |

## Numbering history (2026-11-09)

- `0146` was allocated, then found to collide with the calendar branches that reached `dev`
  (`0146_calendar`, `0147_source_objects`). The original file written against `0146` is
  obsolete and its cycle proves nothing.
- Final assignment: **`0149`**, parent **`0148`** — P19-T04's approval migration, which lands
  in `dev` first. The two tasks chain rather than both taking the head, so neither creates a
  second head.

## Superseded numbering (2026-11-09, owner-directed)

- The numbers in this note are **history, not a reservation**. Under the
  [migration numbering rules](../../process/COLLABORATION.md#migration-numbering-and-parallel-tasks)
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
  [migration numbering rules](../../process/COLLABORATION.md#migration-numbering-and-parallel-tasks)
  a task writes a placeholder revision and the number is assigned at merge.
- Both tasks follow the live `dev` head and ship a placeholder revision. The
  integrator assigns the next free numbers in integration order, rewrites only
  `revision`/`down_revision`, and records the landed number in the audit note.
- Do not ask the auditor for a number and do not wait for one. A green cycle on
  the task's own isolated schema is the evidence; changing only the numbering
  needs no new cycle, only the single-head check.
