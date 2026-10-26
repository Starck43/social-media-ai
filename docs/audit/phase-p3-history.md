<!-- Preserved source: docs/tasks/p3-identity-access.md; historical evidence only. -->

# P3 — Identity and approvals (gate: identity, permissions and approvals fail closed)

Phase scope and accepted-task history. [Active index](../../ROADMAP.md);
detailed cards are siblings in this directory. [Product direction](../PRODUCT_PLAN.md#foundation-directions).
Historical instructions/permissions below are evidence, not current policy.

Goal: unknown, removed or revoked actors fail closed; approval is actor-bound,
versioned and single-use.
Delivered (DONE, accepted locally with real PostgreSQL/ASGI evidence — see
docs/ACCESS_MIGRATION.md and Git history at `edbe0fc`):

<a id="p3-t90"></a>
- [x] P3-T90 (ACCESS-CATALOG) — separate fixed platform/workspace roles and
  stable domain resources; migration 0096 preserves assignments. status: DONE
<a id="p3-t91"></a>
- [x] P3-T91 (ACCESS-POLICY) — unified admin/API/web and transactional team
  controls; cross-workspace, operator/member, revocation, last-owner cases.
  status: DONE
<a id="p3-t92"></a>
- [x] P3-T92 (ACCESS-MEMORY) — personal memory and explicitly granted team AI;
  transcript/facts/watermarks and approval-revocation isolation. status: DONE

Out of that acceptance: existing `alembic check` drift, full-suite/browser/
production acceptance, real-DB pending-confirmation end-to-end run.

## Closed tasks

<a id="p3-t01"></a>
- [x] P3-T01 — finish the digest send permission parity acceptance; accept:
  delegated acceptance via R10 at the exact current integrated source revision;
  preserve the digest permission parity contract already in dev (`6d16436`);
  owner: remote Notion agent (`fix/p3-t01-digest-send-parity`); status: DONE —
  accepted 2026-10-25, [PR #233](https://github.com/Starck43/social-media-ai/pull/233)
  merged as `f46de6e` (ancestry verified in `origin/dev`; integration revision
  `cf36bd4`). PR #176 stays closed and unmerged; it was not reused or rebased.
  Remote acceptance (docs-only branch on top of `4bc03d0`, code identical to the
  pinned `c1069ef`): parity holds on all three surfaces — the agent tool
  `digest_send_now` requires `notifications.digest_run.create` with
  confirmation while the preview keeps `view`, the web
  `POST /app/digests/send-now` guards `digestrun/create` through `WebPerms.can`
  with no superuser shortcut, and the CLI reaches the same
  `build_and_publish` only inside the trusted operator scope. Viewer,
  update-only, inactive, missing-identity, platform-admin-viewer and
  revoked-grant cases fail closed; a confirmed create-holder dispatches once.
  Checks: `tests/security/test_digest_send_permissions.py` 10/10 exit 0;
  `tests/security/test_tool_dispatch_permissions.py` 16 ran with one
  pre-existing `tests/security` failure (registered as P10-T19), not caused or
  fixed here; `tests/notifications/test_digest_send_current_boundaries.py` 37 ran
  with one stale expectation, reported and not fixed.
  The deferred DB/ASGI acceptance was run by the local helper on the
  integration revision `cf36bd4` (identical digest/confirmation code, exclusive
  `test_schema`, sequential): `tests/notifications/test_digest_send_postgres_acceptance.py`
  plus `test_digest_send_asgi_acceptance.py` → **42 passed, 1 failed** in 118 s.
  The single failure, `test_real_cas_rejects_foreign_scope_and_stale_replaced_intent`,
  asserts that a replaced pending intent gets a new `intent_id`; `82555f2`
  deliberately dedupes a repeated identical intent ("a staged confirmation is a
  visible question, never a twin"), so the assertion predates that contract. It
  is not a permission-parity failure. Evidence:
  [validation](p3-t01-digest-parity.md) and
  [PR comment](https://github.com/Starck43/social-media-ai/pull/233#issuecomment-6400712714).
  The two stale digest-confirmation expectations (unit and DB twin) are
  registered as P10-T20.

<a id="p3-t02"></a>
- [x] P3-T02 — reject a stale personal provider credential at dispatch;
  accept: revalidate the client's selected provider/model and API-key identity
  at the final application check immediately before outbound HTTP; a provider
  disable or key rotation committed while billing admission is in progress
  must fail closed without sending; a request that passed the final check and
  was already sent is not claimed to be cancellable; tenant/model ownership
  and billing admission remain intact; focused mocked-client tests via R10;
  depends: P3-T93; owns `app/services/ai/llm_client.py` and its focused tests;
  owner: codex; status: DONE at dev `82a95a8580a87f75995aeb3f9836354855d42051`.
  Candidate audit found a
  time-of-check/time-of-use window between cached-key validation and provider
  HTTP send. The same-owner cross-workspace confirmation replay candidate was
  separately reviewed and rejected: session, tenant, actor and one-use grant
  checks bind the authorization to its originating workspace.
  Fix: all OpenAI-compatible, Anthropic and custom analyze/chat paths revalidate
  the fresh catalog owner, model/provider state, selected endpoint and API-key
  identity after billing admission. A stale selection records a proven
  `not_dispatched` outcome and cannot reach the HTTP client; if recording that
  release fails, the reservation remains held. No schema change.
  OpenCode/River focused no-DB evidence at base dev `d1e26832e72145ed69a3d5b957acab0f3bcf5ffe`
  with the three task files dirty: `test_credential_revoked_after_admission_releases_reservation_without_http`
  and `TestProviderCostUsageProjection.test_all_actual_adapter_chat_and_analyze_calls_use_provider_totals`
  — 2 passed, 0 failed/skipped. The task-scoped blobs in accepted code commit
  `82a95a8580a87f75995aeb3f9836354855d42051` exactly match the tested worktree
  (`llm_client.py` `2125004…`, transport test `40a6aac…`, usage test
  `48560b8…`). A post-commit rerun was attempted but not executed: OpenCode
  blocked before pytest and the secondary Kilo route stalled before starting a
  test runner. No DB test or schema operation was performed. Accepted by Codex
  after reviewing the exact committed diff and matching tested blobs.

<a id="p3-t93"></a>
- [x] P3-T93 — personal LLM provider/model ownership across a user's workspaces;
  accept: explicit reviewed legacy ownership map, owner-scoped CRUD/defaults/fallback,
  atomic provider+models bundle, no secret DTOs, fresh pre-dispatch owner checks;
  depends: none; owns: LLM schema/managers, personal catalog service, migration 0109,
  API/backend contracts; owner: personal-catalog agent; status: DONE (owner-local migration/guard/catalog evidence at recorded source SHAs; final code integrated in dev b343536; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](personal-llm-ownership.md).
<a id="p3-t94"></a>
- [x] P3-T94 — atomically reject foreign/missing task source/scenario IDs;
  accept: owner row and all linked rows locked/validated before writes, no partial
  replacement, normal manager and web boundaries covered; depends: none;
  owns: task/source binding manager and web validation (coordinate PR #217);
  owner: personal-catalog agent; status: DONE (atomic binding evidence at recorded source SHAs; final code integrated in dev b343536; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](personal-llm-ownership.md).
<a id="p3-t95"></a>
- [x] P3-T95 — numeric chat model FK and shared owner/tier/provider eligibility;
  accept: no name fallback, consistent settings options/save/probe/runtime,
  explicit default vs selected model indicators, no cross-user fallback;
  depends: P3-T93; owns: tenant chat model FK, runtime, Agent settings adapter;
  UI provider management is owned by ui/llm-provider-settings;
  owner: personal-catalog agent; status: DONE (owner/tier eligibility and ASGI evidence at recorded source SHAs; final code integrated in dev b343536; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](personal-llm-ownership.md).

<a id="p3-t96"></a>
- [x] P3-T96 — personal provider/model settings UI with explicit default and chat-selection markers;
  accept: owner-only sparse atomic bundle save, separate shared default action, integer
  chat model IDs, desktop/mobile light/dark validation; depends: P3-T93, P3-T95;
  owns: settings_llm.py, settings.html/LLM partials/assets and UI tests;
  owner: remote UI session (`ui/llm-provider-settings`); status: DONE (ASGI evidence at recorded source SHA and browser evidence at #230 head56fb209; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](personal-llm-ownership.md).

### P3-T93–P3-T96 integration and acceptance checkpoint

PR #230 merged into the feature at `447c755b963462d1f8dc3e25f50b496699796f1c`;
PR #229 then merged normally into dev at
`b34353645fe37de53c97c4d022c05af0044dc8ba` (tree
`c5a72aeae0e6d86adf5a951e7463c8e4c104f3c3`). The final tree is identical to
the approved #230 head `56fb209704e2ada7510f26d77b2d3006135e8462`; the merges
added no content beyond that reviewed tree. Exact-SHA evidence and limitations
remain in [the ownership evidence ledger](personal-llm-ownership.md).
Codex accepted all four tasks as DONE on 2026-10-23 at dev
`b34353645fe37de53c97c4d022c05af0044dc8ba` after exact-tree and conflict review.
This phase remains open for its other tasks.
The code merge did not upgrade a working/deployment schema or deploy the code.
Prior isolated migration and backfill acceptance is retained in the evidence
ledger; operational rollout remains a separate authorized step.
