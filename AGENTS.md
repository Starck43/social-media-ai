# Shared agent rules

These rules apply to every local/remote agent, including chat assignments without
a skill. Owner instructions take precedence. A skill selects a role; without one,
use general execution. Anyone may add a question or TODO to [ROADMAP](ROADMAP.md).

## Read once, then lazily

Read [README](README.md), [DESIGN](docs/DESIGN.md) and this file once per session.
For work, open one ROADMAP row and its task card; load only relevant linked
sections. Record documents actually read and checks in the card/PR. Shared
[skills and prompt examples](docs/PROMPT_EXAMPLES.md) select execution/audit roles.

## Mandatory boundaries

- Before task edits, claim/create its unique ID, status, owner, dialogue and card
  link in ROADMAP. Publish scoped registry changes to `origin/dev` and reconcile
  local `dev`; follow [claim synchronization](docs/COLLABORATION.md#tasks).
  Work on a dedicated task-ID branch/worktree; preserve other agents' work.
- ROADMAP owns open status; `docs/tasks` owns active detail; `docs/phases` owns
  stage plans; `docs/audit` owns accepted results and detailed failures. Use
  [lifecycle and evidence rules](docs/COLLABORATION.md#acceptance). Never invent
  checks, approvals, dialogue links or completion. READY requires required checks
  PASS. Before solution merge or deferral ask the owner to choose; preserve the
  branch/PR until then. Acceptance and integration are separate. BLOCKED concerns
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
  never portable evidence. Ask owner before merge or deferral; preserve resume
  branch/PR pending the choice. Then stop/delete owned child sessions. Unsupported
  cleanup is reported separately. Remote coordination uses PR comments; PR body
  carries solution/evidence and direct repository/task links.
- Read [tests/AGENTS](tests/AGENTS.md) before test work. Run focused required checks;
  full suites require explicit owner request. Routine docs review needs no pytest.
- At closeout remove own temporary files; inventory worktrees and apply
  [safe cleanup](docs/COLLABORATION.md#git-and-cleanup). Preserve dirty/active/
  foreign/unclear work and unmerged commits; delete branches only after verified
  integration. Report retained paths and next action. Cleanup failure cannot
  reopen an accepted task.
- English code/docs; brief Russian reports; Black/isort line 120. Never commit
  secrets, sessions, generated data or `.agents/tmp/`. Provider adapters in
  `.codex/`, `.opencode/`, `.kilo/` are local ignored configuration.
