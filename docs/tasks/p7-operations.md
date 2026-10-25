# P7 — Reproducible operation (gate: operations are reproducible from evidence)

Phase scope and accepted-task history. [Active index](../../ROADMAP.md#p7);
detailed cards are siblings in this directory. [Product direction](../PRODUCT_PLAN.md#foundation-directions).
Historical instructions/permissions below are evidence, not current policy.

Goal: one owner per migrator/scheduler/listener; private database; recoverable
encrypted credentials; staging-backed install/upgrade/rollback/restore proofs.

## Closed tasks

- [x] P7-T03 — use canonical domain model imports and direct manager registration;
  accept: public/legacy/domain class identity, manager bindings and schema preserved;
  focused import checks completed; depends: none; owns: `app/models/`,
  `tests/test_model_bootstrap.py`, `docs/MODELS.md`; owner: codex; status: DONE —
  2026-10-16; `tests/test_model_bootstrap.py` PASS 17/17 on dirty diff at HEAD
  da83366; Black/isort check-only and `git diff --check` clean. Wider model-layout
  run at the same HEAD: FAIL, 138 passed/20 failed; failures assert leaf-class app
  labels, schema fingerprints and relationship contracts. This refactor changes only
  facade/bootstrap/package docs and adds bootstrap checks; leaf declarations and tests
  are untouched, so the wider result is not claimed green. No migrations or schema
  changes required.

- [x] P7-T04 — document routine OpenCode routing and separate pytest route;
  status: DONE — 2026-10-15; TOML and `git diff --check` passed; OpenCode API
  unavailable; no pytest required.
