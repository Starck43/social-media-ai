---
name: remote-task
description: Execute one bounded task from ROADMAP.md remotely via a GitHub PR lane. Load before claiming, implementing or handing off any single open task from the registry.
---

# Remote task execution

Governing rules: [AGENTS.md](../../../AGENTS.md), [docs/COLLABORATION.md](../../../docs/COLLABORATION.md).

Invocation is one line: «take task `<Pxx-Tyy>` and apply this skill». The ID
resolves everything; never ask the dispatcher for a summary, the rules or the
checks — read them from the files below. Ask only when a fact is absent from
every source; one narrow question, then continue on what does not depend on it.

## Base

`git fetch --prune origin` before reading and again before publishing.
`origin/dev` is the base and the truth — not local `dev`, not `HEAD`, not an
earlier fetch. Read as `git show origin/dev:<path>` until your branch exists.
Two `origin` writes are yours: the claim commit (registry paths only —
`ROADMAP.md`, `docs/tasks/<id>.md`, phase link — on current `origin/dev`, before
any solution edit, fast-forward only, never force) and your own task branch.
Solution publication and merge stay with the owner. Never touch another row, the
owner's checkout, or their local branches. State the base SHA in the first commit
and the PR body.

## Read, in order

1. `ROADMAP.md` row for the ID — status, owner, dialogue, complexity, dependencies.
2. `docs/tasks/<pxx-tyy>.md` in full — scope, owned paths, acceptance, required
   checks, owner decisions. This is the specification; nothing outside it is in scope.
3. `docs/DOCS_INDEX.md` — the one row for your subsystem, then that document and
   the call sites you touch.
4. Accepted contracts the card names (`docs/audit/...`) — only the needed section.
5. `tests/AGENTS.md` before any test work.
6. `gh pr list --state open` — overlaps on your owned paths.

Do not preload other cards, phases, `PRODUCT_PLAN.md`, audit history, or all of
`BUSINESS_MODULES.md`. Record what you actually read (path + purpose) in the card
or PR — that is evidence.

## Execute

- Claim: row `IN_PROGRESS` with owner and dialogue, card updated, before edits.
- Own worktree and one thematic branch from `origin/dev`; Draft PR to `dev`.

### Publish early, publish often

The lane's workspace can be restored from an older snapshot at any moment; a
reset destroys the worktree, the branch and its local objects together, and
nothing local survives it. Local-only work is therefore not work.

- Push the task branch to `origin` with the **first useful edit**, however
  unfinished, and open the Draft PR at the same time. No waiting for a green
  suite, a complete feature or a tidy history.
- Commit each independently green portion — one focused test, one surface, one
  migration step — instead of one large commit at the end. Never hold more than
  about one check cycle of work only on disk.
- Push after every such commit. `--force-with-lease` on your own branch only;
  never force-push `dev` or a branch someone else owns.
- Keep the Draft PR body current (tested SHA, what passes, what is still
  deferred) so a resumed session can continue from `origin` alone: fetch, read
  the PR, find the last pushed SHA. Record in the card that the branch is
  pushed and its SHA, so recovery never depends on a local reflog.
- Draft status is a workflow flag, not a publication veto: the early push
  publishes a branch, not an accepted solution. Hand off and merge rules below
  still apply.

A reset is a normal operating condition, not an incident: when the worktree
returns from an older snapshot, re-create it from `origin/<task-branch>`, never
re-implement what already reached the remote, and state the recovered SHA.
- Follow `pyproject.toml` style (Black + isort, 120), reuse existing
  managers/services/ORM models, no ad-hoc sessions, no non-Alembic migrations,
  no refactors, no Celery/Redis/Streamlit. Keep approval gates, dry runs and
  fail-closed checks intact.
- Stay inside owned paths; edits outside them are a separate labelled commit
  checked against open PRs.

## Test

No database on the remote lane. Run only DB-exempt tests, explicit paths,
`--noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header`,
boundaries replaced by explicit mocks. Hand every DB/integration scenario to the
local runner: exact branch, SHA, commands, scenarios, listed under **Deferred
scenarios** in the PR body. Never report a deferred scenario as passed, and call
mocked evidence mocked. Require the local runner to record fresh dev/test-schema
Alembic alignment (or the isolated schema's explicit baseline/task head), exact
revision, DB results and cleanup under the database safeguards. Unit/mock passes
do not satisfy required DB scenarios.

## Hand off

Draft until the PR body carries tested SHA, exact test paths, passed/failed/
skipped counts, execution route and deferred scenarios. Required deferred checks
keep the task IN_PROGRESS and the PR Draft until linked local evidence passes
(or the owner explicitly waives a named check, still labelled unrun). Only then
update the card and set the row `READY`; do not duplicate status in the phase, do not self-accept and
do not delete the row — the auditor does that. Fetch again and state the SHA you
synchronized to.
