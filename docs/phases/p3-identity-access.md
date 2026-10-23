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

- [ ] P3-T93 — personal LLM provider/model ownership across a user's workspaces;
  accept: explicit reviewed legacy ownership map, owner-scoped CRUD/defaults/fallback,
  atomic provider+models bundle, no secret DTOs, fresh pre-dispatch owner checks;
  depends: none; owns: LLM schema/managers, personal catalog service, migration 0109,
  API/backend contracts; owner: personal-catalog agent; status: READY — merged and
  acceptance-complete (see the acceptance handoff below; DONE pending Codex formal mark).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).
- [ ] P3-T94 — atomically reject foreign/missing task source/scenario IDs;
  accept: owner row and all linked rows locked/validated before writes, no partial
  replacement, normal manager and web boundaries covered; depends: none;
  owns: task/source binding manager and web validation (coordinate PR #217);
  owner: personal-catalog agent; status: READY — merged and
  acceptance-complete (see the acceptance handoff below; DONE pending Codex formal mark).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).
- [ ] P3-T95 — numeric chat model FK and shared owner/tier/provider eligibility;
  accept: no name fallback, consistent settings options/save/probe/runtime,
  explicit default vs selected model indicators, no cross-user fallback;
  depends: P3-T93; owns: tenant chat model FK, runtime, Agent settings adapter;
  UI provider management is owned by ui/llm-provider-settings;
  owner: personal-catalog agent; status: READY — merged and
  acceptance-complete (see the acceptance handoff below; DONE pending Codex formal mark).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).

- [ ] P3-T96 — personal provider/model settings UI with explicit default and chat-selection markers;
  accept: owner-only sparse atomic bundle save, separate shared default action, integer
  chat model IDs, desktop/mobile light/dark validation; depends: P3-T93, P3-T95;
  owns: settings_llm.py, settings.html/LLM partials/assets and UI tests;
  owner: remote UI session (`ui/llm-provider-settings`); status: READY — merged and
  acceptance-complete (see the acceptance handoff below; DONE pending Codex formal mark).
  Evidence: [ownership implementation](../validation/personal-llm-ownership.md).

### P3-T93–P3-T96 acceptance handoff (READY → DONE)

Accepted code: coordinator review of the integrated line found no blocking
findings. [PR #230](https://github.com/Starck43/social-media-ai/pull/230) was
merged 2026-10-23 19:40 UTC (`447c755`) and
[PR #229](https://github.com/Starck43/social-media-ai/pull/229) 19:46 UTC;
**dev = `b343536`** (parents `30e5d9b` + `447c755`), whose tree equals the
tested UI tree `c5a72aeae0e6d86adf5a951e7463c8e4c104f3c3` exactly (independent
merge preflight in the #229 comment at 19:45 UTC).

Acceptance evidence at the tested trees (details and SHA timeline in
[personal-llm-ownership](../validation/personal-llm-ownership.md)):

- Real-DB scopes: queue suites **53/53**, catalog PostgreSQL **9/9**, UI ASGI
  **7/7** on owner-local isolated targets (backend content unchanged through
  the shell-only PR #231 merge, `6556aaf` → dev `30e5d9b`).
- Browser gate at `56fb209`: Chromium **24/24 catalog + 104/104 layout + 53/53
  isolated checks** (merged UI session, exact-head report) and WebKit 18.2
  **104/104** (owner-local, including the 16 agent-mode keyboard/mouse cases
  that failed 88/104 before PR #231).
- Offline consolidated doubles 63 passed + 34 subtests for the T12/T13-adjacent
  contracts do not belong to this phase's scope.

Out of this acceptance (explicitly): production/public migration 0109, data
transfer and deployment; these remain owner-gated operations. The guarded 0109
window itself was exercised and reverted on the shared test schema with a
verified backup during the T12/T13 acceptance (separate lane, [#217
comment](https://github.com/Starck43/social-media-ai/pull/217#issuecomment-6373408286)),
leaving `test_schema` at 0108.

DONE/phase approval remain with Codex; no one marks DONE on their own lane.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
