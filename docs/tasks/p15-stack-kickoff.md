# P15 stack kickoff — order, shared contract and what must not be built twice

Claimed 2026-11-04 by the local auditor for a remote executor. One branch
`feat/p15-inbox-base`, four tasks, delivered as one stack.

## Why one stack

`P15-T01` writes messages into `unified_messages`, which is created by `P15-T02`,
which is `TODO`. Building the connector first would mean inventing a store that
T02 then rewrites. T03 and T04 read that store. Four separate PRs would review
three trees that cannot ship on their own.

## Order (strict)

1. **P15-T02** — models, migration, ingestion service. Everything else depends on it.
2. **P15-T01** — IMAP sync feeding the ingestion API.
3. **P15-T03** — reconciliation over real stored messages.
4. **P15-T04** — inbox UI/API on top of T02 and T03.

Inside T02 land the models and the ingestion service before any adapter; a
half-built adapter on a moving model is the expensive kind of rework.

## Migration ownership

`P15-T02` owns the only migration in this stack. It is `0137` from `dev` `0136`.
`P15-T01`, `T03` and `T04` add no revision. If one of them genuinely needs a
column, it amends `0137` before the stack is published, never adds `0138`.

## Shared contract these four tasks must agree on

- Tables: `unified_messages`, `message_threads`, `contact_channel_stats`.
  **No `message_content_cache`** — owner decision D3, settled by D11: metadata only, and
not even a size-limited body cache ([Q-19](../audit/q-19-messaging-decisions.md)). Do not add
  an FTS column, a `tsvector` or a language configuration.
- `unified_messages` unique key: `(tenant_id, connection_id, channel, original_id)` (R-09).
  Ingestion is an idempotent upsert on that key, never a blind insert.
- Every new table inherits `TenantScopedMixin`; every route re-checks tenant and actor
  rights at execution, not only at the edge.
- Ingestion resolves identities by exact identifiers only (email/phone through the
  P13-T07 blind indexes). No fuzzy match, no automatic merge — T03 suggests, a human merges.
- Originals stay in the source system. A message whose original is gone is marked
  `unavailable`, never deleted and never silently refreshed.
- Email networking reuses the pinned TLS policy in `app/integrations/mail_check.py`.
  No new TLS code. `BODY.PEEK` only: a sync must never mark a message read on the server.

## Testing split

Remote runs DB-exempt tests only (`--noconftest`, explicit mocks, no real mailbox,
no live Telegram or MAX). Database work — the migration round-trip, the unique key
under concurrent ingestion, tenant isolation across two workspaces — is handed to
the local auditor with the exact SHA and commands. State that split in the PR body.

## What the auditor will check at hand-off

Migration `0136 -> 0137 -> 0136` on the isolated schema; the unique key holding
under two concurrent ingests of the same message; UIDVALIDITY reset re-scanning
without duplicates; `BODY.PEEK` leaving flags untouched; one workspace unable to
read another's inbox; and that no content cache, FTS column or second migration
appeared.
