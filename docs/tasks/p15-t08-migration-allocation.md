# Reply-evidence migration `0146` — allocation record for P15-T08

Registry: [ROADMAP](../../ROADMAP.md#priority-p3); the task row and owner live there.
Card: [P15-T08](p15-t08.md) · Request: PR [#448](https://github.com/Starck43/social-media-ai/pull/448) comment 2026-11-09

- **Revision:** `0146`
- **Parent:** `0143` (`down_revision = "0143"`)
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
   rights, no queue, no attachment columns; attachments stay in [P15-T14](p15-t14.md).
3. **One head.** `0146` leaves exactly one Alembic head.
4. **Cycle evidence** on an isolated schema: `0143 → 0146`, empty downgrade `0146 → 0143`, and a
   refused downgrade with evidence present.
5. **`reply_evidence_required` is not authorization.** Direct writes or a flipped flag never grant D15;
   the service rechecks rights and evidence at dispatch.
6. **No public-schema migration** without separate owner authorization.

## Reservation register

| Revision | Task | State |
|---|---|---|
| `0143` | P16-T02 | spent, in `dev` |
| `0146` | P15-T08 reply evidence | allocated 2026-11-09, not merged |
| next free | — | `0147` |

## Collision resolution (2026-11-09)

- P19-T04 was allocated `0146` shortly before this record. This task kept `0146` because it
  had not written its migration file yet; P19-T04 moved on to `0144` and then to `0147`
  (see its allocation record). `0146` is not reused for anything else.
- `0146` here is fixed: no other task may take it. If this task is abandoned, report it and
  the number is returned to the auditor instead of being reassigned silently.
