# Development phases

Single global stage list. Each phase has one task file; open tasks are mirrored
by ID + link in the root [ROADMAP.md](../../ROADMAP.md) registry (no text
duplication). Detailed delivery evidence lives in Git/PRs and
[IMPLEMENTATION_STATUS.md](../IMPLEMENTATION_STATUS.md); readiness gates in
[BUSINESS_PRODUCTION_READINESS.md](../BUSINESS_PRODUCTION_READINESS.md).

Named `phases`, not `workflows`: CI uses `.github/workflows`, and "multi-step
business workflow" is a distinct product concept (see AGENTS.md).

## Conventions

- Task ID: `P<phase>-T<nn>`; legacy IDs kept in parentheses (e.g. `(AUD-01)`).
  Scope of that exception: registry rows and phase headings only, because a
  legacy ID is how older audits and PRs point at a task. It does not extend to
  code comments, docstrings, fields, labels, filenames or phase titles, which
  `AGENTS.md` keeps free of milestone/PRD numbering.
- Task statuses (UPPERCASE, GitHub-style): `TODO`, `IN_PROGRESS`, `BLOCKED`,
  `READY` (checks complete, awaiting review/merge), `DONE`, `DROPPED`.
  Checkbox `[x]` only for `DONE`.
- Task row format (one observable outcome each):

      - [ ] P1-T01 — <outcome>; accept: <evidence condition>; depends: <IDs|none>;
        owns: <paths>; owner: <agent|unassigned|product-owner>; status: TODO

  `product-owner` marks work that only the owner can decide or run (deployment
  approval, production checks); it is not an agent lane.

- Lifecycle: task opens in its phase file + ROADMAP row → agent claims it
  (owner, `IN_PROGRESS`) → done work verified at the tested SHA → `READY` →
  Codex (main agent) reviews/accepts → `DONE`, ROADMAP row removed. `BLOCKED`
  keeps the blocker and the next required input. `DROPPED` needs owner consent.
- Phase closes (`[x]`, DONE in the index) only when all its tasks are `DONE`
  and Codex signs the phase approval block in the phase file.
- Exclusive file ownership per task; serialize shared-file lanes; shared-DB
  acceptance stays sequential (docs/RULES.md R8/R10).

## Index

| Phase | Scope (gate) | Priority | Status | Tasks |
| --- | --- | --- | --- | --- |
| P1 | Truthful queue execution | 1 | DONE | [p1-queue-execution.md](p1-queue-execution.md) |
| P2 | Complete usage and spend | 2 | IN_PROGRESS | [p2-usage-spend.md](p2-usage-spend.md) |
| P3 | Identity and approvals | 3 | IN_PROGRESS | [p3-identity-access.md](p3-identity-access.md) |
| P4 | Tenant-safe delivery | 4 | OPEN | [p4-delivery.md](p4-delivery.md) |
| P5 | Safe AI boundaries | 5 | OPEN | [p5-ai-boundaries.md](p5-ai-boundaries.md) |
| P6 | Lifecycle and privacy | 6 | OPEN | [p6-lifecycle-privacy.md](p6-lifecycle-privacy.md) |
| P7 | Reproducible operation | 7 | OPEN | [p7-operations.md](p7-operations.md) |
| P8 | First value and support UI | 8 | IN_PROGRESS | [p8-first-value-ui.md](p8-first-value-ui.md) |
| P9 | Safe activation and platform access | 9 | OPEN | [p9-safe-activation.md](p9-safe-activation.md) |
| P10 | Source-audit hardening | P0 items first | OPEN | [p10-audit-hardening.md](p10-audit-hardening.md) |

Phase status: `OPEN` (not started), `IN_PROGRESS`, `DONE` (all tasks DONE +
Codex approval). Dependencies and disabled-feature safeguards override the
priority column; product expansion starts only after the applicable gates are
accepted.
