# Development: current stage and task ownership

## Read only these three levels first

1. **Global plan:** [PRODUCT_PLAN](../PRODUCT_PLAN.md) — product outcomes and expansion.
2. **Stages:** [ROADMAP_INTEGRATED](../ROADMAP_INTEGRATED.md) — existing A–G sequence and exit gates.
3. **Current-stage tasks:** this page — what is occupied, done, available or gated.

[Implementation ledger](../IMPLEMENTATION_STATUS.md) records integrated changes;
GitHub dev/open PR heads establish current merge/ownership state. Individual
handoffs explain technical decisions/evidence, not a second global backlog.
[Release gates](../BUSINESS_PRODUCTION_READINESS.md) remain binding.

## Current stage: Foundation, safety and repeatable operation

Latest source checkpoint: dev `52b0d296f1d3732a3ecff3b3a2dbcd4c94f18501`.
#59–#79 and #81 are merged; their evidence retains its original
revision/patch attribution in the ledger. The older navigation snapshots below
are historical, not the current work allocation. No merged-head/full-suite,
deployment or acceptance is implied.

Historical navigation snapshot: dev `50dc2a2b0bb9832c05b426ea16c6010616264b31` is the reconciliation snapshot for this branch (carried in by merge `ef4fd13`), not a claim of permanent currency; dev may have moved again and this docs branch does not chase every movement. At the time of the queue merges the THEN-dev head was `073fcd0` (PR #43). Dev advanced by four owner-approved merges: [PR #45](https://github.com/Starck43/social-media-ai/pull/45) queue foundation integration as `f078f3a1d5a57985d31e6a7a7380bf5bc98f9b3e` (closed #34/#35/#37), [PR #46](https://github.com/Starck43/social-media-ai/pull/46) as `8f3c6127d835b615d624f5e461dc6697c930fa0a`, [PR #49](https://github.com/Starck43/social-media-ai/pull/49) as `7ad1d75b263df953ef8163d2ac990a9c6cc4589f`, and [PR #43](https://github.com/Starck43/social-media-ai/pull/43) as `073fcd0`. The parallel refactoring lanes then merged: #30 tenancy-model layout as `50dc2a2`, #48 stored-analysis lookup as `2aa0b43`, #42 date parsing as `137dd1e`, #47 direct CLI awaitables as `3551f26`, #44 CLI username parsing as `c765365`, PR31 notification-model layout as `f333403`, PR32 collection-model layout as `ef0de1c`. Each merge's checks stay attached to its own tested SHA below; no historical full suite is re-attributed to this baseline.

Integrated baseline dev `683c49e85918503119af47338200d8f762a52403`: PR22 merged
by explicit Owner approval, history preserved. Merge tree equals approved
56d8105; executable code equals Owner-green 482eacb 1612 passed/11
warnings/64 subtests/saved exit0. That full suite belongs to PR22 head 482eacb
and merged 683c49e only; it is not carried onto dev 073fcd0, and no full-suite
run exists for the #43/#45/#46/#49 merges — their evidence is the targeted
checks recorded below at their own tested SHAs. Merged/regression-verified does
not establish deployment, live activation, or business/security acceptance. The
general-queue foundation is now merged (current baseline); remaining queue
gates stay OPEN and broader gates remain OPEN.
Product **Foundation** is still open; engineering stages **B/C** have unfinished
gates. This is not a declaration that all earlier/later stage gates passed.
Do not restart merged digest integration or bounded logging work.

| Stage / task | State and allocation | What to do next |
| --- | --- | --- |
| B / identity and permissions, PRD-02 | **MERGED bounded safeguards:** [PR #22](https://github.com/Starck43/social-media-ai/pull/22) as 683c49e; Owner 482eacb full 1612 passed/11 warnings/64 subtests/saved exit0 | Do not restart delivered boundary/tests. Broad XSS/durable approval/CAS/browser/live/release acceptance OPEN. [Handoff](identity_permissions_handoff.md); PR29/30/31/32 separate. |
| C / general queue, PRD-05 | **MERGED bounded foundation:** [PR #45](https://github.com/Starck43/social-media-ai/pull/45) as `f078f3a` carrying closed #34 (atomic stale reaping), #35 (docs wording) and #37 (outcome contract docs); [PR #46](https://github.com/Starck43/social-media-ai/pull/46) dispatcher outcome-error boundary as `8f3c612`; [PR #49](https://github.com/Starck43/social-media-ai/pull/49) truthful web job outcome as `7ad1d75`; [PR #51](https://github.com/Starck43/social-media-ai/pull/51) ordinary claim fencing as `351f3b6` | Code evidence at tested SHA `81138d99dab987b3355e674d5bf2922df59e7d49`: standalone 5 OK/exit 0 (log `pr34-rerun-standalone-20261010-054125.txt`), config check exit 0 (log `pr34-rerun-schemacheck-20261010-054130.txt`), PostgreSQL 12 passed/exit 0 (log `pr34-rerun-dbtest-20261010-054136.txt`). Byte identity applies ONLY to `app/models/managers/job_manager.py`, `tests/test_job_stale_reap.py` and `tests/test_job_stale_reap_db.py` between the tested SHA and integration head `6decbf9` (docs-only delta `e9d25d3`, other files not claimed). #46 at tested `6979feb` (25+13+14/exit 0); #49 at tested `74af779` (13/exit 0); #51 at tested `dbc054c` (77 standalone + 15 PostgreSQL, exit 0). Full suite NOT run at merged heads. Bounded ordinary Job/task atomicity is merged in #53 (`24386628`; owner-tested `e3e2226`, 27+15+7/exit 0); #52 web feedback is merged (`10d16090`, final `4f664786`, owner-tested `f613839`, 15/exit 0). Operator cancellation #54 is MERGED (`ee43592a`, final `7316a084`, owner-tested `4d65af4`: 14+15 standalone, config check, 10 PostgreSQL; all exit 0). Cleanup_done #55 is MERGED (`9d63892c`, final `6e46edca`, owner-tested `1eb561fd`: 6 standalone + config + 8 PostgreSQL, all exit 0); web deletion #56 is MERGED (`c7b3693e`, owner-tested `0cac44fd`: 11 standalone/exit 0). Attempt-budget #60 is MERGED as `975b0d43`; owner-tested `0159392b`: 5 stale + 5 wiring units, config check + 14 PostgreSQL, all exit 0; no merged-head execution. General leases, recovery, ordering, spend and broader fencing remain OPEN. |
| C / collect/analyze → user feedback and digest coverage | **MERGED:** #61 analyze error accounting (`871046fc`), #62 warning notifications (`c4b570ea`), #63 conservative digest caveat (`18e33a84`), #68 analyze-error UI projection (`844209b1`) | See ledger for source/patch evidence and owner review. Source/staging errors are not automatic retry or a new terminal Job status. Staged analyzer diagnostic wiring #71 is merged. Inline diagnostic propagation #73 is merged; real-DB digest-read verification and complete source/content coverage remain OPEN. |
| C / monitored-user collection failures | **MERGED:** [PR #69](https://github.com/Starck43/social-media-ai/pull/69) as `1e58c3f3`, head `aaaa0fd3`; only `ContentCollector.collect_monitored_users`, `handle_collect` and `tests/test_monitored_collection_errors.py` | Author 25 isolated checks on dev `2e96dea` + source patch, exit 0. Preserve successful totals, continue child failures, count one affected parent source, normal empty != error, safe auth subset/markers. Actual integration is tracked by the branch/PR, not a combined-head test claim. Non-raising provider/analyzer failures and legacy new-item fallback semantics remain separate limits. |
| C / staged analyzer reported failures | **MERGED:** [PR #71](https://github.com/Starck43/social-media-ai/pull/71) as `7be57a67`, head `92ef6b76`; analyzer diagnostics + adjacent handle_analyze staged wiring + one standalone test | Author 19 analyzer checks on `1e58c3f` + patch, 12 new wiring checks on `c8e5897` + combined patch, exit 0. Integer-only reported failures mark one affected staged source; subsequent catch does not double-count. Normal skips/list results/storage/retirement/attempts/status/retry stay unchanged. Inline propagation #73 is merged. The bounded partial-result storage safeguard #76 is merged; complete media coverage and every-call accounting remain OPEN. |
| C / inline collect analysis diagnostics | **MERGED:** [PR #73](https://github.com/Starck43/social-media-ai/pull/73) as `8402cd4c`, head `055c1833`; collect_from_source/collect_monitored_users + handle_collect + one standalone test | Author 22 new isolated checks on `6811ccf` + exact patch, exit 0; original baseline rejected. Reused analyzer counters are snapshotted per call, strict nonnegative monotonic int deltas only; legacy/reset/malformed unknown stays omitted. Positive monitored diagnostics retain successful totals and combine with collection failures as one affected parent source. Storage/retirement/status/retry unchanged; no complete coverage or live acceptance claim. |
| C / partial analysis storage and coverage | **MERGED:** [PR #76](https://github.com/Starck43/social-media-ai/pull/76) as `52515986`, head `a461136f`; analyzer record/base/save methods + one new test + one strengthened existing assertion | Author 19 new storage/dedup source checks + the one changed assertion on `47a972d` + patch, OK/exit 0. New-row useful partial has sanitized parsed output, analysis_complete=False, no new completion hashes. Any existing daily row A remains untouched when partial B collides; B returns None/stays staged. B analytics usage/history is not saved on collision. Full-success update, read-side, retirement/status/retry/schema unchanged; broader accounting/coverage OPEN. |
| B / bounded web permission and session safeguards | **MERGED:** #66 active-user default (`118829f2`) and #67 secure session cookies (`2e96dea`) | Evidence stays on original source/patch identities in the ledger. No app startup, network/TLS, deployment or broad permission/security acceptance. |
| C / first-contact recovery | **MERGED bounded safeguard [PR #64](https://github.com/Starck43/social-media-ai/pull/64)** as `d38ea3a5`, head `1f92c3ae`; runtime/session/history coordination remains with parallel chat #1. History-local pairing #59 is MERGED (`acce792d`). | Do not restart delivered first-contact/history work. Evidence is historical source+patch, not submitted/merged-head full suite or general recovery/live acceptance; see ledger. Recovery / ordering / spend remain OPEN. |
| B / per-attempt accounting and reservation design, PRD-03 | Owner sequence places design after queue; schema approval required | Do not duplicate that lane or implement a billing migration from this table. |
| C / bounded log privacy, PRD-06 | **MERGED:** dispatcher #20, retirement #23, attempt-count #25 (`eb49d1d`), bounded runtime #57 (`21edba07`, four log sinks); #57 owner scratch patch: 4 passed/exit 0, NOT a submitted-head run | No repeat implementation or automatic test rerun. Two handle_analyze exception-log sites are MERGED in #70 (`c8e5897`), owner 7/7 + adjacent attempt-log 9/9 + retirement-log 7/7 on `aef81d3`, accepted without rerun; no broader log-privacy acceptance. Retention inventory/proposal #72 is MERGED, periods UNDECIDED and PRD-06 OPEN. The bounded tenant-scope DELETE safeguard #74 is MERGED (`47a972d`); Owner reported 11/11 PostgreSQL checks on detached `47a972d`; untracked test SHA256 reported; content review and typed-source-SHA clarification pending, no saved execution logs. Global bypass DELETE/commit deviated from the rollback-only handoff and must not be repeated; not #77/current-dev evidence or purge authorization. Prune exception-log privacy #75 is MERGED (`f5541c2`), author 7 source/SQL-session-double checks on `47a972d` + patch; this sanitizes one warning only. **MERGED:** [PR #78](https://github.com/Starck43/social-media-ai/pull/78) as `dd3c330`, head `7493da2`, five outer analyzer error logs + local category helper + one isolated test; 18 new checks on `52515986` + patch OK. Static stage/allowlisted category, no exception message or traceback; counters/None/success/actual asyncio cancellation preserved. **MERGED:** [PR #79](https://github.com/Starck43/social-media-ai/pull/79) as `5c8ccd3`, head `4d8f872`; four schema-build warning calls reuse the same category helper; 16 new source/double checks on `dd3c330` + patch OK. Existing fallback/validation/counters/storage preserved. Other collector/analyzer/prune/provider/ORM/transcript logs and external tracing are not globally sanitized. #57 source+patch evidence and limits are in the journal, not full runtime/privacy acceptance. |
| C / staged raw-SQL tenant mutations | **MERGED:** [PR #77](https://github.com/Starck43/social-media-ai/pull/77) as `924479d`, head `272aab86`; only record_attempts/delete_hashes/reset_attempts + one shared local scope helper and isolated test | Author 12 new source/context/SQL-session-double checks on `52515986` + exact patch, OK; baseline rejected. Normal writes add a bound tenant predicate, invalid/missing context fails before execute; explicit existing bypass/source-ID/hash/attempt selection unchanged. #74 expiry method/global imports/context unchanged. Owner #74 PostgreSQL 11/11 reported on detached `47a972d`; test hash recovered/content review pending, NOT DB evidence for these three mutators. Chat #1 fixture [PR #81](https://github.com/Starck43/social-media-ai/pull/81) MERGED as `52b0d296`, head `02dc8c29a3a3dbf847c9dd8a6ae2806fce705395`; only file-local source-tenant fixture/no bypass. Owner reports 15/15 on that exact head via external rollback_runner, no bootstrap/seed, COMMIT0/ROLLBACK1; runner/logs not independently reviewed. Not fresh-dev/COUNT/general DB acceptance. No schema/purge/live or old-check reruns. |
| C / staged fallback count boundary | **SOURCE-VERIFIED:** chat #1, `fix/staged-count-scope`; only store_items fallback SELECT + one standalone test | Author 15 new actual-method/SQL-session-double checks on `5c8ccd3` + exact patch OK; baseline 8 failures. Readback counts only stamped tenant/source pairs of the selected run, never unrelated neighbours. INSERT/tenant stamping/positive rowcount/no-run estimate and #74/#77 mutations unchanged. Persisted count is not proof of newly inserted rows; PostgreSQL execution unverified. Fixture #81 is merged; its owner15 result belongs only to `02dc8c2`, not this COUNT patch. |
| C / typed boundaries and memory, PRD-07 | Typed boundaries #17 merged; **atomic learn memory batch MERGED:** [PR #43](https://github.com/Starck43/social-media-ai/pull/43) as `073fcd0`, head/tested `b29e432` (49 boundary + 11 manager standalone + 11 PostgreSQL, exit 0) | State-key patch #58 is merged (`59d775cc`, scratch evidence in current allocation); confirmation CAS and legacy replacement remain OPEN. Do not rebuild typed output contracts or the atomic batch. Acknowledgement-loss rollback proof, ordinary claim fencing, duplicate LLM spend, fact quality and task/billing atomicity remain OPEN. |
| A/C / development documentation | **MERGED:** [PR #26](https://github.com/Starck43/social-media-ai/pull/26) as `10212b5e`; no application change | Use the three-level entry point; do not redo this navigation cleanup. |
| C / observation and recovery runbook, PRD-04/05 | **MERGED:** [PR #27](https://github.com/Starck43/social-media-ai/pull/27) as `0234c21`; guide `b1cf91f` | [Existing DEPLOYMENT runbook](../DEPLOYMENT.md#observation-and-conservative-recovery): source-grounded observation/escalation, no automatic reset/replay/restart. Eight tabletop cases prepared, NOT run. |
| D–G / UX, pilot, expansion and scale | Gated, not current automatic implementation scope | Use stage exit evidence and an explicit selected user journey, not old research checklists. |

The owner reports having run checks and pushed changes. Do not ask for or execute
the same checks again by default. Detailed evidence belongs to the submitted
revision/PR and owner logs: no full-suite count or tested SHA is invented here.
Historical PR22 Owner full-suite evidence: exact 482eacb 1612 passed/11 warnings/64
subtests/738.25s/saved exit0, unchanged HEAD. Approved merge 683c49e has
identical executable code; post-merge journal diff is docs-only, no retest by
default. Separately approved one-row TEST cleanup (zero dependencies) is not
future cleanup/reset permission. GitGuardian success; no invented independent
tests/CI/warning triage.
Merged, owner-reported checks, independently observed checks, deployed and
accepted remain different states.

### Concurrent-work boundary

PR30 (tenancy-model split) is **MERGED** as `50dc2a2b0bb9832c05b426ea16c6010616264b31`,
PR31 (notification-model layout) as `f333403208d266fdb1978df03afa9d6277010558` and
PR32 (collection-model layout) as `ef0de1c1a34ed74d546f87af70fe790aee9db825`
(git ancestry verified on dev); none of those lanes is an open assignment here.
PR29 was deferred in the prior checkpoint and is now merged as `eac6d899` (see ledger). The compatibility/date/cli lookup lanes #42 (`137dd1e`),
#44 (`c765365`), #47 (`3551f26`, stacked on #44) and #48 (`2aa0b43`) are merged
the same day; see the journal for their tested SHAs.

PR22 is closed/merged; PR33 records its FOUR-file post-merge documentation
status only, not identity/runtime implementation.
The model-layout lanes PR31/PR32 are merged (SHAs above); PR29 was deferred in the prior checkpoint and is now merged as `eac6d899` (see ledger).
Check fresh heads and file ownership before the next package; preserve parallel
corrections. The compatibility pointer and detailed handoff explain evidence,
not competing backlog. Owner separately approved integration of this docs-only
record; that authorization does not cover PR29 or future code changes.
Queue continuation [PR #51](https://github.com/Starck43/social-media-ai/pull/51)
(ordinary claim fencing) merged at the historical merge checkpoint `351f3b6`
(dev has since advanced); the merge SHA is distinct from the owner-tested
`dbc054c7f37ed1071711afea750e044b7e50ffd6`, owner-reported 77 standalone + 15
PostgreSQL checks exit 0, review published on that PR. Queue atomicity [PR #53](https://github.com/Starck43/social-media-ai/pull/53)
is **MERGED** as `243866281f1bb2e3ed87827601e2705e82b6c3df`; owner-tested
`e3e222673bb39debe2b07cdf4fd7d95b71a8dac1`: 27 unit + 15 existing DB + 7 new DB,
all exit 0. [PR #52](https://github.com/Starck43/social-media-ai/pull/52) is
**MERGED** as `10d160901b7305a0ed22d15fd146b7573d6a399b`, final head
`4f66478668a584ba9e23cb5fa7f40712286b3576`; code/tests equal owner-tested
`f61383936cb3dfaec3c5ff09ab6442a106ee0159` (15 standalone, exit 0), final delta
docs-only. No full suite or combined-head check is claimed. Operator cancellation [PR #54](https://github.com/Starck43/social-media-ai/pull/54)
is **MERGED** as `ee43592a05d706168e7b80ece87ea17e2de34641`, final head
`7316a084231591fbbcfbac207a4ddd7d7841e99e`. Owner-tested
`4d65af4e55c6b3096db2278ce2589cfc17f1525e`: 14 cancellation + 15 web outcome
standalone, config check and 10 PostgreSQL checks, sequential/all exit 0;
[owner evidence](https://github.com/Starck43/social-media-ai/pull/54#issuecomment-6097327068).
Final delta was three docs only; executable files equal the owner-tested revision.
No merged-head/full-suite run, deployment or acceptance is implied.
Cleanup [PR #55](https://github.com/Starck43/social-media-ai/pull/55) is **MERGED**
as `9d63892c62ea1d2c9458d133faad961925ac8c86`, final `6e46edca` (docs-only final
update), owner-tested `1eb561fd`: 6 standalone + config + 8 PostgreSQL, all exit 0.
Web deletion [PR #56](https://github.com/Starck43/social-media-ai/pull/56) is
**MERGED** as `c7b3693ee85769dbb91e7a53016669cfe1210173`, owner-tested/final
`0cac44fd`: 11 standalone, exit 0. Its observed-tenant bypass correction was
reviewed; source-isolated tests are not independent PostgreSQL race evidence.
Queue attempt-budget [PR #60](https://github.com/Starck43/social-media-ai/pull/60)
is **MERGED** as `975b0d431cb3df2cd6c6fb0f8f16ea6a3d3589b2`; owner-tested head
`0159392b12b22442e7b051d6873046ecf5ef7138`: 5 stale + 5 wiring units, read-only
config check and 14 PostgreSQL cases, all exit 0. No reset/re-arm of attempts or
cost/history; effect idempotency and stop-notification coverage remain OPEN.
Collect/analyze feedback [PR #61](https://github.com/Starck43/social-media-ai/pull/61),
[PR #62](https://github.com/Starck43/social-media-ai/pull/62) and conservative digest
coverage [PR #63](https://github.com/Starck43/social-media-ai/pull/63) are **MERGED**;
evidence and limits are in the ledger's latest checkpoint, not inferred from a
green combined-head run. Runtime/session/history remain assigned to parallel
chat #1, specifically first-contact recovery in `agent_session_manager.py`,
even without an open PR. Local history pairing #59 is merged as `acce792d`;
do not restart it or take the reserved recovery lane. Its state-key
[PR #58](https://github.com/Starck43/social-media-ai/pull/58) is **MERGED** as
`59d775cc`, head `3f7f8753`: owner-reported scratch source `21edba07` + exact patch,
8 helper + 19 confirmation + 4 privacy + 7 PostgreSQL checks, all exit 0; NOT
submitted/merged-head execution. Cooperating-key writers only; legacy save_state,
confirmation CAS, whole-turn serialization and exactly-once effects remain OPEN.
Bounded runtime privacy #57 remains merged as `21edba07` with its original
scratch evidence. No combined-head/full-suite/deployment claim is made here.
Recovery / ordering / spend and broader fencing remain **OPEN**; PR29 is **MERGED** as `eac6d899dde338b3b457bb7186c1d21a65f9d1c4`; no new test evidence is inferred.

### Retained earlier merge checkpoint

Historical reconciled source checkpoint and integration-branch base:
`844209b1d79c8aa0284868098a6aea42e3053a53`. The monitored source patch was
checked on `2e96dea` + patch; concurrent #68 changed only web UI and its test,
not any edited existing collection/docs file. Earlier reconciliation #65 merged
as `9c2fa5b`; #66 (`118829f2`), #67 (`2e96dea`) and #68 (`844209b1`) followed #64.
Refresh dev/open PRs before the next package; the checkpoint is not permanent.
This metadata update is batched with a real code package, not a separate PR.
This records integration, not combined-head/full-suite execution, deployment
or release acceptance. Recovery / ordering / spend remain OPEN.
Model-layout group is **MERGED**: #36 analysis (`f2f53b1`), #38 identity
(`7d8c731`), #39 agent (`c1d3b9e`), #41 scheduling (`b4d48c6`); earlier
#30/#31/#32 layout merges remain recorded above. Runtime whitespace #40 also
merged as `bcff693`. Git ancestry establishes integration, not new test evidence.
Their checks remain attached to original owner-tested revisions; no new full
suite, deployment or acceptance is claimed by this reconciliation.

## Technical catalog — open only what the selected task needs

Every existing top-level design document is assigned below. A category does not
certify implementation or acceptance. Older documents retain their original
baseline; do not execute their historical "next" section as a new assignment.

### Delivery contracts and acceptance evidence

- [Retry design](digest_delivery_retry_plan.md), [schema review](digest_delivery_state_schema_review.md).
- [Checkpoint contract](digest_checkpoint_contract_handoff.md), [store](digest_checkpoint_store_handoff.md).
- [Single-part transport](digest_single_part_transport_handoff.md), [HTML parts](digest_html_parts_handoff.md).
- [Atomic snapshot](digest_atomic_snapshot_handoff.md), [job integration](digest_job_delivery_handoff.md).
- [Tenant-safe routing review](tenant_safe_digest_delivery_review.md).

### Runtime, outcomes and privacy evidence

- [Bootstrap/readiness](bootstrap_readiness_handoff.md), [returned failures](returned_job_failures_handoff.md).
- [Typed output boundaries](typed_output_boundaries_handoff.md).
- [Dispatcher privacy](dispatcher_log_privacy_handoff.md), [retirement warning](staged_retirement_log_privacy_handoff.md), [attempt-count warning](staged_attempt_log_privacy_handoff.md).

These contracts are retained at their paths to preserve active PR references;
they are not independent current-task lists. Their current merge state comes
from dev/ledger, not an old PREPARED label.

### Product/architecture proposals and design context

- [Vision](vision.md), [product-direction delivery record](product_direction_handoff.md).
- [Deployment proposal record](deployment_architecture_handoff.md), [scale criteria](future_scale_strategy.md).
- [Personal chat routing proposal](personal_chat_workspace_routing_plan.md) — not shipped or assigned here.
- [UI design](ui.md), [analytics UI proposal](analytics_ui_refactor.md).

### Research and review inputs — not the current backlog

- [Proposal review](proposal_review.md), [readiness delta](business_readiness_recheck_2026_10_08.md), [reliability review](reliability_review_2026_10_08.md).
- [Competitive research](competitive_analysis.md), [analysis experience research](analysis_and_best_experiences.md).
- [Analysis detail](analysis_detail_review.md), [drilldown](analytics_drilldown_review.md), [filters](analytics_filters_review.md), [chain navigation](chain_navigation_review.md).

### Historical navigation/model work

- [Navigation preparation record](archive/documentation_organization_handoff.md).
- [Model reconciliation record](archive/model_reference_reconciliation_handoff.md).
- [Historical archive index](archive/README.md) — preserved evidence, not release authority.

The two old top-level filenames remain compatibility pointers, not active
reports. Other technical contract paths remain stable for active PR references.

## Current package and retained cleanup evidence

PR #26 navigation/archive cleanup is **MERGED** as `10212b5e` on the owner's
integration. Original preparation commits: navigation `185a500`, archive/link
cleanup `bd3d893`, PR-state `35d4144`. All 34 design documents were cataloged;
two original reports were archived byte-identically with compatibility pointers.
Its static link/anchor/fence checks are historical documentation evidence, not
application/deployment acceptance. Do not repeat the cleanup.

Operator package [PR #27](https://github.com/Starck43/social-media-ai/pull/27)
is **MERGED** as `0234c21` on the owner's current instruction. Source baseline
`10212b5e`; guide `b1cf91f`, navigation `24c91f8`, final head `a874ada`.
GitGuardian and Kilo completed successfully for that head; Kilo reported no
issues. Fresh dev verified after integration. The latest pre-merge PR #22 file
comparison found ZERO overlap with these three documentation files; that branch
was not merged or modified. Source-grounded
process/HTTP observations, job/part outcome interpretation, evidence preservation
and separately authorized change gates. Eight new tabletop cases are written,
NOT executed; no probes, service commands, tests, DB or external calls here.
Unsafe legacy incident stamp/direct-send/live-volume-tar suggestions removed;
installation/update examples remain explicitly separate drafts, not a verified
production profile. No new runbook/handoff file.

Static checks: registered root health routes and main include; actual db/api
Compose and auto-migration startup; job-manager defaults; digest static categories;
new source links, anchors/fences/added whitespace and observation-command scope.
These are text/source checks, not full-repo crawling, rendering, staging fault
injection or restore acceptance. PR #22's occupied source/status/identity files
are unchanged. Eight tabletop cases remain prepared/unexecuted; merge does not
certify staging fault handling, backup restore or production acceptance. Next:
operator documentation/tabletop review, then select an unoccupied task using
fresh dev/open PRs; do not duplicate the assigned identity/queue/budget lane.
Previously owner-run application checks are not repeated. New implementation or
actual recovery/migration/activation still requires its own authorization.

## Maintenance rule

One global plan, one stage roadmap, one current-stage board. Update the relevant
row and the integration/evidence ledger, not a new competing backlog. Put local
commands/evidence in the PR and English Owner handoff commit body. Add a separate
technical contract only for durable nonduplicated behavior/decisions. Reuse it
for later small changes. Keep compatibility paths when another PR depends on
them; archive only after preserving source and migrating known links.

This is a documentation-only cleanup, not a feature/security audit. No tests,
DB, migration, provider/messenger call or sender activation is performed here.
