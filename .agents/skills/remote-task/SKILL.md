---
name: remote-task
description: Execute one bounded task from ROADMAP.md remotely via a GitHub PR lane. Load before claiming, implementing or handing off any single open task from the registry.
---

# Remote task execution

Read this skill before claiming or starting any single task from
[ROADMAP.md](../../../ROADMAP.md) remotely. Governing rules:
[AGENTS.md](../../../AGENTS.md) and [docs/COLLABORATION.md](../../../docs/COLLABORATION.md).

**The invocation is one line: «take task `<Pxx-Tyy>` and apply
`.agents/skills/remote-task/SKILL.md`».** That line contains everything needed:
the task ID resolves the repository, the row, the card and the branch. Never ask
the dispatcher for a summary of the task, the rules or the checks — read them
from the files named below. Ask only when a fact is genuinely missing from every
listed source (for example, a decision the owner has not made yet); then ask the
single narrowest question and continue on everything that does not depend on it.

---

## 0. Repository and base

The remote lane works **inside the project repository**, never against a copy or
an older git fetch. The dispatcher supplies the repository path alongside the
task ID; if it does not, resolve it yourself before anything else:

```bash
git rev-parse --show-toplevel            # repository root — verify it is the project, not a scratch clone
git remote -v                            # must contain the project's origin
git fetch --prune origin                 # ALWAYS fetch before reading anything
git rev-parse origin/dev                 # the base everything is compared against
git log --oneline -n 20 origin/dev
git status --short                       # your working tree must start clean
```

Rules that follow from this:

- **`origin/dev` is the truth**, not a local `dev`, not `HEAD`, not a previously
  fetched state. A stale base is the most frequent cause of a wrongly claimed
  task, a phantom conflict or a wrong dependency version. Re-run `git fetch`
  immediately before claiming, and again immediately before publishing the PR.
- **Refresh is cheap and repeatable**: an empty `git log origin/dev..HEAD` is a
  valid answer ("nothing new"), not an error.
- Read every document below **from the freshly fetched `origin/dev`**, not from
  your working copy, when you have not yet checked out your task branch:
  `git show origin/dev:ROADMAP.md`, `git show origin/dev:docs/tasks/<pxx-tyy>.md`.
  After creating your branch, your working copy and `origin/dev` must differ only
  by your own commits.
- **Never** push to `dev`, force-push, merge, or rewrite history on `origin`.
  The only ref you write is your own task branch `<agent>/<Pxx-Tyy>-<slug>`.
- Never switch, move or commit branches in the owner's primary checkout; create a
  worktree for your branch instead.
- Report the exact base SHA you synchronized to in the first commit message and
  in the PR body. If `origin/dev` moved after you branched, rebase onto it before
  marking READY and re-state the SHA.

## 1. Read in this order, once

Read fresh from `origin/dev` unless a step says otherwise. Read each item once;
do not recurse into links those documents mention unless a step below says so.

| # | Read | Why | Notes |
|---|---|---|---|
| 1 | `ROADMAP.md` row for `<Pxx-Tyy>` | status, owner, dialogue, complexity, dependencies | If the row is missing, stop and report — do not recreate it from memory. |
| 2 | `docs/tasks/<pxx-tyy>.md` — the whole card | goal, scope, owned paths, acceptance, required checks, owner decisions, baseline | This is the specification. Nothing outside it is in scope. |
| 3 | `docs/DOCS_INDEX.md` — only the row(s) matching your subsystem | finds the one canonical subsystem doc | Do not read the whole index tree or every linked doc. |
| 4 | The subsystem doc from step 3, plus the call sites you will touch | conventions you must follow | If the card or index names a contract from an accepted predecessor (for example `docs/audit/<task>.md`), read only that contract's section you need. |
| 5 | `docs/tasks/` cards named in the card's **Depends on** | confirms the dependency is accepted | Read the row in `ROADMAP.md`, not the whole predecessor card, when the dependency is already accepted and linked as an audit. |
| 6 | `tests/AGENTS.md` | test layout, fixtures, DB scopes | Required before any test work. |
| 7 | `.agents/skills/test-runner/SKILL.md` | how execution is delegated locally | Read only the execution/route part. |
| 8 | `git log --oneline -n 20 origin/dev`, `gh pr list --state open` | base SHA, open PRs touching your owned paths | Detect overlaps before claiming. |

