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

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
