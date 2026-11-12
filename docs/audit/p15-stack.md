# P15 stack audit — round 1

PR [#391](https://github.com/Starck43/social-media-ai/pull/391) (`feat/p15-inbox-base`, head
`a309eb1a`, base `dev` `8bb693d4`), stack `P15-T01..T04` under the binding contract in
[p15-stack-kickoff](../archive/tasks/p15-stack-kickoff.md).

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

## Round 2 — reviewed at `f972b6b1` (2026-11-05)

Delta: `83e66841..f972b6b1` (one commit, `fix(p15): address round-1 audit findings
B-0..B-10 and N-1..N-6 on 0139`). Round 1's record stays as written.

Verdict: **not accepted at `f972b6b1`**. Round 1's findings are answered and five of the
six checks I could run against the real schema now pass. Round 2 found two correctness
regressions in the new merge/expunge code, one durability gap, and one registry collision
that has to be resolved before this branch can land at all.

### Round 1 findings: closed

| Finding | How it is now |
| --- | --- |
| B-0 | `0139` on top of `0138`; `0138 -> 0139 -> 0138` twice is clean and restores tables, triggers, `comms.message` and the workspace presets (owner/admin 4, manager 3, analyst/viewer 1) |
| B-1 | dedupe flag is `uv_reset`, not `uv_reset or True`; a message in `INBOX` and `Archive` keeps two rows (`test_b1_*`) |
| B-2 | the hash lookup returns a chosen candidate, never `scalar_one_or_none`; a duplicate `Message-ID` re-binds instead of aborting |
| B-3 | a message without `Message-ID` re-binds on the header `content_hash` (`test_b3_*`) |
| B-4 | the cursor advances to the batch's highest candidate UID on a successful batch only (`test_b4_*`) |
| B-5 | `comms.email.sync` is in `HANDLERS`, in `DEFAULT_TASKS` (`*/15`) and in the dispatcher labels |
| B-6 | `prune` sweeps `unified_messages` (7 aged rows deleted on the real schema); IMAP expunge marks `unavailable` |
| B-7 | every communication route gates on `comms.message` |
| B-8 | identity lookup stays inside the connection; a same-connection repeat reuses the contact (contacts 2 -> 2) |
| B-10 | `knowledge/workspace.py` resolves `context_type='communication'` |

Probes on `test_schema` at `0139`: concurrent ingest of one message -> 1 row (loser on the
unique key); the same `original_id` in two workspaces -> 1 row each; reset re-binds
`INBOX:1:10 -> INBOX:2:11` as `created=False, uid_rebound=True`; NULL `Message-ID` re-binds
`INBOX:2:50 -> INBOX:3:51` through `content_hash` with no new row; `prune` deleted 7 aged
messages and left the rest. `39 passed` locally for the four suites.

### New blocking findings

**B-11 — unmerge can delete a thread the target already owned.**
`app/services/contacts/reconcile.py:641-651` appends `dest_th.id` to both
`created_thread_ids` and `moved_thread_ids` **after** `threads_by_conn_key.get(conn_key)`
may have returned an *existing* target thread. `unmerge` then runs
`DELETE FROM message_threads WHERE id IN created_thread_ids` (`:911-918`), so a pre-existing
thread disappears and its messages keep a dangling `thread_id`. The test at
`tests/contacts/test_p15_t03_reconcile_unit.py:699-750` misses it only because its fixture
keys do not actually collide. Fix: keep "created by this merge" separate from "re-bound to",
and never delete a thread that existed before the merge.

**B-12 — re-binding keeps the source thread's key, which ingest can never regenerate.**
`reconcile.py:630-639` builds the destination thread with `thread_key=cur_th.thread_key`
while changing `channel` to `msg.channel`. An email message re-bound out of a Telegram
thread becomes `(connection, "email", "telegram:chat:777")`; `ingest` derives keys per
channel, so the next real email for that conversation opens a second thread. Fix: re-derive
the key with the same helper ingest uses, or refuse cross-channel re-binding.

**B-13 — a failed connection discards the earlier connections' committed work.**
`app/jobs/job_handlers/email_sync.py:53-69` runs every connection in one transaction and one
session. `sync_connection` flushes `conn.config` (cursor) inside its loop
(`app/integrations/email/imap_sync.py:588-601`), so a later folder raising leaves partial
cursor writes in the transaction. A failure in connection *n* also drops connection *n-1*'s
rows, which were already ingested. Fix: a savepoint per connection.

**B-14 — a transient empty `SEARCH` reply marks a whole folder unavailable.**
`imap_sync.py:322-324` returns `present_uids=set(), all_batches_ok=True` when the server
answers `OK` with no data; the expunge test at `:635-639` then sees every stored message
"missing" and marks the folder's backlog `unavailable`. Fix: an empty reply is not evidence
of deletion — skip marking and flag the folder as incomplete.

### New non-blocking

- **N-7** per-folder cursor progress is only persisted through the final
  `conn.config = config` (`imap_sync.py:668-670`); an abort in a later folder loses the
  earlier folders' progress even though their rows are ingested.
- **N-8** a message moved between folders shows as `unavailable` in the old folder *and*
  available in the new one — acceptable under TZ 2.5, worth stating in the card.
- **N-9** `mark_thread_read`/re-sync make read state one-way (clearing `\Seen` on the server
  never returns a message to unread). Document it or fix it.
