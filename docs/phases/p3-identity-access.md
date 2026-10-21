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
  accept: remaining real DB/ASGI acceptance on a reviewed current-dev tree
  completed via the current local test route; code already in dev (`6d16436`).
  Historical pin `0c18bab` and PR #176 Draft wording are superseded: #176 and
  #202 are now closed without merge; #202 records that the acceptance modules
  are already in dev, not that acceptance passed. Preserve earlier evidence.
  Depends: none; owns: current digest acceptance lane; owner: remote Notion
  agent; status: TODO — no duplicate runs, no parallel shared-schema writers.
  [Current source/PR review](../audits/roadmap-current-dev-review.md).

- [ ] P3-T02 — close one actor/workspace/tool/write gap including revocation,
  expiry or replay where applicable; accept: gap verified in current code,
  fail-closed behavior proven with focused tests; depends: none;
  owns: per selection; owner: unassigned; status: TODO

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
