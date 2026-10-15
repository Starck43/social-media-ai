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

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
