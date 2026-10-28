# Development contract

Read only the section needed by the selected skill. Mandatory entry rules live
in [AGENTS](../AGENTS.md); this file owns detailed workflow and DB/test procedures.

## Tasks

ROADMAP owns one line per open item: ID/title linked to its card, status, owner,
priority/complexity and last status-changing dialogue or PR-comment link. Use `—` only for unknown
legacy links; a real session ID/resume command is valid when no URL exists.
Anyone may add a TODO/question. Reserve the next unused phase/task or question ID
against ROADMAP and phase/audit history; allocate IDs in numeric sequence, never
recycle them. Display the active queue in the priority order defined by ROADMAP,
respecting required predecessors and keeping explicit deferrals parked; phase
plans retain numeric task order. Priority/complexity do not authorize execution
or change a task status.

Before editing a solution, fetch `origin`, claim/create the row as IN_PROGRESS
with owner/dialogue, and add its card/parent-plan link. Publish only these registry
paths in a small commit based on current `origin/dev`; synchronize local `dev`
without overwriting other changes. If local dev is ahead, use a separate registry
worktree based on origin rather than pushing unrelated commits. A non-fast-forward
push requires fetch/reconcile and rechecking the claim; never force-push. Claim
publication to `origin/dev` is authorized by this workflow; solution publication
and merge still require the owner’s explicit choice. An offline/unpublishable claim
is local only: report the coordination limitation, never pretend it is visible.
Remote agents self-publish their scoped claim commit to `origin/dev` under the
same rules as local claims, always before any solution edit and never later with
the solution; they do not publish the solution, unrelated registry rows or
competing registry copies, and they never stop to wait for a custodian or the
owner between steps. A designated custodian may instead apply PR-comment status
requests and refreshes both directions before each transition.

Create `type/<task-id>-<topic>` from the agreed source revision. Independent
writers use exclusive paths and separate worktrees. Keep task implementation out
of integration `dev`; registry-only synchronization is the exception above.

The primary checkout is the owner's interactive workspace: agents never
switch, move or commit branches in it, and never leave it on an agent branch.
When the owner's checkout sits on a branch an agent needs, the agent opens a
worktree for that branch instead of moving the checkout. Verify the current
branch and dirty state before any commit; a commit belongs only in the
agent's own task worktree.

`docs/tasks/<id>.md` contains scope/acceptance, parent phase (or explicit orphan),
related task links, owned paths/dependencies, branch/PR, documents read and checks.
It links ROADMAP instead of copying current status. Phases live in `docs/phases/`:
product-parent backlink, goal, numbered plan, active-card links and accepted audit
links. Plan substantial work by creating/revising a numbered phase with continuity
and reasons; keep short standalone tasks as cards, not artificial stages.

## State machine

Open row statuses: `TODO`, `IN_PROGRESS`, `READY`, `BLOCKED`, `DEFERRED`;
questions use `TODO`/`OPEN`/`DEFERRED`. `DONE` is not a row status: acceptance
removes the row, and the audit record plus the phase line carry the DONE/date
summary.

- `TODO → IN_PROGRESS`: the claim commit is published as under Tasks, before any
  solution edit. A remote executor self-publishes its scoped claim commit (a
  designated custodian may instead apply the PR-comment request); the executor
  never idles waiting for either.
