# P4 — Tenant-safe delivery (gate: delivery stays tenant-safe)

Goal: successful recipients are not resent; ambiguous sends stop for review;
recipients and credentials belong to the owning workspace.
Delivered: legacy partial-delivery stop, zero-success terminal outcome,
scheduled/forced publisher fences and lease-loss evidence (PRs #106, #112,
#113, #115, #120).

## Tasks

- [ ] P4-T01 — staging operator-recovery evidence; accept: documented staging
  run proving operator recovery of an ambiguous/stopped delivery without
  resending to successful recipients; depends: staging availability;
  owns: docs/evidence lane; owner: unassigned; status: BLOCKED — no staging
  environment is available, so it cannot be claimed as written; it completes
  only after the deployment stage that P6-T01 and P7-T01 wait for, or it
  should be DROPPED together with them.

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