- **N-10** `access_catalog.py`, `app/models/bootstrap.py`, `app/tasks/bootstrap.py`,
  `app/jobs/handlers.py`, `app/jobs/dispatcher.py`, `cli/commands/pii.py`,
  `app/services/knowledge/workspace.py` and `tests/jobs/test_p11_t13_handler_seams.py` are
  outside every P15 card's owned paths and land in one commit. The cards ask for a separate
  small commit; the PR body should name each one and why.

### Registry collision — must be fixed before landing

The branch also moves `P15-T06` from `TODO`/unassigned to `IN_PROGRESS`/Notion AI
(`ROADMAP.md:78`), which belongs to another lane's claim. Remove it from this branch; the
auditor reconciles the registry, and a task branch may not.

### What the executor delivers next

B-11, B-12, B-13, B-14 with one test each — in particular a merge/unmerge round trip where
the destination thread already exists, a cross-channel re-bound message followed by a
normal ingest of the same conversation, a two-connection job where the second fails, and an
empty `SEARCH` reply. Then drop the `P15-T06` line and state the shared-path files in the PR.

## Round 2 — state check (2026-11-05, after the review)

PR #391 is still **open and unmerged** at `f972b6b1`; round 2 has not been answered.
`ROADMAP.md` keeps `P15-T01..T04` at `READY`, which is the correct status for work whose
audit found open blockers.

One correction to the round-2 record: the `P15-T06` claim in `ROADMAP.md:78` is **not** a
collision. The line names its own owner (Notion AI) and a different dialogue, and that lane
is landing on `feat/p15-t06-channel-senders`. The finding stands only in one respect — a
task branch edits a row it does not own. Whoever resolves the branch should keep the
`P15-T06` claim intact when rebasing, not revert it.

## Round 3 — auditor fixes at `0a55061d` (2026-11-05)

Round 2 stayed unanswered, so the auditor made the remaining fixes in the branch itself
rather than leaving the stack idle. Nothing about the review standard changed; two round-2
findings were corrected against the code.

### Corrections to the round-2 record

- **B-11 withdrawn.** `reconcile.py:641-651` records `dest_th.id` as created only inside
  `if dest_th is None`, so a thread the target already owned before the merge is never
  deleted on unmerge. The round-2 claim came from reading
  `threads_by_conn_key.get(conn_key)` as a hit when that path had already returned `None`.
  The only real defect left was the duplicate entry in `moved_thread_ids`, now removed.
- **B-14 cause restated.** `imap_sync.py:322` folds "no data" into the error branch, so the
  caller is told `all_batches_ok=True` and the expunge check clears the folder's backlog.

### Fixes

| Finding | Change |
| --- | --- |
| B-12 | `_thread_key_for_message()` re-scopes a foreign-channel key under the message's own channel before the re-bound thread is created; unmerge restores `thread_key` from the journal |
| B-13 | `job_handlers/email_sync.py` wraps each connection in `session.begin_nested()`, committing or rolling back per mailbox |
| B-14 | an `OK` reply without data returns `all_batches_ok=False`; the expunge check needs one complete, non-empty scan |
| N-1 | a partial scan keeps `last_uid` at 0 and stays `uidvalidity_reset_pending` |
| registry drift | `ROADMAP.md`, `docs/CHANNELS.md`, `docs/tasks/p15-t06.md`, `docs/tasks/p16-t01.md` and a P20 card were carried from before the T06 lane landed; they now match `dev` (`61e2840e`) |

### Evidence at `0a55061d`

- `pytest tests/integrations/test_p15_t01_imap_unit.py tests/contacts tests/communications -q`
  → **94 passed**.
- The B-14 test fails with the previous `imap_sync.py` reverted, so it exercises the guard.
- `test_schema` `0139 -> 0138 -> 0139`, twice: version and the three tables round-trip.
  The audit tenants `audit-p15-a` (8) and `audit-p15-b` (9) and their rows are removed;
  `unified_messages`, `message_threads` and `contact_channel_stats` are empty.
- No new migration, no route, no content column; `0139` untouched.

Still open, none blocking: N-7 (earlier folders' cursor progress lost when a later folder
returns nothing), N-8 (a moved message shows unavailable *and* available — acceptable under
TZ 2.5), N-9 (read state is one-way), N-10 (shared files listed in the PR body).

## Acceptance — `9fe76fe3` (2026-11-05)

Accepted and merged into `dev` as `9fe76fe3` ("unified inbox stack — T01..T04 with 0139 on
0138"), after the branch was merged with `dev` (`3db24a36`) and the one conflict
(`docs/CHANNELS.md`, both sides the P15-T06 sender section) resolved to `dev`'s version.

Checks on the merged tree:

- 94 focused tests pass (`tests/integrations/test_p15_t01_imap_unit.py`, `tests/contacts`,
  `tests/communications`); `tests/jobs/test_p11_t13_handler_seams.py` passes.
- `0139` sits on `0138`; `test_schema` round-trips `0139 -> 0138 -> 0139` twice with the
  three tables intact (round 3).
- The audit's synthetic tenants (`audit-p15-a` 8, `audit-p15-b` 9) and their rows are gone
  from `test_schema`.

Accepted with these open, non-blocking items: N-7 (cursor of earlier folders survives a
later empty folder only after the next full pass), N-8 (a message moved between folders shows
`unavailable` in the old one and available in the new one — TZ 2.5), N-9 (read state is
one-way), N-10 (shared entrypoint files land with the stack; listed in the PR comment).

`tests/security/test_access_policy.py::test_migration_catalog_and_presets_match_runtime_without_database`
fails on `dev` before this merge as well; recorded as a pre-existing red, not caused by `0139`.
The four ROADMAP rows are removed by the auditor; the four task cards stay as their record.
