# P12-T14 single-PR review entry

The one implementation and audit PR is **#308**, targeting `dev`, using branch
`docs/p12-t14-config-security-plan` (the historical branch name is retained to
preserve the existing PR). Do not audit or merge old slices in sequence. All new
P12-T14 work is published to this PR; separate working branches are implementation
checkpoints, not additional audit requests. #308 stays draft until the complete
scope and checks are ready for independent review. No merge is authorized.

## Review map

1. **Credential storage and renewal:** user credential model/manager, VK OAuth
   refresh transaction fence and encrypted refresh material; migration 0124.
2. **Trust and safe diagnostics:** VK OAuth URL encoding and endpoint allowlist,
   fixed OAuth error guidance, renewal_unverified/operator vs personal action
   statuses, source preflight and personal notifications.
3. **Operations:** read-only source/owner inventories, guarded unreadable-key
   recovery and offline credential-key rotation. Apply/rotation still require
   explicit owner authorization, backup and the documented stopped-writer window.
4. **Telegram boundaries and reuse:** no deployment-wide personal session,
   selected owner checks, one encrypted personal authorization and explicit
   per-workspace grants, reuse/detach/local-revoke API and durable bounded Telegram
   phone/code/2FA machine API. See
   [account reuse](CONNECTOR_ACCOUNT_REUSE.md) for the delivery limits.
5. **Migration graph:** linear `0123` -> `0124` -> `p12t14_accounts` -> `p12t14_tg_login` -> `p12t14_tg_runtime` ->
   `p12t14_tg_recovery` -> `p12t14_tg_retention`, not the former
   slice-only sibling arrangement. No legacy Telegram session import.

## Preserved slice provenance

The source heads below are historical checkpoints; their individual passing
checks do not prove the consolidated tree. Local consolidation preserves these
heads in its integration ancestry. Publication uses the GitHub file API to copy
the exact verified resulting tree into the existing #308 branch, so the final
published commit need not carry every source head as a Git ancestor. The mapping
below and tree equality are the provenance contract; no old PR is marked merged.

| Old slice | Source head | Retained scope |
| --- | --- | --- |
| #314 | `2b9766d4aea2170ee75b044cc7208e59d78a3a69` | VK refresh concurrency fence |
| #317 | `856b3711aa8f13d413257c4d0258867b85b73c65` | Refresh encryption, migration, key rotation |
| #321 | `957bc2baa8833f90445302130bb911fe6ab96900` | Preserve bootstrap revocations |
| #322 | `b246e03a63f17a00f40a651e5d7b4086958ebe73` | OAuth guidance and bounded errors |
| #323 | `17bfbada39d49bdc18745c9716619eb915110c4b` | Duplicate OAuth guidance; source-only checks retained |
| #324 | `de44344659ba7737cb9cd690974db1332795052f` | OAuth query encoding |
| #325 | `94f9ffbd69e746646647ef9764a36037edaefab2` | OAuth endpoint trust policy |
| #326 | `04e00e62d20df18038190d04d4a53f808a2b42f4` | Selected Telegram owner isolation |
| #328 | `20411cb6b69638a6036a9c00cd061f3e013f3b7b` | Read-only source identity inventory |
| #330 | `50b7a9b7c2212efed89ee803e8d39253af04c490` | Read-only owner bootstrap inventory |
| #331 | `cf31e40b7022a9edbbca93dfe492705b7bec584d` | Refresh status and unreadable-key guidance |
| #332 | `075105b66a0ccb28bf7290e04a78dadb09dd414a` | Reason-aware preflight and notifications |
| #333 | `b1b95469e2c5c61ac460c397cbd703db976935e4` | Known-unsent unreadable-key recovery |
| #334 | `26527604f9809ae520a730de5e601d1911a9200a` | No global MTProto session fallback |
| #335 | `217021becea3f094fb8e48c44277c49df5f1629b` | Same-user reusable authorization grants |

Conflict resolutions retain #322's stronger bounded configuration error handling
instead of #323's exception echo; source-level checks now require redaction for
both error classes. They retain #331's `renewal_unverified` and unreadable-key
caveat, #334's no-global-session behavior and #335's explicit workspace-consent
model. Authorization schema now follows 0124; both secret-only revisions skip
unrelated permission-catalog reconciliation. Consolidation also fixes key
rotation to include personal authorization ciphertext and to refuse unsupported
or mismatched schema generations, avoiding an unreadable-store key switch.

## Source-grant inspection continuation

