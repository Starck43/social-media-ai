# Implementation status

This file is a compact index of delivered foundations. The current queue and
ownership live in [design/README](design/README.md). Pull requests and Git history
are the source for exact patches, review discussions and test transcripts.

## Integration checkpoint

Verified `origin/dev`: `8894b22` on 2026-10-12. Receipt storage/verifier
(PRs #137/#154), non-analyzer journal capture (#163) and registration privilege
protection (#165) are integrated. Outcome storage [Draft PR #168](https://github.com/Starck43/social-media-ai/pull/168)
is NOT merged or activated. This checkpoint does not certify a deployment,
database revision or full release gate.

## Delivered foundations

| Area | Delivered behavior | Integration references | Remaining boundary |
| --- | --- | --- | --- |
| Identity and tenancy | Fail-closed active-user and permission checks, tenant-scoped model/manager boundaries, tenant-scoped agent-session uniqueness. Web (`/app`) and API (`/api`) workspace resolution ignores memberships of deactivated workspaces, matching chat identity and invite redemption. A non-owner member's rights on workspace-scoped models follow the membership role (existing Role matrix) in web, API and agent tools; owners, platform superusers and global models keep their previous rights. A workspace manager issues single-use invite links (with QR) carrying a chosen access level (viewer, analyst, manager, co-admin; the owner level is not invitable), and redemption binds that role; the team tab lists the workspace's open invites (level, expiry, uses) and a manager can revoke one, after which its code no longer joins. The web menu and the dashboard offer only sections the member's level may view (dashboard calls to create also need the create right); a direct visit to a hidden section answers 403 with the reason and level. Public API self-registration cannot set platform rights or account state: `is_superuser`, `is_active` and `role_id` in the body are ignored, the account gets the Viewer platform role, and a missing Viewer role fails closed. API analytics debug routes require the analytics read right like the other analytics reads, and the public OAuth callback HTML-escapes its error text. In chat the agent is offered only tools the member's level may call; settings show a member their own part and level, not workspace-only tabs. | PRs #22, #29–32, #38, #66, #95, #122 (merge `7b99f03`; owner-local 37 passed at `8e5fd3c`), #126 (merge `2329459`; owner-local at `deca354`: 79 passed / 5 pre-existing `test_web_permissions.py` failures identical to dev, 11 permission files 134 passed), #127 (merge `090878e`; owner-local 23 passed at `dcfe0b8`), #142 (merge `8b28311`; owner-local focused tests and browser check at `65d1954`), #146 (merge `a05ff56`; owner-local 31 passed at `36b5103`), #130 (merge `fd067ef`; test-only stub fix closing those 5 failures), #150 (merge `0806a08`; owner-local 11 passed at `4b64065`), #151 (merge `98204d1`; owner-local 17 passed, approved at `f1d8de2`), #160 (merge `9a01b40`; dashboard header names the member level, viewer layout), #165 (merge `7786cd7`; owner-local 4 + 81 tests and test_schema hostile-register scenario at `9e244d4`), #170 (merge `33c908f`; owner-local 7 + 35 tests and test_schema 200/404/403 and escaping scenario at `f4a34bf`) | Durable approval lifecycle, complete global-admin/credential review, workspace reactivation flow. |
| PostgreSQL jobs | Claim fencing, renewal, ownership preflight, periodic heartbeat, cancellation and bounded recovery/admission primitives. A claimed job with no registered handler records a terminal `failed` outcome without automatic retry. A stale-quarantined or attempt-budget-stopped job writes its task's `last_status`/`last_error` in the same transaction, so web/API do not keep `queued` or an older result; newer task evidence is not overwritten. New and legacy operator cancellations are classified as unconfirmed external outcomes; the queue shows review rather than ordinary retry, and scheduled prune retains their attempt/result/cost evidence. | PRs #51–57, #87, #89, #91, #93; direct `dev` commit `35350a8`; #158 (merge `5941642`; owner-local 74 passed / 1 pre-existing `PrunePredicateTests` failure identical to dev, at `ff81e7c`); [#155](https://github.com/Starck43/social-media-ai/pull/155) (merge `0f48d4b`; owner-local 81 standalone tests and read-only PG predicate accepted at `d41a9e4`; inherited handlers.py formatting drift retained) | Non-cooperative work, production timing and unresolved external-effect recovery. Actual DELETE/cancellation-race evidence and explicit history deletion/clear are outside the cancellation slice. |
| Agent state | Atomic session state/history changes, typed memory/output boundaries and untrusted-data framing of modality results in unified-summary prompts. | PRs #58, #59, #100, #102, #108, #109 | Remaining hostile-input surfaces and every-write authorization coverage. |
| Atomic confirmation consumption | Versioned actor-bound intent, tenant-scoped locked consume of the exact pending snapshot and post-commit single-use authorization; losers, replacements and uncertain commits cannot dispatch the approved write. | PR #105, merge `0179296`; owner-local acceptance at `a4d2673`, subsequent board-only reconciliation. | External-effect exactly-once delivery, crash recovery after consumption, every-write coverage and whole PRD-02 acceptance remain open; no runtime rollout or DB migration certification. |
| Collection and analysis | Partial-result diagnostics, tenant-scoped staging, bounded retirement, attachment snapshots and conservative text/media coverage. Web marks an explicit `analysis_complete = false` as an incomplete analysis with recorded reasons (`text_sampled`, `media_uncovered`, `stage_failed`) on the detail page, dashboard, day feed and group lists; a row without the marker is never labelled complete. | PRs #69–80, #88, #90, #92, #94, #97, #98; PR #134 (head `e10a7d5`, merge `b54d9f2`; owner-local 50/56/44 tests accepted) | Historical repair, remaining source fidelity, digest/API presentation of incomplete coverage and end-to-end evidence acceptance. |
| Telegram admission | Durable L1/L2 admission with cursor/receipt coordination and media placeholders. | PRs #97, #101 | Long-term receipt policy and whole-channel recovery acceptance. |
| Platform rate limits | A short platform rate limit gets a bounded pause and retry (VK code 6, HTTP 429 `Retry-After`, Bot API 429 `retry_after`; Telethon waits out a flood wait itself up to `flood_sleep_threshold`). VK codes 9/29 (flood control, method quota) defer immediately without retry; a limit that does not clear within the configured budget (`SOCIAL_RATE_LIMIT_RETRIES`, `SOCIAL_RATE_LIMIT_MAX_WAIT`), a longer hint or a Telegram `FloodWaitError` above the cap marks the source deferred with `outcome: "rate_limited"` and a retry hint — not an error and not an empty result. On deferral nothing read so far is staged and the cursor/watermark does not advance; monitored users count rate-limited sources apart from failed. Writes and non-limit API errors keep their existing behavior. | PR #143 (head `28036a8`, merge `79a5cd1`; owner-local command 4 accepted at `28036a8`) | A shared per-platform budget across sources/workers is not implemented; Telegram durable-admission integration and cross-day limits remain open. |
| Credential diagnostic privacy | Bounded API credential-test and MTProto check-session failure statuses without provider/exception secret text; success labels and access gates preserved. | PR #107, head `5ff3d9f`, merge `f02d6d8`; verified in `dev`. | Other login/OAuth/CLI/vault logs, retention/offboarding and whole PRD-06 acceptance; no live-provider or deployment certification. |
| Data retention | `DATA_RETENTION_MONTHS` (`app/core/config.py`, `.env.example`; default 1 calendar month, minimum 1). The workspace's daily prune deletes analysis results whose last write is older than the cutoff (not publication date) and caps unanalysed staged raw content at the same age; analyses referenced by bot actions, the LLM attempt journal and job/outcome evidence are kept. A retention failure is logged privately and flagged without aborting the prune. | PR #139 (head `8b78080`, merge `451be42`; owner-local accepted, `tests/test_collected_items.py` 16 passed); integrated in `dev`, not deployed — deployment deferred by owner | Deletion is irreversible with no export/backup or offboarding flow; other data classes, per-workspace overrides and whole PRD-06 acceptance remain open. |
| Digest delivery | Per-target/per-part state, snapshot/checkpoint foundations and tenant-safe routing safeguards; legacy-path partial delivery is terminal `partial_delivery_requires_review` and blocks automatic rescheduled resends (explicit force only). Zero-success legacy delivery is a terminal unconfirmed outcome (no automatic retry). Scheduled and forced attempts claim the run row on its observed status and `updated_at` version; an in-flight `pending` row stops other attempts with `digest_run_in_progress_requires_review` before build/send, and a forced resend takes it over only after 30 minutes without a write. Lease loss mid-run leaves the run `pending` (no requeue, no resend) and digest review codes stay visible on the job. | PR #12, follow-up delivery packages, PRs #106, #112, #113, #115, #120 | Staging lease-loss and operator-recovery acceptance; delivery of an attempt stopped mid-flight stays unknown (operator decision). |
| LLM routing | DB-managed providers/models, model-level defaults and capability-aware selection/fallback. | PR #96 and migrations through 0091 | Complete per-attempt cost ledger and concurrent budget admission. |
| LLM analyzer journal foundation — DONE | Tenant-scoped captured stage records before result projection; tariff snapshot, Decimal USD and explicit unknown cost; execution IDs independent of source/job/task foreign keys. New OpenAI-compatible, Anthropic and custom analyze calls have pre-HTTP UUID identities: distinct identical dispatches remain distinct and replay of one envelope stays stable. Client-owned token evidence preserves unknown counts independently of zero projections. Structured provider USD price arbitrates; explicit provider/table zero is FREE, missing or unresolved price is NULL/UNDEFINED, with tenant-scoped captured-journal counts. Persistence acknowledges only an exact tenant-bound replay and committed insert; only the named attempt-key constraint suppresses a write, other errors propagate. | Journal `05bf353`, analyzer wiring `5953efb`, contract `9ebe945`, migration definition 0092; PR #114, head `55db324`, merge `f8b468e`; PR #118, tested head `e62bf40`, merge `ab21917`; PR #125, accepted head `08c53e1`, merge `d2b687d`; PR #129, accepted head `196ce7b`, merge `b0582b0`; integrated in `dev`. | immutable raw provider-report provenance, legacy/mixed-version envelopes, durable pre-dispatch evidence/write recovery, dispatch-time tariffs and concurrent budget admission; whole PRD-03 remains OPEN. |
| LLM non-analyzer journal capture — DONE | Every `chat_with_fallback` try (agent chat, learning, reflection, prompt suggestion; unlabelled `chat`), the agent model-test tool and the digest summary write their own tenant-bound journal row with a fresh dispatch identity, raw-provider token evidence and three-state pricing; failed tries are `failed_transport`/undefined, pre-dispatch refusals are not paid attempts, and journal failure never changes the result. Projections and the cap metric are unchanged, never summed with the journal. | [PR #163](https://github.com/Starck43/social-media-ai/pull/163) (implementation `5759035`; owner-local 139 passed / 1 pre-existing order-dependent `test_compact_reports` failure reproduced on dev); regression `tests/test_scoped_attempt_journal.py` | Admin connection tests (no operator payer), durable pre-dispatch receipts, cancellation/process-loss evidence and atomic concurrent budget admission. |
| Web and operations | Truthful job outcomes, secure cookies outside development, runtime readiness, conservative recovery guidance and redacted `/app/jobs` backlog/stale-worker signals. | PRs #27, #40, #67, #68, #110 | External alerting, repeatable staging install/restore and release evidence packet. |
| Development workflow | Canonical local/remote skills, collaboration rules and compact handoff format. | PRs #103, #104; commits `7833ac6`, `84ee16d` | Keep instructions aligned with the canonical document set. |
| Web chat UX | `/app/chat` offers one-tap quick replies: the agent may end a reply with a trailing `[[options: …]]` line (validated: up to 6 labels, 60 chars, deduplicated), rendered as buttons under the latest answer; pending confirmations always show «Да»/«Нет». A button sends its label as an ordinary message, so permission, confirmation and cost paths are unchanged; messenger replies get the marker stripped and a plain-text choices hint. In-log thinking indicator with staged label and elapsed time, restructured `/help`, empty-state starter cards, 14px desktop composer font, typed-text send fix, and a confirmed `source_enable` tool (`source.update`). | [PR #171](https://github.com/Starck43/social-media-ai/pull/171) (owner-approved after manual browser check at `427ef19`; remote no-DB 22 passed at `44a4e13`) | Telegram/MAX inline keyboards, streaming replies and DB-backed chat/registry regression run remain open. |
| Run-status modal | `/app/tasks` run modal shows real per-source progress for collect/analyze (bounded snapshot in the running job's `result["progress"]`, written under the heartbeat claim predicates and shown only for the current attempt; no migration), S/M/L window sizes and a background mode. Cancel is tenant-scoped, CSRF-protected and needs `agenttask.update`: a job that has not started is cancelled before start; a running collect keeps or discards this run's staged rows (rows staged after the cancel are settled once when the handler stops); analyze cancel has no delete option; retry-pending jobs are not cancellable from the modal. A stopped claim halts the handler at the next source boundary. | [PR #169](https://github.com/Starck43/social-media-ai/pull/169) (owner-local at `aa490d9`: 18 new tests and 55 test_schema cancel/progress scenarios passed; owner manual browser check of sizes, background mode, cancel confirmation and cancelled view; focused and full-suite failure sets identical to dev `15669ae`) | Handlers stop only at source boundaries; the cancelled run's external effects stay unconfirmed. |

## Current handoff

- Analyzer accounting certainty (PR #118, merge `ab21917`) is **DONE, integrated**.
  Owner-local 28/20/4 tests were accepted after a Black-only test correction at
  `e62bf40`; review/formatter/baseline details remain in the PR. Remote checks
  were static readback/diff/merge checks, not Python, database or provider tests.
- Owner three-state pricing decision and all-path admission contract (PR #119,
  merge `852ba14`) are recorded. This documentation does not implement admission
  or certify the runtime price policy. Explicit provider/table zero is FREE;
  provider-reported cost arbitrates; missing price is UNDEFINED, separately tracked
  and not silently free. Undefined-price dispatch is permitted by owner policy.
- Provider-reported analyzer pricing and three-state runtime mapping (PR #125,
  merge `d2b687d`) are **DONE, integrated**. Owner-local evidence at accepted head
  `08c53e1` covers 83 tests and isort; Black/compileall evidence was reused from
  unchanged relevant files at `16ff8b1`. PostgreSQL and full suite were NOT RUN.
  Statistics remain captured-journal-only, not full spend or a new budget cap.
- Strict journal replay/error acknowledgement ([PR #129](https://github.com/Starck43/social-media-ai/pull/129),
  merge `b0582b0`) is **DONE, integrated**. Accepted owner-local evidence at
  `196ce7b`: 108 methods, compiler/formatters and nine standalone PostgreSQL
  checks passed. Full suite NOT RUN; faults were injected, not real network-loss
  proof. No schema setup/reset/migration was performed; synthetic test rows retained.
- Preparation-receipt storage and frozen tariff basis ([PR #137](https://github.com/Starck43/social-media-ai/pull/137),
  merge `9f76c81`, tested head `e8d335e`) and corrected PostgreSQL verifier
  ([PR #154](https://github.com/Starck43/social-media-ai/pull/154), merge `e13905d`,
  tested head `69f95c9`) are **DONE, integrated; storage NOT ACTIVATED**.
  Accepted owner-local evidence: 143 foundation tests; 29 verifier tests,
  compileall and focused Black/isort exit 0 at the exact verifier SHA; six PG
  assertions PASS (scoped definition/model/catalog parity, transaction-local
  guard refusal with rollback, stored-key tenant collision, pre-commit rollback
  and post-successful-commit injected ack loss/replay, two-session barrier races,
  preflight-to-final content fingerprints). Security/fresh merge compatibility passed.
  Approved test-only 0093 definition application was report-attributed; public
  version stays 0092, old journal 79, enabled guards and 49 receipts retained
  after five new synthetic records. Do NOT rerun the old 44-row command or clean up.
  Earlier #137 verifier committed guard disable/restore, leaving one interrupted
  disabled interval; #154 fixes the proof method but does not certify historical
  integrity during that interval. Faults are injected, not real network loss;
  concurrency is same-process/two-session, not multi-process/load evidence.
  Stock Alembic check/production migrator/full suite, runtime and cutover are not
  certified. Fresh remote refs independently resolved the local SSH-fetch gap.
  Whole PRD-03 remains **OPEN**: all-path capture, durable pre-dispatch receipts,
  dispatch-time tariff snapshots, recovery, reservation/settlement and PostgreSQL
  concurrency/cutover evidence still require completion. See the existing
  [pipeline contract](AI_PIPELINE_AND_PROMPTS.md#usage-and-cost-boundary).
- Platform rate-limit handling in collection ([PR #143](https://github.com/Starck43/social-media-ai/pull/143),
  merge `79a5cd1`) is **DONE, integrated**. Owner-local verification at accepted
  head `28036a8`: command 4 (`tests/test_telegram_durable_admission.py`) passed
  29 tests after the stub-settings fix on that head; merge tree against dev was
  clean. No schema migration, provider or live calls were performed.
- Immutable outcome-event storage [Draft PR #168](https://github.com/Starck43/social-media-ai/pull/168)
  is **BLOCKED, NOT merged/activated**, primary head `f8cbe69`. Unchanged
  foundation `5511a69`: remote 43 tests, owner-local 116/registry checks PASS.
  Application runner: 18 remote no-DB tests/compiler/scoped formatters PASS.
  Approved option-(b) attempt stopped BEFORE DDL on shared-journal drift;
  [owner report](https://github.com/Starck43/social-media-ai/pull/168#issuecomment-6134825709)
  diagnoses 49 receipts (full hash unchanged), 188 journal, 362 tenants,
  public0092, intact0093, absent0094. Historical journal/tenant subset integrity
  is not established by the new whole-table digest. Next: agreed idle window,
  fresh read-only baseline confirmation, then one revised pinned application.
  Auxiliary prepared verifier `c1320ac` adds only two files and has 16 remote
  no-DB tests/compiler/scoped formatters PASS; not applied to the primary head
  or run against PG. Synthetic writes/rollback-only probes require separate
  approval and successful application evidence. No runtime/admission/settlement
  or full PRD-03 acceptance. Exact hashes, commands and handoff are in the PR.
- Owner reports test-only 0093 definition application; no working-schema migration,
  schema reset, live activation or deployment is claimed.

## Recording future delivery

Add or update one area row only when a merge changes a durable project boundary.
Link the PR and state the remaining boundary. Keep command output, individual test
counts, patch hashes and abandoned approaches in the PR rather than this file.
