# P14 — Bitrix24 CRM integration (TZ module 1)

[Product direction](../product/PRODUCT_PLAN.md#business-modules) · [Adapted specification and review](../product/BUSINESS_MODULES.md) · [ROADMAP](../../ROADMAP.md#p14)

**Phase status: IN_PROGRESS.** Core T01–T06 are accepted as of 2026-11-08; the separately deferred on-premise authorization task P14-T08 remains tracked in ROADMAP. Registration: [P13-T01 accepted](../audit/p13-t01.md).
Owner estimate (TZ): 3–4 weeks — not a repository commitment.

**Goal:** Connect Bitrix24 portals, keep contacts/companies/leads/deals synchronized, show upcoming birthdays and anniversaries with AI greeting drafts, then — after the communication hub — send approved greetings and consented batch messages.

## Baseline: done and arriving

- Accepted core: Bitrix24 connection, incremental sync, encrypted contact cards, upcoming events and greeting drafts ([T01–T04](../audit/p13-t04-p14-t04-business-crm.md)); approved greeting delivery and consented batch messaging through the P15 outbox ([T05](../audit/p14-t05.md), [T06](../audit/p14-t06.md)).
- Depends on P13 (PII, contact core, rate limits, OAuth, commands). Sending depends on [P15-T05](../tasks/p15-t05.md)/T06.

## Adapted requirements

TZ 1.1–1.4, adapted:

- **Connector:** OAuth 2.0 with refresh (decision D2 keeps the webhook as an admin alternative); contacts, companies, leads, deals; incremental sync every 15 min by `DATE_MODIFY` watermark; optional real-time event handlers (R-14); 2 req/s per portal with `batch` (R-11).
- **Contacts:** imported into the contact core as identities (R-02, R-16); birthday, anniversary and allowlisted custom fields encrypted ([P13-T02 accepted](../audit/p13-t02.md)); duplicates surfaced for [P15-T03](../tasks/p15-t03.md) merge.
- **Greetings:** reminders 7/3/1 days ahead; AI drafts; history prevents repeats; scheduled sending with approval; delivery via email/Telegram bot/MAX bot.
- **Mass messaging:** selected contacts, channel choice, `{name}`/`{company}` personalization, delivery tracking, retry, unsubscribe; consent basis and channel limits per R-10/D7.

## Data model (adapted)

TZ `bitrix_connections` → `workspace_connections` + secrets; `bitrix_contacts` → `crm_entities` + `contact_identities`; `greeting_history` kept (integer keys, encrypted text, unique per contact/event/year).

Conventions: integer keys, `TenantScopedMixin`, `settings.DB_SCHEMA`, Alembic revision = next free after rebasing on `dev` (R-01, R-20).

## API (adapted)

Kept as in TZ under `/api/v1/integrations/bitrix/…`: `connect`, `connections`, `connections/{id}`, `contacts`, `contacts/{id}`, `contacts/sync`, `greetings/upcoming|draft|send`, `messaging/batch`, `messaging/status/{batch_id}` (integer IDs).

## Acceptance

TZ criteria with adaptations: OAuth connection (T01); automatic 15-min sync (T02); upcoming birthdays 7/3/1 days (T04); AI personalized greetings (T04); sending via selected channel (T05, after P15); history without repeats (T04); batch for 100+ contacts with consent/unsubscribe (T06); personal data Fernet-encrypted (T02 via [P13-T02 accepted](../audit/p13-t02.md)); ≤ 2 req/s (T01).

## Plan

1. [P14-T01 accepted](../audit/p13-t04-p14-t04-business-crm.md) — Bitrix24 connection: OAuth 2.0 portal authorization and health · `P2` · `C3` · depends: [P13-T08 accepted](../audit/p13-t04-p14-t04-business-crm.md), [P13-T03 accepted](../audit/p13-t03.md)
2. [P14-T02 accepted](../audit/p13-t04-p14-t04-business-crm.md) — Bitrix24 read-only incremental sync of contacts, companies, leads and deals · `P2` · `C4` · depends: [P14-T01 accepted](../audit/p13-t04-p14-t04-business-crm.md), [P13-T07 accepted](../audit/p13-t07.md)
3. [P14-T03 accepted](../audit/p13-t04-p14-t04-business-crm.md) — CRM contact cards and duplicate candidates · `P2` · `C3` · depends: [P14-T02 accepted](../audit/p13-t04-p14-t04-business-crm.md)
4. [P14-T04 accepted](../audit/p13-t04-p14-t04-business-crm.md) — Upcoming events and AI greeting drafts · `P2` · `C3` · depends: [P14-T02 accepted](../audit/p13-t04-p14-t04-business-crm.md)
5. [P14-T05 accepted](../audit/p14-t05.md) — DONE 2026-11-08: consented operator-approved greetings through the existing outbox. Accepted SHA `c125cbc23e4f70d124f46e438c28f715ce59680d` (commit date 2026-11-08).
6. [P14-T06 accepted](../audit/p14-t06.md) — DONE 2026-11-08: personalized consented campaigns, durable replay and public one-click unsubscribe. Accepted SHA `c125cbc23e4f70d124f46e438c28f715ce59680d` (commit date 2026-11-08).

## Dependencies and gates

T01–T04 can complete before P15 (read-only + drafts). T05/T06 are `P3` until the hub outbox and senders exist. Testing: Bitrix24 sandbox portal (owner-provided, optional) and recorded mocks; no live writes in tests.