`telegram_grant_access` now provides strict non-secret source/owner/grant/revision
pins and a fresh joined metadata inspector. Unit tests use fake sessions and SQL
compilation; scoped PostgreSQL integration evidence is separately pinned in the
task card and #308, using an exclusively owned synthetic schema. This is a preparatory slice only: no collector call site,
secret resolution, source-picker binding write or session lease is delivered.
Snapshots cannot authorize I/O or establish provider/envelope health. See the
account-reuse contract for fields and freshness limits.

## Shared-account lock continuation (partial item 1)

`telegram_account_lock` adds fail-fast PostgreSQL transaction advisory exclusion
per personal authorization across workspace grants. Pre/post-acquisition and
explicit handle checks use fresh metadata on the same READ COMMITTED, READ ONLY
connection; the source inspector refreshes ORM identities and uses the unique
platform type instead of a display label. Handles are task/lifetime-bound and
fail closed on failed recheck or connection loss. Normal exit/error/cancellation
releases the lock. Revoke/detach remain independently available.

The lock-only foundation did not decrypt. Its private setup-material extension
below still does not call providers, wire the collector, authorize initiator/payer,
reclaim stale workers or fence remote requests/output. It is not
a complete renewable/durable execution lease. Targeted fake-session and synthetic
PostgreSQL tests (including an independent process and lost-connection case) are
separate from unrun runtime/provider and full migration-history gates. See the
account-reuse contract and latest card/#308 evidence for exact scopes/revision.

## In-lock envelope continuation (partial item 1)

The private `guard._setup_material()` scope loads the exact account envelope with
the same joined source/owner/grant/revision predicates, validates it through the
shared reuse decoder, and rechecks metadata after decryption before yield. Setup
values are task/transaction/tenant/scope-bound, repr-redacted and cleared on exit
or invalidation; already-read Python strings cannot be recalled or zeroized.
Property access is not a fresh database authorization or provider-operation right.
Failed resolution/checks and cancellation fail closed without replay or fallback.
This remains preparatory: no runtime TTL, initiator/payer checks, request/output
fence, collector integration or real-provider validation. See latest card/#308
for scoped synthetic tests; the full suite and independent audit remain end gates.

## Transactional consent admission continuation (partial item 1)

The private guard admission transaction locks account -> grant -> source, then
all mutable owner-rights rows, using fresh READ COMMITTED predicates and no secret
selection/decryption. Only same-transaction writes are protected through commit.
Withdrawals linearize before rejection or after the receipt, not between check
and commit. Both write-local pins and the original guard are rechecked before
commit; errors/cancellation invalidate without replay. This is not a complete
output fence: initiator/payer/job checks, durable stale-worker generation,
request boundaries and actual collector/staging wiring remain gated. See latest
task/#308 evidence for lock contention, cancellation and synthetic receipt tests.

## Account-only local revocation continuation

The JWT-only personal metadata list and existing personal-wide local-revoke route
now work without workspace membership or a live encryption key. Only the owned
authorization and own consents can be invalidated; no provider logout is claimed.
The middleware exception is exact-method/path only. Workspace attach/detach/login
remain membership-bound. Fake-transaction/JWT-ASGI tests are distinct from the
authored-but-unrun PostgreSQL/assembled-app rollback and no-membership checks.
This API slice alone did not deliver account settings UI or reauthorization/reconsent;
the subsequent partial web slice is described below.

## Machine API contract continuation

Connector-account responses now have explicit metadata-only OpenAPI models and
HTTP output field filtering, with fixed non-echoing 500 on invalid internal
projections. Existing success fields/statuses, nullable IDs and datetime offsets
are preserved. Legacy credential login is declared deprecated with its actual
409 response; credential-disable compatibility aliases remain unchanged. The
generated route index includes personal listing. DB-exempt JWT/ASGI and mounted
application OpenAPI checks are not database or real consumer evidence. API
consumer migration and full migration-history checks remain open.

## Personal metadata and local-revoke web continuation

The browser-session page lists owned new-store Telegram metadata and provides an
explicit confirmation for personal-wide local revoke, including without any
workspace membership. The exact-path middleware exception does not extend to
login, attach, detach or workspace settings. A bounded form and session/actor/page-
bound CSRF token guard the action; the service rechecks owner and revision. Errors
are opaque and an uncertain result is not reported as success. No remote logout,
legacy vault modification or provider-health claim is made. Fake-service HTTP and
offline DOM checks do not prove real database transactions, real CDN rendering,
provider connectivity or the complete login/consent journey.

## Workspace grant web continuation (partial)

A workspace-scoped browser page now exposes explicit attach/detach of the
current user's reusable Telegram authorization/grant via the existing service.
CSRF binds session, actor and active workspace; confirmed local changes never
contact Telegram. This does not deliver source selection, runtime enforcement,
provider health or reauthorization. DB-exempt browser-session tests are not
PostgreSQL transaction/race or full browser/provider acceptance evidence.

