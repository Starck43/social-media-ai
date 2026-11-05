# P15 stack audit — round 1

PR [#391](https://github.com/Starck43/social-media-ai/pull/391) (`feat/p15-inbox-base`, head
`a309eb1a`, base `dev` `8bb693d4`), stack `P15-T01..T04` under the binding contract in
[p15-stack-kickoff](../tasks/p15-stack-kickoff.md).

Verdict: **not accepted at `a309eb1a`** (eleven blocking findings; B-0 added after the
first posting, see the note under it). The store, the migration and the isolation
boundary hold; the acceptance path that the contract calls out — UIDVALIDITY reset,
`BODY.PEEK`, unique key under concurrency — breaks in the reset and in the duplicate
handling, and the parts the card requires to exist (job registration, retention,
unavailable) have no producer.

## What was verified as PASS

| Check | Command / probe | Result |
| --- | --- | --- |
| Migration round trip | `0138 -> 0137 -> 0138 -> 0137` on `test_schema` via `migrations/env.py` | tables, `comms.message` registry (VIEW/CREATE/UPDATE/DELETE) and 4 PII triggers created; downgrade removes all of it and leaves `0138` |
| Unique key under concurrency | two `ingest()` calls, same message, separate transactions | one row survives; the loser aborts on a unique violation instead of double-inserting |
| Workspace isolation | identical `original_id` ingested from two tenants | one row per tenant, no leak |
| `BODY.PEEK` | `imap_sync.FETCH_SPEC`, `_fetch_folder_envelopes_sync` | `select(..., readonly=True)`, `BODY.PEEK[...]` only, no `STORE` |
| No content cache / FTS | `0137`, models, migrations | no body column, no `tsvector`, no second migration |
| Exact-identity ingestion | `_resolve_or_create_contact` | blind-index lookup only, no name matching, new contacts unmerged |
| Crash without a message | failed `ingest()` | row count stays 1 |

## Blocking findings

### B-0 — the stack migration carries the wrong number and points backwards

`migrations/versions/0137_unified_messages.py` declares `revision = "0137"` and
`down_revision = "0138"`. The trusted-outbound migration closed at `8bb693d4` is `0138`
(`0138_trusted_outbound_hosts.py:19-20`), so the inbox stack belongs at `0139` on top of
it. As written Alembic reads the new tables as *older* than the trusted-outbound schema,
`alembic history` reports the wrong order, and the stack claims a number for a position
that is already closed. The round trip itself succeeds on an existing schema — that is
why the check below passed — but the revision graph is wrong and must be fixed before
acceptance: rename to `0139_unified_messages.py`, `revision = "0139"`,
`down_revision = "0138"`. Nothing else in the stack depends on the number.

### B-1 — the reset de-duplication is not a de-duplication

`app/integrations/email/imap_sync.py:508` passes `dedupe_by_external_message_id_hash=uv_reset or True`,
which is always `True`. Every incremental sync therefore rebinds by RFC `Message-ID`, not only
the reset. A message that legitimately exists in two selected folders (`INBOX` and `Archive`)
collapses into one row and the second copy's `original_id` is rewritten — the store stops
being "one row per `folder:uid`", and the reset path, the only thing the flag was for, is
indistinguishable from ordinary traffic.

### B-2 — two messages with one `Message-ID` abort the whole connection sync

`app/services/communications/ingest.py:575-585` resolves the hash match with
`scalar_one_or_none()`. Resent or forwarded mail and cross-folder copies share a
`Message-ID`; the second one raises `MultipleResultsFound` inside the sync loop, which fails
the connection and, through `app/jobs/job_handlers/email_sync.py:34-58`, every other
connection in the same transaction.

### B-3 — messages without `Message-ID` duplicate on every reset

`ingest.py:575` requires `ext_msg_hash is not None`; a NULL `Message-ID` skips rebinding, and
`imap_sync.py:490` then writes a new `original_id` (`folder:new_uidvalidity:uid`), inserting a
second row for a message already stored. The kickoff's "UIDVALIDITY reset re-scanning without
duplicates" fails exactly here.

### B-4 — an unparseable fetch reply permanently skips a message

`highest_uid` (`imap_sync.py:475, 517`) advances only over envelopes whose `UID` parsed
(`imap_sync.py:335-340`). A reply without a parseable `UID` is dropped silently and the next
`SEARCH UID n:*` starts above it, so that message is never fetched again. The cursor has to
advance to the highest **candidate** UID of a successful batch, not the highest parsed one.

