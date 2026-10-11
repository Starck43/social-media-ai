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

## Owner guard correction (same Draft #101)

Owner reports original as-is head `f4c92edd`, runner `7aabd199…`, EXIT1 and
0/10 at first admission; fixture creation/cleanup succeeded, no leftovers,
external baseline intact, sequences advanced. No shim/rerun/local correction.
The hidden original cause was not inspected directly. Offline actual compiler
and callback reproduced a guard defect consistent with this failure: VALUES
binds are `_mN`, not plain ownership names. Prefetched row-zero defaults use
plain names; compiler string labels are also supported by strict normalization.

Runner-only decoding verifies each complete row independently, with actual
INSERT metadata/column/default checks; malformed/foreign/mixed-row fences still
reject. Production privacy boundary `raise ... from None` remains unchanged:
Python preserves `__context__`. Owner-only diagnostics traverse it and print
only static guard reasons/allowlisted category, known SQLSTATE and repository
line, not original error/SQL/params/locals. NEW 13 offline compiler/callback/
privacy regressions passed on `f4c92edd` + runner/helper/test artifact `9f372086eb2d022c97bacccced535ee99616fd0b41026cc1889a8a916f6afecd`.

Corrected runner `596b9dda5aae215ed1a1c9d87c58231a750de8e1a7f29bfa2383e2222fa9823a` and helper `826e474d46b29dc41f542ccd3b178fd19f623853751f75bbb2d7d7b7a0649e62` are NOT driver-tested.
One pinned sequential rerun of these 10 new cases is authorized by the existing
L1-specific fixture approval; no old tests, production changes, schema work or
#100 activation. Driver acceptance remains pending.

## Owner driver acceptance — bounded slice only

Owner approved one as-is retry on `bd8eb2b42564f45e603431886f537cb12b5cf89f`:
[10/10 EXIT0 acceptance](https://github.com/Starck43/social-media-ai/pull/101#issuecomment-6103751755).
Runner `596b9dda5aae215ed1a1c9d87c58231a750de8e1a7f29bfa2383e2222fa9823a`
and helper `826e474d46b29dc41f542ccd3b178fd19f623853751f75bbb2d7d7b7a0649e62`
independently match published bytes. Original as-is `f4c92edd` 0/10 remains
separate failed evidence, not rewritten as acceptance.

Owner reports 13 tagged creation/admission/cleanup commit acknowledgements,
17 rollbacks and two distinct PostgreSQL backends including independent
precommit visibility. Scoped cleanup/read-only post-check found zero tagged
leftovers and restored external test_schema baseline (tenants4/sources0/raw0/
jobs0/analytics0); public/schema unchanged. Sequences only advanced:
tenants146→148, sources3→4, rawNULL→7, jobs32→36, analytics2→3.
The two safe diagnostic lines belonged to intentionally negative queue and
foreign-tenant cases; both passed. No shim, unit/old-suite rerun, DDL/bootstrap/
migration/provider/dispatcher execution.

Execution, environment and owner-local log remain owner-reported; the linked
comment/source hashes were inspected, not the local log file or server itself.
This accepts the ten scoped storage/admission/duplicate/concurrency checks,
not production timing, provider fencing, historical repair, retention policy,
or whole L1/L2/PRD gate closure. No further owner test task. Final closeout is
three existing docs only; all app/test/runner/helper bytes retain the exact
owner-tested revision. Fresh heads/compatibility/checks still precede merge.