Everything in the table is read as of `git fetch --prune origin` done in this
session — see §0. If any step contradicts `origin/dev`, `origin/dev` wins.

Do **not** preload: `docs/phases/*` beyond the target phase's row for this task,
`docs/PRODUCT_PLAN.md`, other task cards, audit history, or `docs/BUSINESS_MODULES.md`
as a whole (only the section its card links).

Record every document actually read (path + purpose) in the card or PR body.
Recording what was read is evidence; recording what you were told to read is not.

## 2. Claim

- Set the `ROADMAP.md` row to `IN_PROGRESS` with owner and dialogue; create/update
  the linked task card with scope before solution edits. Preserve other rows.
- Keep current status in `ROADMAP.md`; parent phases carry active-card links and
  accepted summaries, not duplicate status rows.
- State the synchronized `dev` base SHA in the first commit message.

## 3. Implementation rules

- Create one thematic branch from fresh `dev`; open a Draft PR to `dev`.
- Follow current architecture, ORM/service conventions and
  `pyproject.toml` style (Black + isort, line 120).
- Reuse existing managers/services/ORM models; no ad-hoc DB sessions.
- No Alembic-outside migrations; no unrelated refactors; no frozen
  paths (Celery/Redis/Streamlit).
- Preserve all existing approval gates, dry-run defaults and fail-closed
  authorization checks.
- Work only inside the card's owned paths. Any edit outside them is a separate,
  clearly labelled commit, and must be checked against open PRs for the same files.

## 4. Testing

The remote lane has no database. Apply the **remote no-database exception**:

- Run only genuinely DB-exempt tests in an isolated sandbox venv.
- Explicit test paths, no real secrets or external sends:
  `--noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header`.
- Replace DB/LLM/provider boundaries with explicit mocks.
- Hand all shared-DB and integration validation to the local OpenCode
  test-runner with exact branch, SHA, commands and acceptance criteria; list
  them under **Deferred scenarios** in the PR body so the local runner knows
  exactly what to execute.
- Pin tested SHA; record exact scopes, counts and known limitations in
  `.agents/tmp/test-report.md` (transient; not committed).

Never report a deferred scenario as passed, and never present a mocked result as
if it exercised the real component — say "mocked" in the evidence.

## 5. PR and handoff

- Keep PR **Draft** until all required evidence is recorded.
- Before marking READY the PR description must carry:
  tested SHA, exact test paths/scopes, passed/failed/skipped counts,
  execution route, deferred scenarios.
- Update the task card with evidence and set the `ROADMAP.md` row to `READY` only
  after every required check passes. Keep the parent phase's active-card link; do
  not duplicate status there. Never self-accept as `DONE` or delete the row — that
  is the auditor's job after review.
- Synchronize `ROADMAP.md` and owned `docs/` edits with fresh `dev`
  immediately before publish; state the synchronized SHA.

## 6. Closeout checklist

- [ ] `git fetch --prune origin` run in this session; base SHA is `origin/dev`, stated in the first commit message
- [ ] Branch created from fresh `origin/dev` in own worktree, open Draft PR
- [ ] Task claimed in ROADMAP.md and linked task card
- [ ] Implementation follows architecture/conventions and owned paths
- [ ] Isolated remote tests pass with explicit mocks
- [ ] Local DB/integration validation handed off with exact commands and scenarios
- [ ] PR description carries durable test fingerprint
- [ ] Task card and ROADMAP row marked READY with evidence
- [ ] ROADMAP.md synchronized to fresh `origin/dev` immediately before publish, fetched again at that moment
- [ ] No secrets/.env/migrations outside Alembic committed