- `IN_PROGRESS → READY`: the executor (for remote work, the custodian from the
  PR comment) publishes the switch only when every required acceptance check
  passed, with the [Evidence](#evidence) record in the card or linked PR body.
  Actionable failure keeps the row IN_PROGRESS.
- `READY → DONE`: the auditor applies the [Acceptance](#acceptance) steps; the
  row and card are removed, the audit record and phase line remain.
- rejected, correctable: back to IN_PROGRESS; the auditor returns findings,
  required checks and a recommended fix. Remote findings ride PR comments,
  never the PR body, which stays executor-owned.
- rejected, cannot proceed: BLOCKED with reason, passed/failed/unrun checks and
  recommended fix in the linked audit diagnostic.
- owner defer: DEFERRED with the explicit choice, reason and resume branch/PR in
  the row; never BLOCKED solely for being postponed.
- owner descope without acceptance: only the owner's explicit decision; record
  it in the audit note or a Questions row, remove the row, keep the note.

## Evidence

Before READY, record in the card or linked PR body:

- **Solution:** behavior, affected files, source SHA, branch and dependencies.
- **Documentation read:** actual `path#section` and purpose, not suggested reading.
- **Checks:** exact commands, route, tested SHA, exit code and passed/failed/skipped
  counts; required unrun checks and reason; DB target/cleanup when applicable.
- **Handoff:** direct repository/PR links, remaining limits and review request.

READY means every required acceptance check passed. Docs/mechanical work records
applicable static checks and explains why application tests are not applicable.
A required test not run is not PASS; use BLOCKED only if necessary acceptance
cannot proceed, otherwise continue IN_PROGRESS. A waived check requires an explicit
owner waiver and remains labelled unrun. Remote PR body is the solution/evidence
entry point; the card links it instead of duplicating it. Preserve original
revision attribution; old greens never certify new code. Long failures belong
in `docs/audit/<id>.md`. `/tmp` and ignored `.agents/tmp/` are local scratch only:
never link them as evidence or a handoff. Copy the useful summary, tested SHA,
commands/results and resume commit into the task card or PR body, then delete
scratch files at closeout. A path on one machine is not visible to another
checkout. For local commands, name the tested SHA and branch/worktree; distinguish
migration/command success from application-test results. A clean `dev` status
does not mean tests passed.

## Acceptance

The auditor skill resolves a READY row → card → PR solution/diff and recorded
checks. Findings may require focused tests; auditors decide based on corrections
and risk. No routine retest for a docs-only review; full suites need owner request.
No executor self-acceptance. `audit <ID>` / skill selection is enough; `/audit`
works only if an interface explicitly maps it to the skill.

On success, in one registry update:

1. Write `docs/audit/<id>.md`: verdict, completion date, accepted source SHA,
   key corrections, affected files (including tests), checks/limitations and dialogue.
   Consolidate useful card evidence here; do not retain redundant active cards.
2. Replace the parent plan's task link with that audit link and brief DONE/date
   summary. Remove the ROADMAP row and task card; repair inbound active references.
   For an orphan, only the audit remains. Keep historical references as history.
3. When every phase item is accepted, mark PHASE DONE/date; replace detailed plan
   with a short outcome per task/audit link, history pointer, accepted SHA and its
   actual commit date. Do not invent missing historical metadata.
4. Synchronize the scoped registry update with local/origin dev as under Tasks.
   Record pending solution integration separately from audit acceptance.

Before integrating or deferring solution work, the executor reports the exact
branch/commit or PR and asks the owner for one choice: merge to dev; continue on
the current branch/PR; or defer and preserve that resume point. Do not merge,
push a solution, close the PR or discard the branch until the owner chooses; an
existing explicit instruction already covering that action counts. If the owner
has not answered, leave the solution intact and record the pending decision.
READY is executor evidence; successful audit is an acceptance decision; neither
authorizes integration by itself. After acceptance, remove the queue item and
archive it even when integration is still pending. For unresolved work, keep the
task IN_PROGRESS if work can continue; use BLOCKED only for a task/dependency
that prevents resolution. If the owner chooses to defer, record that choice and
resume branch/PR without calling it BLOCKED solely for being postponed.

If unresolved acceptance cannot proceed, change IN_PROGRESS/READY to BLOCKED;
record concrete reason, passed/failed/unrun checks and recommended fix in the
linked audit diagnostic. Correctable findings return to IN_PROGRESS when work
can continue. Cleanup/session failures never cause BLOCKED or reopen accepted work.
Data residue is an acceptance defect if test cleanup is required.

Report briefly: `✅ <ID>: <result>; removed from ROADMAP; archive: <link>;
checks: <actual results>`. State pending integration/cleanup separately.

## Delegation

Delegate bounded routine edits/inventories/test commands to a cheaper agent when
its narrow input saves more cost/context than setup and result review. Keep
judgment and final acceptance with the auditor; do not relay whole documentation
trees. Assign one outcome, exact HEAD, file ownership and checks; require a compact
verdict, file refs and counts. Only Codex calls third-party agents; if unavailable,
use native children under the same scope/guards. Other agents use native children.
Persist results, then stop/delete owned child sessions immediately. Never delete
foreign sessions or manipulate session storage; unavailable/already closed sessions
are housekeeping notes, not task failures. Keep a durable handoff if deletion
prevents resume. Do not repeatedly investigate missing sessions after owner closure.

Remote assignments include repository and PR URLs. PR body holds solution/source
and evidence; all instructions, feedback and questions go through PR comments.
One custodian applies those status comments to fresh ROADMAP/task cards; remote
branches do not publish competing registry copies.

For a task-ID assignment, the remote agent resolves its repository from the
checkout/card and creates or locates the PR. Ask the owner for a repository or PR
link only when project context cannot resolve it; never require the owner to find
the source SHA. Reviewer/executor records the resolved SHA as evidence.

## Tests

Read [test-runner](../.agents/skills/test-runner/SKILL.md) and
[tests/AGENTS](../tests/AGENTS.md) for test work only. Codex uses OpenCode first:
from the target checkout, `opencode run --agent build --file <task-file>` with
instructions to invoke configured `tester` via the V2 `subagent` tool. Its model
`river/deepseek-ai/DeepSeek-V4.1-Flash-1017` and subagent mode come from
`~/.config/opencode/opencode.jsonc`; do not override them or select tester as
parent. Tester only runs the supplied command and analyzes output; parent owns
preparation/cleanup. Explicit test authoring is a separate assignment.
If unavailable, use Kilo, then a native Codex child; permission/HEAD/ownership
failures are not bypass permissions. Other agents use native test children.
Collect affected paths after enum/import/layout changes. DB scopes use root
conftest; disable it only for proven no-DB isolation. No broad suite without
explicit owner request; auditor retest decisions follow Acceptance above.

## Database

Local `localhost:5432/social_manager/test_schema` is authorized for synthetic
records, setup/reset and reviewed Alembic migrations without repeated permission.
Resolve effective host/database/`TEST_POSTGRES_URL`/`DB_TEST_SCHEMA`/settings,
compatibility and exclusive writer ownership first. All worktrees share it:
tests, migrations, setup and cleanup are sequential. Use the existing claim/lock
and inspect active runners; no pytest alone does not exclude other writers.
Preserve unrelated schemas/reference data; back up retained evidence before
necessary destructive reset. Do not fake migration history or edit applied DDL.

After success/failure/interruption, stop surviving processes, remove all synthetic
records (including separately committed fixtures), and verify none remain.
Rollback alone is insufficient. Take a schema inventory (relations, types,
functions, triggers and `alembic_version`) before and after every run and diff
it; never accept a spot check such as "existing triggers still present" as
evidence of a clean run. A new relation, type, permission/`model_types` row or a
revision the target branch does not contain is a leftover, even when no test
failed. Diffing also proves a run left nothing behind in a schema it did not own. Never delete another active run's data; wait for
exclusive ownership. Report partial/interrupted runs honestly and resolve unknown
target/data state before retry. Preserve migration metadata/non-test reference rows.
Public migrations require full prior validation of the same revisions/behavior on
test_schema and separately authorized apply. Public test records require explicit
Superuser approval for that scope; a DB role/agent identity does not grant approval.
Use resolved schemas/Alembic context, qualified DDL and existing manager/session
conventions; no ad-hoc engines, runtime DDL or unsafe search-path assumptions.

## State transitions

A state-changing method is not safe for concurrent callers merely because it
exists and passes a single-threaded test. Read-then-write transitions need an
atomic compare-and-set or a row lock; a pre-check against unique columns does not
protect concurrent creators, only a database uniqueness constraint does. Approval
must be bound to actor and payload, not to a writable status field.

When a task delivers such a mechanism, its acceptance names the atomicity and
idempotency guarantee actually provided, and the audit records any remaining
race or duplicate window. Do not describe a preview, helper or unreachable
transition path as an execution mechanism. Loopholes found in a mechanism belong
in the owning task card and its acceptance criteria, not in shared rules.

## Git and cleanup

Review scoped diffs before local task commits. Preserve foreign staging/edits.
Integration/push/deployment requires applicable owner authorization. At closeout,
inventory `git worktree list`; for each candidate verify task/status/owner, HEAD,
dirty and untracked files, and whether its commits are integrated. Age or a repeated
HEAD alone does not prove a workspace is unused.

Remove an owned, clean worktree when its owner confirms no active work remains.
If its commits are unmerged, retain the branch and record branch/HEAD in the task
card as the resume point. After verified integration (ancestry, or reviewed
equivalent squash/rebase patch), remove the worktree and merged task branch.
If files are dirty/untracked, ownership or activity is unclear, or task work
remains active, preserve the workspace and report its path, task, branch/HEAD,
file state and next owner action. Never discard work or force-delete to hide loss.
Preserve foreign branches and explicit recovery backups. Record removed and
retained candidates in closeout; acceptance, integration and housekeeping remain
separate facts.
