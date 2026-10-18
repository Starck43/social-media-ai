# P6 — Lifecycle and privacy (gate PRD-06)

Goal: tenant data is isolated and auditable; deletion preserves evidence
required for unresolved effects and accounting.

## Tasks

- [ ] P6-T01 — data-retention rollout (code merged via PR #139);
  accept: deployment approved by owner, `ai_analytics` backed up if history
  matters, `DATA_RETENTION_MONTHS` confirmed (default 1 — first daily prune
  irreversibly deletes analyses older than cutoff), post-deploy verification
  recorded, then this row is removed; depends: owner deployment approval;
  owner: owner; status: BLOCKED — owner decision (2026-10-12): do not deploy yet

- [ ] P6-T02 — decide and implement one bounded retention, export, offboarding,
  credential-revocation or logging contract; accept: contract documented and
  implemented with isolation/audit evidence; depends: none;
  owns: per selection; owner: unassigned; status: TODO

- [ ] P6-T03 — add a workspace-admin-only action on the source page to delete
  only that source's raw staged `CollectedItem` rows; accept: explicit
  confirmation, server-side admin and CSRF checks, tenant/source scoped delete,
  refuse while collect/analyze work is pending or running, analytics and
  receipts untouched; add focused tests; no live DB cleanup; depends: none;
  owns: `app/web/sources.py`, `app/web/templates/web/source_detail.html`,
  `app/models/managers/collected_item_manager.py` and focused tests;
  owner: codex; status: IN_PROGRESS; evidence: diff review and `git diff --check`
  passed, compileall passed; focused pytest was not run because the OpenCode
  model quota was exhausted and the secondary Kilo task handoff was rejected.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
