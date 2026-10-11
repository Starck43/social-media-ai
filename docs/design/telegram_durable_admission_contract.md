# Telegram L1/L2 durable admission

## Bounded contract

A fetched post is acknowledged only after a committed raw admission (or an exact
already-saved content-hash receipt), not after an LLM response. Source high-water
marks are bookkeeping, not evidence that every lower message was admitted.
Unseen lower IDs must still stage; existing raw identities are immutable.

Both paths use one canonical staging-row builder and one source-locked admission
transaction. The transaction checks explicit tenant/source/active ownership,
records the raw snapshot, verifies every identity/hash, updates the monotonic
source cursor, and, for a new L1 raw item, creates an existing `analyze` job in the
same transaction. Count estimates from legacy `store_items` are not receipts.
A commit/insert/queue/ownership failure leaves the Bot API update unacknowledged.
Cancellation is not a success. No analysis/provider/send runs in admission.

L1 captions and media-only posts preserve image/video/unknown placeholders with
no Bot API file IDs, file URLs, tokens, or downloads. Partial text/media coverage
uses existing analyzer/hash retirement: useful text may save, unresolved parent
hashes remain staged. A duplicate pending raw identity does not reset attempts,
replace attachments, or create another job; saved parent hashes also suppress
re-admission. Dedup receipts last only while the raw/analysis retention policy
keeps them: this is not an eternal exactly-once or paid-attempt ledger.

L2 fetch does not write cursors. Only durable admission advances its pull cursor.
`Source.params.telegram_l2_last_item_id` separates pull progress from L1 arrivals;
initialization preserves the previous legacy cursor, never silently backfills.
The shared `last_item_id` is a monotonic display/admission maximum. Bounded L2
pulls use oldest-first order above their cursor so a limit cannot jump over older
eligible messages. Historical cursor holes and edits are not repaired here.

Bot API poll advances its in-memory offset after the consumer resumes the yield.
The listener closes a failed iterator and re-polls; ingest errors are static,
not swallowed. Newly supported caption/media channel posts do not activate the
chat/reply route; existing text-channel routing remains unchanged. Intentional unsupported/unmonitored/digest-target updates may be
acknowledged without collection. Ambiguous source ownership fails closed.

## Reuse and limits

L1 jobs contain only the exact source ID and use `handle_analyze`, existing queue
claim/uncertainty policy, staging replay and hash retirement. No AgentTask/action
is attached; this does not activate a sender. No new downloader, provider,
backfill, schema, migration, retry reset, or historical hash repair is introduced.
Multiple independent analysis tasks/provider effects are not source-fenced by
this admission lock. Existing bounded replay windows, attempt ceilings, partial
same-day collision and raw retention remain separate contracts. A failed wakeup
is visible in the existing queue; it does not discard the raw row or silently
create a replacement for an uncertain job. A default inactive scenario leaves
raw content staged for explicitly configured future analysis.

## Acceptance

NEW isolated checks must cover actual normalization, canonical snapshots,
transaction/commit/queue failure, exact duplicate and lower-ID admission,
monotonic independent cursors, cancellation, delayed poll ACK and iterator drain.
Author preparation: 28 NEW isolated source/task/session/SQL-double checks and
4 NEW actual offline SQLAlchemy/Telethon compilation/signature checks passed on
`adce4fa8` + the exact source artifact (fresh `c0e59dac` has unchanged relevant
app/test-helper bytes before this patch). The 10-case owner runner is prepared,
compiled and refuses before DB imports without explicit approval; NOT RUN.

PostgreSQL acceptance is pending: real JSON/ON CONFLICT, atomic commit visibility,
rollback on queue failure, two concurrent deliveries and tenant boundaries.
Owner execution must be sequential in existing localhost test_schema, with no
bootstrap/DDL/foreign cleanup/provider work. Synthetic fixture COMMIT/cleanup
is now separately user-approved for this L1 package (relayed by chat 2), not
inherited queue permission. It remains owner-local/fixture-scoped, not a general
UPDATE-only COMMIT, bootstrap/schema/live authorization. No driver run has
yet occurred; one sequential pinned handoff follows publication/readback. Source/unit doubles do not certify database durability or production
transport/provider behavior.

A final routing refinement has one NEW actual-listener check on published
`bf0be4e1` + scoped patch (final source artifact `4f74b8bc09f7c60cea2d551d0ca5ced08fdee6de562dbad7f951d10ff35b3130`). It does not move
the earlier 28+4 evidence to a new head. Total 33 NEW preparation checks across
these explicit scopes; owner10 remains NOT RUN.
