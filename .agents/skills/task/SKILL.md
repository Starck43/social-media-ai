---
name: task
description: "Use when assigned one project task ID to implement, continue, fix or prepare for review; owns implementation, evidence and handoff."
---

# Task executor

Declare this skill at session start. A task ID and action are enough: resolve the
ROADMAP row, card, repository and task branch from context; ask only for
unresolvable details. Read AGENTS, one ROADMAP row and the card, then only the
linked contracts the change needs.

Work only on assigned paths in a task-ID branch/worktree. The primary checkout is
the owner's: never switch, move or commit branches in it; open a worktree instead.
Claim the row before editing and update status, owner and dialogue link through
its custodian. Record source revision, documents actually read (path/section and
purpose), checks and outcomes in the card or PR body.

## Role split

You own implementation, the registry claim, evidence and the handoff. You do not
own acceptance: no self-acceptance, no verdict, no merge on your own authority.
A green suite is evidence, not acceptance. An orchestrator that also audits must
load `orchestrator-task` and keep the phases separate.

## Delegation

Load `test-runner` for tests and `audit` for the reviewer contract; this skill
does not restate them. Fall through only when the previous route is unavailable
or fails, and record the route and model that actually ran.

| Work | Route |
| --- | --- |
| Bounded research, call-site inventory | `explore` or `general` |
| One exact test command | `tester` → `free-pi` → own model |
| Independent verdict | `perch` → `free-pi` → `auditor` |
| DB/integration scenario on real PostgreSQL | local DB lane; `remote-agent-helper` when the task is remote |
| Acceptance, merge, registry closeout | auditor only, never the implementer |

A child gets the path, exact section, command and expected output — never this
transcript. If you cannot check its result in one read, the task was too broad.

## Budget

- Use at most 1–2 children per task; each gets a compact, self-contained handoff
  (card path, frozen SHA, owned files, exact commands), not a fork of this
  session. A forked child re-sends 50–100k of inherited context on every turn.
- Preflight a third-party route once before spawning it (`perch status`,
  `free-pi` availability). Do not let a child spend a turn discovering that its
  route is down.
- Stop a child at ~1M input tokens or ~20 turns and narrow the assignment; an
  unbounded child can outspend the whole task.
- Implement on the cheap/medium profile; reserve high reasoning for a final
  review of a small diff.
- An owner prompt that adds "along the way" work is split into a separate task
  or lane; it is never absorbed silently into the current one.

## Lifecycle

1. Claim, then implement on the task branch.
2. Run the card's required checks through the test route. Record exact commands,
   tested SHA, counts, unrun checks and DB cleanup.
3. Report READY only when every required check passed and the Evidence record is
   in the card/PR. The record maps each acceptance criterion to its code path,
   the test that exercises it and the observed result; mocked evidence is
   labelled mocked.
4. Send the `audit` handoff. On `CHANGES_REQUIRED` apply the corrections and
   repeat the test/audit cycle. On `BLOCKED`, record the exact blocker and next
   step.
5. On `ACCEPTED` the auditor owns integration, merge and registry closeout under
   [acceptance](../../../docs/process/COLLABORATION.md#acceptance). Hand over the
   exact branch/PR and stop editing the registry. If the owner explicitly assigns
   the closeout to you, record the independent verdict first and make only the
   registry-only closeout commit.

Keep IN_PROGRESS on actionable failures; use BLOCKED only when the solution or
required acceptance cannot proceed. Store handoff in the card/PR, not `/tmp` or
ignored `.agents/tmp`. Follow
[COLLABORATION](../../../docs/process/COLLABORATION.md) only as needed; it is the
source of truth and wins on any conflict with this skill.
