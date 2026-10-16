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
  accept: current-dev real DB runtime confirmation/committed CAS and ASGI
  send-now membership/CSRF/tenant acceptance completed sequentially through R10
  at recorded HEAD plus effective test hashes; reuse the accepted 29 access
  cases, not the obsolete `0c18bab` verification tree. Code already in dev
  (`6d16436`); PR #176 remains Draft pending acceptance/compatibility review;
  depends: none; owns: digest acceptance tests and bounded runner-mode alignment;
  owner: remote Notion agent; status: IN_PROGRESS — test candidates prepared
  against dev `19ce6e1`; DB/ASGI collection/execution still pending. The relayed
  37 mock PASS is not real integration acceptance. No duplicate runs or parallel
  shared-schema writers; only coordinator acceptance may mark DONE.

- [ ] P3-T02 — close one actor/workspace/tool/write gap including revocation,
  expiry or replay where applicable; accept: gap verified in current code,
  fail-closed behavior proven with focused tests; depends: none;
  owns: per selection; owner: unassigned; status: TODO

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
