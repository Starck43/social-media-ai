# Shared agent rules

These rules apply to every local/remote agent, including chat assignments without
a skill. Owner instructions take precedence. A skill selects a role; without one,
use general execution. Anyone may add a question or TODO to [ROADMAP](ROADMAP.md).

## Read once, then lazily

Read [README](README.md), [DESIGN](docs/DESIGN.md) and this file once per session.
For work, open one ROADMAP row and its task card; load only relevant linked
sections. A link is navigation, not an instruction to recursively load parent
phases, PRODUCT_PLAN, related cards or audit history. If assigned an ID, read only
its queue row; scan the queue only to choose work or coordinate ownership. For
predecessors inspect their current row and just the specific delivered contract
needed. Reuse already-loaded unchanged rules within a session; refresh source
and changed sections after a revision change. Record documents actually read and
checks in the card/PR. Shared
[skills and prompt examples](docs/PROMPT_EXAMPLES.md) select execution/audit roles.

For structural changes, keep move-only and behavior edits separate, preserve
compatibility until consumers migrate, and define a safe rollback. Task cards
carry only task-specific additions, not copies of these common rules.

## Working principles

- Simplicity over cleverness; concise reporting without restating what the commit,
  card or PR already carries. Ask only when ambiguity materially changes the
  outcome; otherwise choose a reasonable default and proceed.
- Derive, don't store: a stale fact is worse than an absent one. Keep conventions,
  workflow order, gotchas and security constraints; never record values one
  command reveals (versions, listings, counts, current metrics) — store the pointer
  to their source instead.
- Prefer scripts over instructions for deterministic checks; only their output
  enters context. Hand long implementations to a fresh session with plan,
  decisions and next steps. Record actual documents read, never suggested reading.
- All projects follow [Semantic Versioning](https://semver.org/); a release updates
  the package version and records its evidence in the task card or audit.

## Mandatory boundaries

- Before task edits, claim/create its unique ID, status, owner, dialogue and card
  link in ROADMAP. Publish scoped registry changes to `origin/dev` and reconcile
  local `dev`; follow [claim synchronization](docs/COLLABORATION.md#tasks).
  Work on a dedicated task-ID branch/worktree; preserve other agents' work.
- ROADMAP owns open status; `docs/tasks` owns active detail; `docs/phases` owns
  stage plans; `docs/audit` owns accepted results and detailed failures. Use
  [lifecycle and evidence rules](docs/COLLABORATION.md#acceptance). Never invent
  checks, approvals, dialogue links or completion. READY requires required checks
  PASS. A successful audit acceptance also authorizes merging that exact accepted
  revision into `dev` and deleting its branch, unless the target or a finding
  fails ([acceptance rules](docs/COLLABORATION.md#acceptance)). Any other merge or
  deferral still needs the owner's choice; preserve the branch/PR until then.
  BLOCKED concerns
  the solution/acceptance, never a decision wait or housekeeping. Open row
  statuses: TODO, IN_PROGRESS, READY, BLOCKED, DEFERRED — DONE is not a row
  status; acceptance removes the row. DEFERRED is only an explicit owner choice,
  recorded with reason and resume branch/PR; postponement is never BLOCKED.
  Transition rules: [state machine](docs/COLLABORATION.md#state-machine).
- Resolve DB target/schema before access. Test-schema setup/migrations are allowed;
  serialize shared DB work and remove/verify all synthetic records afterward.
  Public test records require explicit Superuser approval. Public migrations need
  full test-schema validation and separate apply authorization. Alembic only;
  [database safeguards](docs/COLLABORATION.md#database) apply after failed runs too.
- Reuse ORM managers/services and transactions. Fail closed; recheck tenant/actor
  rights at execution. Preserve confirmation/dry-run, payer/usage evidence and
  secrets. Mock paid providers and external writes during tests.
- One `python -m app.runtime` owner; Celery/Redis and Streamlit stay frozen.
  Registry-only publication above does not authorize code pushes, force-push,
  deployment, production mutation or publication of unrelated local commits.
- Only Codex may invoke third-party agents; others use native children. Preserve
  handoff evidence in the task card/PR; `.agents/tmp` and `/tmp` are scratch,
  never portable evidence. Acceptance authorizes the merge it covers; any other
  merge or deferral asks the owner first and preserves the resume branch/PR.
  Then stop/delete owned child sessions. Unsupported
  cleanup is reported separately. Remote coordination uses PR comments; PR body
  carries solution/evidence and direct repository/task links.
- Read [tests/AGENTS](tests/AGENTS.md) before test work. Run focused required checks;
  full suites require explicit owner request. Routine docs review needs no pytest.
- At closeout remove own temporary files; inventory worktrees and branches
  (`git branch --no-merged origin/dev`) and apply
  [safe cleanup](docs/COLLABORATION.md#git-and-cleanup). The executor removes the
  branch it created once that branch's commits are in `dev`; the auditor verifies
  it at acceptance. Preserve dirty/active/foreign/unclear work and unmerged
  commits; delete branches only after verified integration. Report retained paths
  and next action. Cleanup failure cannot reopen an accepted task.
- English code/docs; brief Russian reports; Black/isort line 120. Never commit
  secrets, sessions, generated data or `.agents/tmp/`. Provider adapters in
  `.codex/`, `.opencode/`, `.kilo/` are local ignored configuration.
