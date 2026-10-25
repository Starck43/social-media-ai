# Development contract

Read only the section needed by the selected skill. Mandatory entry rules live
in [AGENTS](../AGENTS.md); this file owns detailed workflow and DB/test procedures.

## Tasks

- ROADMAP is a short open-task index: ID/title, status, owner, card and last
  status-changing dialogue/PR-comment link. Use `—` for unknown legacy dialogue.
- `docs/tasks/<id>.md` owns scope, acceptance, dependencies, owned paths, branch,
  source revision, documents read, checks and outcome. Link its parent phase.
  Do not repeat current status in the card; link back to the queue.
- Reserve unique `P<n>-T<nn>` / `Q-<n>` IDs against queue and parent history.
  Read status/HEAD/worktrees/fresh base refs, then claim one unassigned ready
  item on `type/<task-id>-<topic>`. Independent writers need exclusive files
  and separate worktrees. Never overwrite foreign work or stale registry copies.
- On a status change, update only that row and the card's actual evidence;
  record actor, timestamp and real dialogue/comment link. Local session IDs and
  supported resume commands are valid; never invent a session URL.
- Parent phase files in `docs/tasks/` hold direction/gate links and closed task
  history; active tasks are linked via ROADMAP, not copied into phase files.
  Whole closed phases alone move to `docs/phases/` after auditor approval.

## Evidence

Before READY, the task card or linked PR records:

- Source SHA and owned paths; branch/worktree and dependencies.
- **Documentation read:** actual `path#section` references and why each mattered.
  Context suggestions are not proof that a document was read.
- **Checks:** exact command/scope, execution route, tested SHA, exit code,
  passed/failed/skipped counts, unrun checks and their reasons; DB cleanup result.
- **Result:** behavior, limitations and handoff. Remote PR body carries the same
  compact evidence; the card links it instead of duplicating transcripts.

No tests for mechanical/docs-only edits. Preserve historical evidence at its
original revision; unknown counts are unknown. Put long failure diagnostics in
`docs/audit/` only when needed, linked from the card. Temporary logs/prompts live
in ignored `.agents/tmp/`; retain useful evidence before removing them.

## Acceptance

Executor reports READY; auditor reviews the recorded diff for correctness,
edge cases, security and acceptance, using the card's documentation/check links.
On success, mark DONE in the parent phase archive and remove the ROADMAP row
in the same scoped update. Record accepted SHA, checks and dialogue; integration
SHA/state is separate and must not be invented. No executor self-acceptance.

BLOCKED is only for an unresolved solution, missing required acceptance evidence
or a dependency preventing this task. Record the concrete reason, passed/failed/
unrun checks and recommended next action in the card, with optional audit detail.
Tool unavailability blocks only if it prevents necessary task work. An already
completed task is not BLOCKED for session deletion, cleanup reporting, commit
administration or pending merge; report these separately. Data residue is a
validation defect when clean test data is a required acceptance condition.

## Delegation

Assign one outcome, exact HEAD, file ownership and checks; require a compact
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
explicit owner request; auditor retest condition is defined once in AGENTS.

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
Rollback alone is insufficient. Never delete another active run's data; wait for
exclusive ownership. Report partial/interrupted runs honestly and resolve unknown
target/data state before retry. Preserve migration metadata/non-test reference rows.
Public migrations require full prior validation of the same revisions/behavior on
test_schema and separately authorized apply. Public test records require explicit
Superuser approval for that scope; a DB role/agent identity does not grant approval.
Use resolved schemas/Alembic context, qualified DDL and existing manager/session
conventions; no ad-hoc engines, runtime DDL or unsafe search-path assumptions.

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