## Remaining completion gates

- Telegram reauthorization/recovery, metadata retention and operational recovery;
  bounded machine login is delivered but is not a web onboarding UI. Scheduled
  expired-ciphertext cleanup is implemented but PostgreSQL concurrency/rollback
  validation is still required. No live provider validation is claimed.
- Complete login, reauthorization/reconsent and workspace account/source picker UI;
  assembled CSRF/DOM journeys with a fake provider; fresh execution-time grant,
  owner, membership and revision checks; shared-session execution lease/locking.
- Validate the default-OFF Telegram runtime route and removal of personal-pull
  vault fallback; old/unpinned jobs need explicit re-admission. No activation yet.
- API consumer compatibility for changed status/unsupported legacy login;
  current combined-tree tests and full migration-history validation; independent
  audit. Q-5/music live-provider gates and rollout approvals remain separate.

#311 is protected coordination work, not another implementation slice to merge;
it and other tasks' PRs/branches are intentionally unchanged. Never interpret the
single-PR workflow as authority to edit another task, apply a public migration,
read/copy real sessions, contact a live provider, rotate a key or deploy.


## Code-first execution epoch and bounded adapter (unvalidated)

Private modules now add a per-account BIGINT epoch, persisted JobClaim and exact
source/payload pin, fresh initiator or explicitly consented scheduled-payer rights,
a bounded history-only Telethon send gate, and a generation/job-aware output
transaction. The adapter permits no login/send/exported sender, application retry,
reconnect, flood sleep, update receiver or background keepalive. Its dependency on
Telethon private APIs is a specific final-test/audit gate. Already-sent requests
cannot be recalled; stale/withdrawn responses and receipts must be rejected.

No collector route is activated; the legacy collector remains unchanged. New
staging/orchestration and consumer migration are still required. Runtime migration
code is NOT applied. Downgrade/epoch rollback requires draining all runtimes first.
These additions are deliberately untested under the owner's revised code-first
workflow: no new tests or test execution until the final shared phase. Formatting
and AST/diff checks are not acceptance. Previous test fingerprints apply only to
their earlier trees; no READY, merge, provider-health or deployment claim.


Guarded staging continuation: a private transaction-owned writer is reused by
the legacy owning wrapper and a one-attempt runtime-batch helper. Only the latter
uses the runtime epoch/job/consent commit; it takes no alternate content/session
or run ID. The trusted dispatcher claim accessor supplies identity only. This
code is not collector-routed and is untested until the final shared phase.


## Default-OFF collection integration checkpoint (unvalidated)

Collect enqueue overwrites untrusted Telegram metadata snapshots; the new
runtime checks the exact queued source/grant/account/revision pin against its
held account lock. Missing or changed pins fail closed, never discover another
account. The collector route is code-wired behind a default-false switch, with
raw/cursor/selection-ledger writes in the same fenced commit and guard release
before AI. Raw personal pull/vault fallback is removed from the legacy Telegram
client entry point. Bot push/sending are independent. Do not enable the switch
without final tests/audit and separate rollout approval.

Static review corrected capped history ordering to oldest-unread-first to avoid
skipping the unread middle with a max-ID cursor; source.last_checked is not used
as an L2 watermark. Rehearsals omit fetched text/hashes in deferred previews and
write no raw rows/cursors, although runtime epoch/job metadata still changes.
Old jobs, discovery-created targets and consumer diagnostics need explicit
compatibility coverage. No new tests or DB/provider/migration execution occurred.


## Code-first assembled continuation: review before rollout

The latest checkpoints supersede earlier "not delivered" statements above only
for code availability, NOT validation. The supplied picker patch was applied to
`7b6331d2`; source picker API/web, Telegram push/api compatibility, same-subject
recovery/reconsent, bounded browser onboarding, retention replay tombstones and
legacy-consumer diagnostics are in this PR. Review generic source-manager guards
alongside the dedicated picker: old CRUD may preserve but not introduce/retarget
its binding. Review queued legacy diagnostics and rotation's explicit supported
schema-generation allowlist. Runtime remains default OFF, Draft/IN_PROGRESS.
No merge, deployment, migration apply, key rotation or real provider operation.

Final validation must cover strict form/CSRF/privacy boundaries, state and grant
races, runtime job/payer/epoch/request/output fences, fake-provider pagination,
Bot API/push/VK/other-job regressions, metadata purge/replay protection and migration
model/schema/history agreement. Database/migration checks are NOT authorized by
this code handoff; the owner's current no-apply hold remains in force. Independent
audit follows the common tests, with no self-acceptance or merge authorization.
