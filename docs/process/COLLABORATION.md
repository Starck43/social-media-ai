# Development contract

Entry rules live in [AGENTS](../../AGENTS.md); this file owns detailed workflow,
DB and test procedures. Read only the section your skill names — this is a
reference, not a per-session read.

## Tasks

ROADMAP: one line per open item — ID/title linked to its card, status, owner,
priority/complexity, last status-changing dialogue or PR-comment link (`—` only
for unknown legacy links; a session ID/resume command is valid when no URL
exists). Anyone may add a TODO/question. Reserve the next unused phase/task or
question ID against ROADMAP and phase/audit history, in numeric sequence; never
recycle IDs. Display the queue in ROADMAP priority order, respecting predecessors
and parked deferrals; phase plans keep numeric order. Priority/complexity never
authorize execution or change status.

Before editing a solution: fetch `origin`, claim/create the row as IN_PROGRESS
with owner/dialogue and card/parent-plan link, and publish that claim as a small
registry-only commit based on current `origin/dev`. Synchronize local `dev`
without overwriting others' changes; if local dev is ahead, use a separate
registry worktree instead of pushing unrelated commits. On a non-fast-forward
push: fetch, reconcile, recheck the claim; never force-push. Claim publication
to `origin/dev` is authorized by this workflow; solution publication still
requires the owner's explicit choice, while merge follows
[Acceptance](#acceptance). An unpublishable claim stays local —
report the limitation, never pretend it is visible. Remote agents self-publish
their scoped claim commit under the same rules, always before any solution edit
and never with it; they never publish solutions, unrelated rows or competing
registry copies, and never idle waiting for a custodian or owner. A designated
custodian may instead apply PR-comment status requests, refreshing both
directions before each transition.

Create `type/<task-id>-<topic>` from the agreed revision. Independent writers use
exclusive paths and separate worktrees. Every task branch with active work gets its
own worktree keyed by the task ID (`/private/tmp/social-<task-id>[-<suffix>]`, e.g.
`...-audit` for a review); a bare branch ref with no active work needs no worktree.
Record the branch and worktree path/HEAD on the card; integration removes both,
unmerged work keeps a resume point under [Git and cleanup](#git-and-cleanup). Task
implementation stays out of `dev` (registry-only synchronization is the exception
above).

The primary checkout is the owner's interactive workspace: never switch, move or
commit branches in it, never leave it on an agent branch. If it sits on a branch
you need, open a worktree for it. Verify branch and dirty state before any
commit; commit only in your own task worktree.

`docs/tasks/<id>.md`: scope/acceptance, parent phase (or explicit orphan),
related links, owned paths/dependencies, branch/PR, documents read, checks;
links ROADMAP instead of copying status. Phases in `docs/phases/`: product-parent
backlink, goal, numbered plan, active-card links, accepted audit links. Plan
substantial work as a numbered phase; keep short standalone tasks as cards.

A card is working material for an **open** item. Once the row leaves ROADMAP,
move the card to `docs/archive/tasks/<id>.md` — never keep it in `docs/tasks/`,
never delete it while the history is useful. The phase line states the outcome
and links the audit; the audit owns the durable record. A phase or contract with
a living decision keeps that text itself. `scripts/check_registry.py` warns about
cards in `docs/tasks/` without an open ROADMAP row.

## State machine

Open statuses: `TODO`, `IN_PROGRESS`, `READY`, `BLOCKED`, `DEFERRED`; questions
use `TODO`/`OPEN`/`DEFERRED`. `DONE` is not a row status: acceptance removes the
row; the audit record plus phase line carry the DONE/date summary.

- `TODO → IN_PROGRESS`: published claim commit (see Tasks) before any solution
  edit; remote executors self-publish, never idle.
- `IN_PROGRESS → READY`: executor (remote: custodian via PR comment) publishes
  only when every required acceptance check passed, with the
  [Evidence](#evidence) record in the card or linked PR body. Actionable failure
  keeps IN_PROGRESS.
- `READY → DONE`: auditor applies [Acceptance](#acceptance) only after verified
  integration into fresh `origin/dev`; remove the row, archive the card, keep
  audit and phase line.
- Rejected, correctable → IN_PROGRESS with findings, required checks and a
  recommended fix; remote findings ride PR comments, never the executor-owned
  PR body.
- Rejected, cannot proceed → BLOCKED with reason, passed/failed/unrun checks and
  fix in the linked audit diagnostic.
- Owner defer → DEFERRED with choice, reason and resume branch/PR; never BLOCKED
  solely for being postponed.
- Owner descope without acceptance: owner's explicit decision only; record in
  the audit note or a Questions row, remove the row, keep the note.

## Evidence

Before READY, record in the card or linked PR body:

- **Solution:** behavior, affected files, source SHA, branch, dependencies.
- **Documentation read:** actual `path#section` and purpose.
- **Checks:** exact commands, route, tested SHA, exit code, passed/failed/skipped
  counts; required unrun checks with reasons; DB target/cleanup when applicable.
- **Handoff:** repository/PR links, remaining limits, review request.

READY means every required acceptance check passed. Docs/mechanical work records
applicable static checks and why application tests are N/A. A required test not
run is not PASS: stay IN_PROGRESS, or BLOCKED only if acceptance cannot proceed.
A waived check requires an explicit owner waiver and stays labelled unrun. The
remote PR body is the evidence entry point; the card links it. Preserve original
revision attribution — old greens never certify new code. Long failures go to
`docs/audit/<phase>/<id>.md`. `/tmp` and `.agents/tmp/` are local scratch only:
copy the useful summary (SHA, commands/results, resume commit) into the card or
PR body, then delete scratch at closeout; a path on one machine is invisible to
another checkout. For local commands name the tested SHA and branch/worktree;
distinguish migration/command success from application-test results — a clean
`dev` status does not mean tests passed. Required DB/integration scenarios are
acceptance checks, not a supplement to unit tests: record their exact commands,
scenario counts, tested SHA and DB target/cleanup separately; green unit tests
cannot replace a failing or unrun DB scenario. A remote handoff stays IN_PROGRESS
until the local runner's evidence is linked and all required checks pass (or the
owner waives a named check).

## Acceptance

The auditor resolves a READY row → card → PR diff and recorded checks. Acceptance
is criterion-by-criterion: for each acceptance item name the code path, the test
that exercises it and the observed evidence. A test that still passes with the
change reverted proves nothing; a mock-only or unit-only green never replaces a
required DB/integration scenario. A suite reshaped to the implementation (weakened
assertion, edited expectation, mock of the module under test) is a finding, not a
pass. Findings may require focused tests (auditor judgment on corrections and
risk); no routine retest for docs-only reviews; full suites need owner request.
No executor self-acceptance. Self-review is not acceptance: an orchestrator that
both implemented and reviews may run the mechanical pre-flight (diff, checks,
integration proof), but the verdict must come from the independent reviewer
route; without one the task stays READY, never DONE. `audit <ID>` selects the
skill; `/audit` only where an interface maps it.

On successful review, verify integration **before** publishing DONE or removing
any row/card:

1. Confirm the accepted SHA is still the PR head and the merge conditions below
   hold; perform the authorized merge. A rejected command, queued merge or
   closed PR is not integration; never close a PR instead of merging it.
2. `gh pr view <number> --json state,mergedAt,mergeCommit,headRefOid,baseRefName`:
   require `state=MERGED`, non-null `mergedAt` and merge commit, base `dev`,
   accepted head. Fetch `origin`; prove ancestry with
   `git merge-base --is-ancestor <merge-sha> origin/dev`. For each delivered path
   (including migrations) check `git ls-tree origin/dev -- <file>` and compare
   content with the accepted revision (deletions must be absent) — a listing
   proves presence, not content. Resolve discrepancies before closeout.
3. Then publish one scoped registry update: `docs/audit/<phase>/<id>.md` with
   verdict, date, accepted SHA, PR, verified merge commit, integration
   commands/results, corrections, affected files, checks/limitations, dialogue;
   consolidate useful card evidence; record branch/worktree disposition under
   [cleanup](#git-and-cleanup); delete only verified-integrated owned artifacts.
4. Replace the parent task link with the audit link and a brief DONE/date
   summary; remove the ROADMAP row/card and repair inbound active references;
   an orphan leaves only its audit. When every phase item is accepted, compact
   its plan to outcomes/audit links, history pointer, accepted SHA and commit date.
5. Synchronize the registry update as under [Tasks](#tasks).

If merge or verification fails: retain row/card and resume branch, record review
success separately from missing integration, report the actual failure. Keep
READY only while its checks remain valid; actionable corrections return to
IN_PROGRESS; inability to proceed follows the BLOCKED rules. Never publish DONE
or delete recovery artifacts. If a premature acceptance was published, correct
the registry to the actual integration state; a closed PR never supplies missing
merge evidence.

An explicitly authorized direct integration has no PR `mergedAt`: record the
exception and authorization, prove accepted ancestry (or reviewed squash/rebase
equivalence) and verify the fresh dev tree as above. A failed PR merge does not
by itself authorize this alternative.

Before integrating or deferring solution work, the executor reports the exact
branch/commit or PR and asks the owner to choose: merge to dev; continue on the
current branch/PR; or defer and preserve the resume point. Do not merge, push a
solution, close the PR or discard the branch until the owner chooses (an existing
explicit instruction covering the action counts); unanswered — leave the solution
intact and record the pending decision. READY grants nothing. A successful audit
acceptance **is** merge authorization when all of: accepted SHA is the PR head;
the audit ran on that exact revision with required checks recorded; the PR
targets `dev` with no conflicts or outstanding change requests. Then the auditor
merges (normal strategy) in the same closeout, verifies integration, deletes the
merged branch and the task's owned worktrees, and records the merge evidence in
`docs/audit/<phase>/<id>.md`. Acceptance never authorizes force-push, production
or schema mutation, or publishing unrelated commits.

When any condition fails — dirty/unmerged work, unresolved finding, non-`dev`
target, or owner hold — the auditor does not merge: it records the failing
condition in the audit note and asks the owner. Integration and acceptance stay
separately recorded, but a plain accepted task needs no second decision. Keep
IN_PROGRESS while work can continue; BLOCKED only for a blocking task/dependency;
an owner deferral records the choice and resume point, not BLOCKED. Record
BLOCKED with concrete reason, passed/failed/unrun checks and fix in the linked
audit diagnostic. Cleanup/session failures never cause BLOCKED or reopen accepted
work. Required-test data residue is an acceptance defect.

Report briefly: `✅ <ID>: <result>; removed from ROADMAP; archive: <link>;
checks: <actual results>`. State pending integration/cleanup separately.

## Owner questions

The owner is a decision source, not a routine reviewer. A task waiting on the
owner is not moving — a silent stall is a defect.

### When to ask (four triggers)

Ask **before** implementing when the choice:

1. **Changes money, certificates or infrastructure** — paid services,
   code-signing/notarization, new cloud components, recurring bills, support
   burden. State the annual cost in the question.
2. **Alters an accepted contract** — a `docs/audit/` document, an API shape in
   use, a test encoding an accepted decision, a closed owner decision.
3. **Is irreversible once merged** — data-dropping/rewriting migrations,
   deletions, a public API others depend on, anything a follow-up commit cannot
   roll back.
4. **Changes what a subscriber sees** — user-visible behaviour, install/onboarding
   friction, customer-facing warnings, the product's promise.

Everything else belongs to the executor: naming, internal structure, library
choice within a module, test layout, wording. "It is a judgment call" is not a
reason to ask — if none of the four applies, decide.

### How to ask

- **Short:** 2–3 options plus a recommendation; each option one sentence with
  the consequence behind your recommendation.
- **Batch related questions** in one message so the owner answers once.
- **Read the register first.** An answered decision is an authorization: check
  [Q-18](../audit/q/q-18-owner-decisions.md) and the contracts in
  [BUSINESS_MODULES](../product/BUSINESS_MODULES.md). Re-asking granted decisions
  costs a round trip and reads as not having done the task.
- **Recommend one**, marked as such; "you decide" is not actionable.
- Ask through the interface the owner reads this session (chat question tool,
  PR or issue comment), in Russian when the owner writes Russian.

### When the owner is unavailable

Do not stall. For a doubt hitting a trigger that must be resolved to continue:

1. **Record it in the task card** under `## Open doubts`: doubt, options, chosen
   default, reason, **how to reverse it**. Add the section at the first doubt.
2. **Proceed on the smallest reversible step** — prefer adding an option over
   replacing an accepted one.
3. **Mark the task as needing owner confirmation** in the PR body. It may reach
   READY when checks pass, but the auditor names the open doubt in the acceptance
   note; an unanswered question is never recorded as approved.
4. **Surface it in the next status report** so the owner closes it in one pass.

An answer given anywhere (chat, PR/issue comment) is written back into the
registry the same session — ROADMAP question row or card — with decision ID,
choice, date and link. A decision living only in chat is not a decision.

### Owner decisions in a register

When several open questions block as a group, keep them in one register card
(Q-18 was one, now closed and kept as [history](../audit/q/q-18-owner-decisions.md)):
one row per decision — ID, question, recommended default, needing task. Each
card names its decision by ID. When the last row is answered, move the register
to `docs/audit/` and remove its ROADMAP row.

## Delegation

Delegate bounded routine edits/inventories/test commands to a cheaper agent when
narrow input saves more cost/context than setup and review. Keep judgment and
final acceptance with the auditor; don't relay whole documentation trees. Assign
one outcome, exact HEAD, file ownership and checks; require a compact verdict
with file refs and counts.

| Delegate to a cheap child | Keep with the executor/auditor |
| --- | --- |
| Repo/call-site exploration; one subsystem question | Acceptance judgment and the verdict |
| Running one exact test command (`tester`) | DB migration/cycle decisions and schema ownership |
| Parsing a long log, traceback or test report | Merge, force-push, deployment, destructive cleanup |
| Link, registry, branch/worktree, schema inventories (scripts) | Owner questions and irreversible choices |
| Drafting a doc/PR section from a fixed outline | Security review and final handoff wording |
| Repeating a deterministic check on a named SHA | Anything whose failure you cannot verify yourself |

A child gets the path, the exact section, the command and the expected output —
never the parent transcript. If the parent cannot check the child's result in one
read, the delegation was too broad. Any configured lane may dispatch third-party
routes (`perch`, `free-pi`); each role skill defines the order, and an unavailable
route falls to the next one under the same scope/guards. Persist results, then
stop/delete owned child sessions immediately; never touch foreign sessions or
session storage. Unavailable/closed sessions are housekeeping notes, not task
failures; keep a durable handoff if deletion prevents resume; don't repeatedly
investigate missing sessions after owner closure.

Budget each lane: at most 1–2 children per task, each capped at ~1M input tokens
or ~20 turns, and a compact handoff instead of a forked session. A forked child
re-sends its inherited 50–100k context every turn, so five children can outspend
the whole task. Preflight a third-party route once (`perch status`, `free-pi`
availability) before spawning it; a child must not spend a turn discovering the
route is down. Use one blocking call with a timeout, never a polling loop. An
audit is verdict-only: UI/style/code work added "along the way" is a separate
`task` lane, never implementation inside the audit. On 2026-11-26 an audit of
P22-T08 spent 16.4M input tokens in ~15 minutes (2% on the verdict, 35% on
premature UI edits, 25% on review, 23% on DB research); see
[PROMPT_EXAMPLES](PROMPT_EXAMPLES.md#anti-pattern-audit-plus-redesign) for the
corrected prompt shape.

Remote assignments include repository and PR URLs. The PR body holds
solution/evidence; instructions, feedback and questions go through PR comments.
One custodian applies status comments to fresh ROADMAP/cards; remote branches
never publish competing registry copies.

The assignment issue is the lane's channel, not its state: status, owner and
evidence live in ROADMAP and the card. The accepting auditor closes the issue in
the same closeout citing merge commit and audit link; an executor never closes
it. Paused/deferred tasks keep the issue open with the decision and resume point.
Closing without an accepted result, or leaving an accepted one open, is a
registry defect the next auditor fixes.

For a task-ID assignment, the remote agent resolves the repository from the
checkout/card and creates or locates the PR; ask the owner for a link only when
project context cannot resolve it, never make the owner find the source SHA.
Record the resolved SHA as evidence.

## Tests

Read [test-runner](../../.agents/skills/test-runner/SKILL.md) and
[tests/AGENTS](../../tests/AGENTS.md) for test work. Route runs in delegation
order: the `tester` child (`cent.ai/hy4-flash:free`, run-only), then the `free-pi`
subagent (`free-pi/deepseek-v4.1-flash#medium`), then the executor's own model;
fall to the next route only when the previous is unavailable or fails, and report
which route ran — permission/HEAD/ownership failures are not bypass permissions.
Perch is an external executor for a self-contained job, not a test runner.
Collect affected paths after enum/import/layout changes. DB scopes use root
conftest; disable it only for proven no-DB isolation. No broad suite without
explicit owner request; auditor retests follow Acceptance above.

## Database

Local `localhost:5432/social_manager/test_schema` is authorized for synthetic
records, setup/reset and reviewed Alembic migrations without repeated permission.
First resolve effective host/database/`TEST_POSTGRES_URL`/`DB_TEST_SCHEMA`/
settings, compatibility and exclusive writer ownership. All worktrees share it:
tests, migrations, setup and cleanup are sequential — use the existing claim/lock
and inspect active runners ("no pytest alone" does not exclude other writers).
Preserve unrelated schemas/reference data; back up retained evidence before a
necessary destructive reset. Never fake migration history or edit applied DDL.

Before branch DB checks, fetch origin and compare the Alembic head derived from
fresh `origin/dev` (`ScriptDirectory.from_config(cfg).get_heads()` in a
dev-based checkout) with the shared `test_schema.alembic_version` and the
candidate's graph; record SHAs and observed revisions. An unexplained shared
schema/dev mismatch is a baseline/integration failure, not a branch defect: stop
bootstrap, find the missing integration or leftover migration, reconcile under
exclusive ownership. Never stamp past a mismatch, copy unmerged migrations into
dev, or reset another run's schema to make a branch pass. An assigned
task-isolated schema may run the candidate cycle; record its dev baseline and
expected task head separately. Before releasing a shared target, re-verify the
agreed dev baseline or record the ownership handoff for a still-active task head.

After any run (success/failure/interruption): stop surviving processes, remove
all synthetic records (including separately committed fixtures) and verify none
remain — rollback alone is insufficient. Take a schema inventory (relations,
types, functions, triggers, `alembic_version`) before and after every run and
diff it; a spot check is not evidence. A new relation, type, permission or
`model_types` row, or a revision the target branch lacks, is a leftover even
when no test failed; diffing also proves nothing was left in a schema the run did
not own. Never delete another active run's data — wait for exclusive ownership.
Report partial/interrupted runs honestly and resolve unknown state before retry.
Preserve migration metadata and non-test reference rows. Public migrations
require full prior validation of the same revisions/behavior on test_schema and
separately authorized apply. Public test records require explicit Superuser
approval for that scope; a DB role does not grant approval. Use resolved
schemas/Alembic context, qualified DDL and existing manager/session conventions;
no ad-hoc engines, runtime DDL or unsafe search-path assumptions.

## Migration numbering and parallel tasks

Task cards describe the needed migration but hold no number in advance — the
number is integration-order metadata, resolved once at merge, not negotiated.

1. **No pre-reservation.** Write the migration with a stable placeholder
   revision (e.g. `p19t04_document_approval`) and `down_revision` set to the
   **live** head: run `ScriptDirectory.from_config(cfg).get_heads()` on current
   `dev`, never a remembered number or earlier file listing (R-20 in
   [BUSINESS_MODULES](../product/BUSINESS_MODULES.md)).
2. **Numbering happens at merge.** Whoever integrates assigns the next free
   numeric revision and rewrites `down_revision` in the same commit; the body
   does not change. The audit note records the landed number.
3. **Parallel tasks chain.** First integrated takes the next number after the
   head; the second chains after the first. Chain order is integration order,
   not claim order.
4. **A dependent task does not wait for a number.** Task B records the
   dependency in its card, builds against A's placeholder locally, integrates
   after A; it never polls for a number, and no agent allocates one it cannot
   see in `dev`.
5. **Cycle evidence is earned once, at the landing graph.** Run upgrade, empty
   downgrade, refused downgrade with data and single-head check on the task's
   isolated schema against the chain that will merge. A renumber touching only
   `revision`/`down_revision` needs only a fresh head check.
6. **Public is not a second rehearsal.** [Database](#database) already requires
   full validation on an isolated schema before any public apply; a green
   isolated cycle is that validation — `public` is a separate authorized
   deployment action.

An allocation note under `docs/tasks/` matters only while the task is open; it
records the placeholder and dependency, not a promised number. The
[reservation register](../archive/tasks/p19-t04-migration-allocation.md) is
history, not a lock file.

## State transitions

A state-changing method is not concurrency-safe just because it exists and
passes a single-threaded test. Read-then-write transitions need an atomic
compare-and-set or row lock; a pre-check against unique columns protects nothing
— only a database uniqueness constraint does. Approval must bind to actor and
payload, not to a writable status field.

When a task delivers such a mechanism, its acceptance names the atomicity and
idempotency guarantee actually provided, and the audit records any remaining
race/duplicate window. Never describe a preview, helper or unreachable path as
an execution mechanism. Loopholes belong in the owning task card and its
acceptance criteria, not in shared rules.

## Git and cleanup

### Who creates and who removes a branch

Executor branches are task-scoped and short-lived. The creator owns removal; a
branch without an owner is a defect.

| Artifact | Created by | Removed by |
| --- | --- | --- |
| Task branch / worktree | the task's executor, named on the card | the auditor at acceptance (= merge authorization); the executor if it closes the task itself |
| Merged task branch | as above | whoever integrates, immediately after the merge |
| Audit/review branch | the auditor | the auditor, once the audit commit is on `dev` |
| Recovery backup (`backup/*`) | the agent naming it | owner decision only, recorded on the card |

Acceptance, integration and housekeeping are recorded as separate facts, but a
plain accepted task is not parked for a second decision: the audit verdict
authorizes the merge, and the merging auditor deletes the branch in the same
closeout. Deleting a branch whose commits are in `dev` drops no work — it removes
a second name for the same commit.

### Closeout and acceptance duties

Review scoped diffs before local task commits; preserve foreign staging/edits.
Integration/push/deployment requires applicable owner authorization. At closeout
inventory `git branch --no-merged origin/dev`, `git worktree list`, `git status`;
per candidate verify task/status/owner, HEAD, dirty/untracked files and whether
commits are integrated — age or a repeated HEAD alone proves nothing.

Remove an owned, clean worktree when its owner confirms no active work remains.
Unmerged commits: retain the branch, record branch/HEAD on the card as resume
point. After verified integration (ancestry or reviewed equivalent squash/rebase):
remove worktree and merged branch. Dirty/untracked, unclear ownership/activity,
or active work: preserve the workspace and report path, task, branch/HEAD, state
and next owner action. Never discard work or force-delete to hide loss. Preserve
foreign branches and explicit backups. Record removed and retained candidates.

Acceptance adds one obligation: before removing the ROADMAP row, the auditor
checks the task's branches/worktrees, deletes those already integrated, and
records in `docs/audit/<phase>/<id>.md` either the completed removal or the
retained artifacts and their owner. A closed task with a live integrated branch
is an acceptance follow-up, not a new task.

### Periodic sweep

Branches accrue only when the executor skipped cleanup and no auditor noticed.
`git branch --no-merged origin/dev` and `git worktree list` are the authoritative
inventory — never a hand-kept list. Any agent may sweep before claiming a new
task; the owner may request one anytime. A sweep is read-only until, per branch,
it proves the commits are ancestors of `origin/dev` (or reviewed equivalent) and
the worktree is clean; failing branches are reported with task ID and next owner
action, never deleted. A sweep is housekeeping: it cannot reopen or block a task,
and deleting an integrated branch is not a statement about acceptance. Branches
retained for BLOCKED/deferred tasks are expected, not sweep failures — the card
names them as the resume point.
