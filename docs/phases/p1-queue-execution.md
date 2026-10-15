# P1 — Truthful queue execution (gate PRD-05)

Goal: no handler starts after claim loss; recovery cannot duplicate an
unresolved external effect; execution outcome remains truthful.
Delivered: claim renewal, preflight, heartbeat, atomic-trigger slices;
source-aware explicit-run admission (PRs #192, #193 — bounded slices merged;
raw new-input wakeups retained). Real PostgreSQL race/rollback and ASGI
workspace acceptance remain unperformed (P1-T01).

## Tasks

- [ ] P1-T01 (AUD-PR193) — prove source admission on PostgreSQL across
  concurrent connections, rollback and ASGI workspace scope; accept: real
  two-session race admits exactly one job, aborted admission leaves no
  occupancy, another workspace cannot run the task; depends: none;
  owns: `tests/test_task_run_now.py`; owner: codex; status: BLOCKED —
  Kilo emitted `sysmon request failed: sysmond service not found`, never
  returned TEST RUN and was interrupted; DB/test-process state uncertain.
  Next input: read-only confirmation that no child/pytest process is active
  and `test_schema` is safe (docs/RULES.md R4/R8), then rerun via the primary
  OpenCode test route (R10).

- [ ] P1-T02 — select and close the next bounded recovery or admission gap;
  accept: chosen gap verified against current code, fix keeps claim-loss,
  duplicate-effect and truthful-outcome invariants, focused tests via R10;
  depends: none; owns: per selection; owner: unassigned; status: TODO

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
