# P7 — Reproducible operation (gate PRD-04)

Goal: one owner per migrator/scheduler/listener; private database; recoverable
encrypted credentials; staging-backed install/upgrade/rollback/restore proofs.

## Tasks

- [ ] P7-T01 — loopback-only database binding before production release;
  accept: checked-in PostgreSQL host publish restricted to `127.0.0.1:5432:5432`
  in `docker/docker-compose.yml`, container `db:5432` and local-owner tools
  preserved, network isolation proven with local/staging evidence;
  depends: none; owns: `docker/docker-compose.yml`; owner: owner; status: TODO —
  deferred pre-production; do NOT start/restart Compose (API startup runs Alembic)

- [ ] P7-T02 — produce one staging-backed install, upgrade, rollback, restore
  or runtime-ownership proof; accept: documented staging evidence for the
  chosen operation; depends: staging availability; owns: docs/evidence lane;
  owner: unassigned; status: TODO

- [ ] P7-T03 — use canonical domain model imports and direct manager registration;
  accept: public/legacy/domain class identity, manager bindings and schema preserved;
  focused import checks completed; depends: none; owns: `app/models/`,
  `tests/test_model_bootstrap.py`, `docs/MODELS.md`; owner: codex; status: BLOCKED
  — implementation reviewed on `a3a3262` + working diff; Black, isort checks and
  `git diff --check` passed. OpenCode collection/execution pending: existing pytest
  PID 47047 uses shared `test_schema` and overlapping model-layout scopes. Resume
  the assigned `.agents/tmp/test-task-model-domains.md` after that run finishes;
  no migrations or schema changes required.

- [x] P7-T04 — document routine OpenCode routing and separate pytest route;
  status: DONE — 2026-10-15; TOML and `git diff --check` passed; OpenCode API
  unavailable; no pytest required.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
