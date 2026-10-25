---
description: READY acceptance — /review <task-id>
---
Accept READY task $ARGUMENTS per the project contract: AGENTS.md
“Accept a READY task” + docs/COLLABORATION.md “### READY task acceptance”.
Find the task in ROADMAP.md and its phase file; review the diff at the
recorded SHA against the acceptance criteria. Reuse the recorded test
evidence — do NOT re-run the full suite. For token economy you may delegate
the read-only diff sweep to the routine-worker subagent (ACCEPT block,
≤120 tokens). On a real defect: fix, then re-run only the affected scopes via
the primary test-runner subagent (R10). Docs-only diffs: no pytest at all.
Never re-run scopes that already passed at the same SHA. Reply compact:
verdict, action taken (DONE / fix + focused re-test / needs-owner-decision),
next step.