### B-5 — no job produces a sync

`comms.email.sync` has a handler (`app/jobs/job_handlers/email_sync.py`) but is absent from
`HANDLERS` (`app/jobs/handlers.py:2153`), and nothing schedules it. P15-T01 item 2 requires the
job; as delivered the connector never runs in any deployment.

### B-6 — retention is written but never called

`prune_expired_messages` (`app/services/communications/ingest.py:829`) and
`mark_message_unavailable` (`ingest.py:802`) have no caller outside the package
`__init__`. P15-T02 item 5 requires retention registration, and D24/TZ 2.5 require deleted
originals to be marked `unavailable`. A message deleted on the server stays "available" with
a dead reference, and stored metadata is kept forever.

### B-7 — the route gate uses the wrong resource

`app/api/v1/endpoints/communication.py:186` gates the whole inbox with
`require_model_perm("contact", VIEW)`; the mutations use `("contact", UPDATE)` (lines 268, 288,
308, 342, 372). D14 registers `comms.message` with its own actions, and the services do check it
(`app/models/managers/unified_message_manager.py:14`), but a role holding `comms.message.view`
without `contact.view` is refused by the router while a role holding `contact.view` but not
`comms.message.view` passes the edge and is refused deeper. The router must require
`comms.message` for message routes.

### B-8 — cross-channel identity binding happens without a human

`ingest.py:329-331` accepts an identity with `connection_id IS NULL` regardless of which
connection created it, so an email `Message-ID` sender adopting a contact first seen through
Telegram is an automatic cross-channel merge. The contract keeps cross-channel joining with
T03, where a human confirms. Identity lookup must stay inside the connection unless a
recorded decision says otherwise.

### B-9 — the merge moves rows across connections

`app/services/contacts/reconcile.py:426-453, 511-517` moves every `UnifiedMessage` and
`MessageThread` of the source contact without regard to `connection_id`, while
`message_threads` is keyed by `(tenant_id, connection_id, channel, thread_key)`
(`migrations/versions/0137_unified_messages.py:102`) and the inbox groups by thread. After a
merge a thread can hold messages from two connections under one connection's counters, so
`message_count`/`unread_count` and the per-connection view disagree. Either the card states the
invariant "a merged thread belongs to the target's connection" and the merge repairs counters,
or threads are split per connection.

### B-10 — the "open in chat" handoff always 404s

`app/web/templates/web/communications.html` links `/app/chat?context_type=communication&context_id=<message>`,
and `app/services/knowledge/workspace.py:86-104` resolves it against `knowledge_snippets`, where
unified messages are never written. T04 item 4 (evidence drawer handoff) is unreachable.

## Non-blocking

- **N-1** `_fetch_folder_envelopes_sync` re-ingests already-stored envelopes when a later
  envelope fails (the cursor is written once per folder); harmless under a correct R-09 dedupe,
  expensive otherwise.
- **N-2** The read-state is one-way: the R-09 branch (`ingest.py:544-572`) never updates
  `is_read`, so a message read in the mailbox stays unread here.
- **N-3** `mark_thread_read` fabricates unread state (`app/services/communications/inbox.py:451-455`):
  unmarking reports one unread message even when all are read.
- **N-4** `app/web/communications.py:56-57` and `app/web/contacts_merge.py:22-26` swallow every
  exception, including `PermissionError`, rendering an empty inbox instead of a denial.
- **N-5** The migration creates `model_types` and `permissions` for `comms.message` but no
  `role_permission`/`workspace_role_permission` presets; only the runtime catalog does. Confirm a
  role sync runs on deploy, or the new resource is grantable but granted to nobody.
- **N-6** No test exercises the reset path against two messages sharing a `Message-ID`, the NULL
  `Message-ID` case, or the unparseable-`UID` case — the three above would have failed.

## What the executor must deliver next

Fix B-0..B-10 on the same branch, with tests for each: two folders holding one message,
`Message-ID` shared by two messages, NULL `Message-ID` across a reset, an unparseable FETCH
reply, a registered and scheduled `comms.email.sync`, a retention sweep wired into `handle_prune`,
`unavailable` on a deleted original, the route gate on `comms.message`, connection-scoped
identity lookup, cross-connection merge, and the chat handoff (or its removal, recorded).
