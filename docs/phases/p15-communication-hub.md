# P15 — Unified communication hub (TZ module 2)

[Product direction](../product/PRODUCT_PLAN.md#business-modules) · [Adapted specification and review](../product/BUSINESS_MODULES.md) · [ROADMAP](../../ROADMAP.md#p15)

**Phase status: IN_PROGRESS** (P15-T06 accepted 2026-11-07; remaining work is governed by ROADMAP).
Owner estimate (TZ): 5–6 weeks — not a repository commitment.

**Goal:** Combine conversations from Telegram, MAX and email into one inbox per contact, reconcile identities, recommend the right channel and send approved, scheduled, multi-channel messages with delivery tracking.

## Baseline: done and arriving

- In `dev`: `/app/communications` and `/app/followups` workspaces with a pure outbox state machine, approval binding and quiet hours (P12-T04, P12-T09); Telegram/MAX channel transports and listener; email connections with IMAP login check only (P12-T08); P9 Telegram channel-post capability; evidence drawer and chat handoff (P12-T06).
- Arriving via open PRs: P12-T14 ([PR #308](https://github.com/Starck43/social-media-ai/pull/308)) keeps personal Telegram runtime OFF; personal Telegram ingestion waits for it.
- Missing: message persistence, email sync/SMTP, identity reconciliation, persistent outbox and senders.

## Adapted requirements

TZ 2.1–2.5, adapted:

- **Identity reconciliation:** by email/phone/username through blind indexes; suggestions with reasons; manual merge/unmerge with journal; conflict resolution ([P15-T03](../tasks/p15-t03.md)).
- **Unified inbox:** thread view, filters (channel, date, read state), search (metadata always; content per D3), chronological order, read/archive, local delete, reply ([P15-T04](../tasks/p15-t04.md), T08).
- **Channel recommendation:** frequency, last active channel, manual preference, optional AI hint, confidence score ([P15-T07 audit](../audit/p15-t07.md)).
- **Outbox & scheduling:** drafts with approval, scheduled sending, one text → several channels, delivery tracking, retry only on definite transient failure, cancel ([P15-T05](../tasks/p15-t05.md)/T06).
- **Storage strategy:** metadata in `unified_messages`, optional content cache, originals stay in source systems, periodic sync, deleted originals → `unavailable` ([P15-T02](../tasks/p15-t02.md)).

## Data model (adapted)

`unified_contacts` → contact core ([CONTACTS](../business/CONTACTS.md); [P13-T07 audit](../audit/p13-t07.md)); `unified_messages` (unique `(tenant_id, connection_id, channel, original_id)`, R-09); `message_threads`; `message_content_cache` (D3, TTL); `contact_channel_stats`; `scheduled_messages` → `outbox_messages` + `outbox_targets`.

Conventions: integer keys, `TenantScopedMixin`, `settings.DB_SCHEMA`, Alembic revision = next free after rebasing on `dev` (R-01, R-20).

## API (adapted)

As in TZ under `/api/v1/communication/…`: `contacts`, `contacts/{id}`, `contacts/merge|unmerge`, `inbox`, `inbox/threads/{id}`, `inbox/mark-read|archive`, `contacts/{id}/recommend-channel`, `outbox/draft|schedule`, `outbox/scheduled`, `outbox/scheduled/{id}`, `threads/{id}/reply`.

## Acceptance

TZ criteria: contacts merged by email/phone (T03); chronological per-contact view (T04); recommendation with confidence (T07); manual merge/unmerge (T03); scheduled sending on time (T05); multi-channel sending (T05/T06); full-text search (T04, per D3); thread grouping (T02/T04); reply to the right channel (T08); delivery status (T05).

## Plan

1. [P15-T01](../tasks/p15-t01.md) — Email connector: IMAP incremental read sync · `P3` · `C4` · depends: [P13-T03 accepted](../audit/p13-t03.md), [P13-T07 accepted](../audit/p13-t07.md)
2. [P15-T02](../tasks/p15-t02.md) — Unified message store and channel ingestion (Telegram, MAX, email) · `P3` · `C4` · depends: [P13-T07 accepted](../audit/p13-t07.md), P12-T14 integrated (Telegram personal runtime gates)
3. [P15-T03](../tasks/p15-t03.md) — Identity reconciliation: suggestions, manual merge and unmerge · `P3` · `C4` · depends: [P15-T02](../tasks/p15-t02.md), [P14-T03 accepted](../audit/p13-t04-p14-t04-business-crm.md) (CRM duplicate candidates, optional)
4. [P15-T04](../tasks/p15-t04.md) — Unified inbox: threads, filters, search and read state · `P3` · `C4` · depends: [P15-T02](../tasks/p15-t02.md), [P15-T03](../tasks/p15-t03.md)
5. [P15-T06 accepted](../audit/p15-t06.md) — **DONE 2026-11-07**, source `b9eb76d5f0a3c12a6349ec6d2a94de756f413ef5`: default-off customer SMTP and private bot senders, honest outcomes and guarded package validation.
6. [P15-T05](../tasks/p15-t05.md) — Persistent outbox, scheduling and delivery tracking — **accepted 2026-11-06** ([audit](../audit/p15-t05-0141.md), revision `0141`) · `P3` · `C4`
7. ~~P15-T07 — Channel recommendation with confidence score~~ **DONE 2026-11-08**, accepted source `8afafd93`, PR #438 — [audit](../audit/p15-t07.md)
8. [P15-T08](../tasks/p15-t08.md) — Reply from the unified interface · `P3` · `C3` · depends: [P15-T04](../tasks/p15-t04.md), [P15-T05](../tasks/p15-t05.md), [P15-T06](../audit/p15-t06.md)
9. [P15-T09](../tasks/p15-t09.md) — Register D14 `comms.outbox` in the central permission catalog and presets · `P3` · `C2` · depends: [P15-T05](../tasks/p15-t05.md), [P15-T06](../audit/p15-t06.md)

10. [P15-T11](../tasks/p15-t11.md) — Restore the red channels test suite · `P2` · `C2` — **DONE 2026-11-07**, PR #429 (`57de19ea`); two FloodWait reds stay by owner decision and move to [P15-T12](../tasks/p15-t12.md)

11. [P15-T12](../tasks/p15-t12.md) — Restore Telegram FloodWait deferral in the guarded runtime · `P2` · `C3` — **DONE 2026-11-07**, PR #432 (`f3d28601`)

12. [P15-T13](../audit/p15-t13.md) — Preserve the personal Telegram source owner · **DONE 2026-11-07**, PR #437 (`2e1bdeb5`).

13. [P15-T14 accepted](../audit/p15-t14.md) — **DONE 2026-11-10**, merge `f47e0815` (source `31d27da8`), PR #451: attachment delivery at the outbox seam — transient spool references bound into the approval hash (D9a), migration `0150`, fail-closed stitch/dispatch refusals.

## Dependencies and gates

Email connector is the TZ's "future module" and is delivered here (T01/T06). Personal Telegram ingestion waits for P12-T14. Mock TG/MAX APIs and test mailboxes only.
