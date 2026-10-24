# P3 — Identity and approvals (gate PRD-02)

Goal: unknown, removed or revoked actors fail closed; approval is actor-bound,
versioned and single-use.
Delivered (DONE, accepted locally with real PostgreSQL/ASGI evidence — see
docs/ACCESS_MIGRATION.md and Git history at `edbe0fc`):

- [x] P3-T90 (ACCESS-CATALOG) — separate fixed platform/workspace roles and
  stable domain resources; migration 0096 preserves assignments. status: DONE
- [x] P3-T91 (ACCESS-POLICY) — unified admin/API/web and transactional team
  controls; cross-workspace, operator/member, revocation, last-owner cases.
  status: DONE
- [x] P3-T92 (ACCESS-MEMORY) — personal memory and explicitly granted team AI;
  transcript/facts/watermarks and approval-revocation isolation. status: DONE

Out of that acceptance: existing `alembic check` drift, full-suite/browser/
production acceptance, real-DB pending-confirmation end-to-end run.

## Tasks

- [ ] P3-T01 — finish PRD-02 digest send permission parity acceptance;
  accept: delegated acceptance via R10 at the exact current integrated source
  revision; preserve the digest permission parity contract already in dev
  (`6d16436`). PR #176 is closed and unmerged (live GitHub verification,
  2026-10-21); its historical verification pin `0c18bab` is not a requirement
  to test obsolete code. Depends: none; owns: digest parity acceptance lane;
  owner: remote Notion
  agent; status: TODO — no duplicate runs, no parallel shared-schema writers

- [ ] P3-T02 — close one actor/workspace/tool/write gap including revocation,
  expiry or replay where applicable; accept: gap verified in current code,
  fail-closed behavior proven with focused tests; depends: none;
  owns: per selection; owner: unassigned; status: TODO

- [x] P3-T93 — personal LLM provider/model ownership across a user's workspaces;
  accept: explicit reviewed legacy ownership map, owner-scoped CRUD/defaults/fallback,
  atomic provider+models bundle, no secret DTOs, fresh pre-dispatch owner checks;
  depends: none; owns: LLM schema/managers, personal catalog service, migration 0109,
  API/backend contracts; owner: personal-catalog agent; status: DONE (owner-local migration/guard/catalog evidence at recorded source SHAs; final code integrated in dev b343536; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).
- [x] P3-T94 — atomically reject foreign/missing task source/scenario IDs;
  accept: owner row and all linked rows locked/validated before writes, no partial
  replacement, normal manager and web boundaries covered; depends: none;
  owns: task/source binding manager and web validation (coordinate PR #217);
  owner: personal-catalog agent; status: DONE (atomic binding evidence at recorded source SHAs; final code integrated in dev b343536; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).
- [x] P3-T95 — numeric chat model FK and shared owner/tier/provider eligibility;
  accept: no name fallback, consistent settings options/save/probe/runtime,
  explicit default vs selected model indicators, no cross-user fallback;
  depends: P3-T93; owns: tenant chat model FK, runtime, Agent settings adapter;
  UI provider management is owned by ui/llm-provider-settings;
  owner: personal-catalog agent; status: DONE (owner/tier eligibility and ASGI evidence at recorded source SHAs; final code integrated in dev b343536; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).

- [x] P3-T96 — personal provider/model settings UI with explicit default and chat-selection markers;
  accept: owner-only sparse atomic bundle save, separate shared default action, integer
  chat model IDs, desktop/mobile light/dark validation; depends: P3-T93, P3-T95;
  owns: settings_llm.py, settings.html/LLM partials/assets and UI tests;
  owner: remote UI session (`ui/llm-provider-settings`); status: DONE (ASGI evidence at recorded source SHA and browser evidence at #230 head56fb209; accepted by Codex on 2026-10-23 at dev b343536).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).

### P3-T93–P3-T96 integration and acceptance checkpoint

PR #230 merged into the feature at `447c755b963462d1f8dc3e25f50b496699796f1c`;
PR #229 then merged normally into dev at
`b34353645fe37de53c97c4d022c05af0044dc8ba` (tree
`c5a72aeae0e6d86adf5a951e7463c8e4c104f3c3`). The final tree is identical to
the approved #230 head `56fb209704e2ada7510f26d77b2d3006135e8462`; the merges
added no content beyond that reviewed tree. Exact-SHA evidence and limitations
remain in [the ownership evidence ledger](../validation/personal-llm-ownership.md).
Codex accepted all four tasks as DONE on 2026-10-23 at dev
`b34353645fe37de53c97c4d022c05af0044dc8ba` after exact-tree and conflict review.
This phase remains open for its other tasks.
The code merge did not upgrade a working/deployment schema or deploy the code.
Prior isolated migration and backfill acceptance is retained in the evidence
ledger; operational rollout remains a separate authorized step.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
