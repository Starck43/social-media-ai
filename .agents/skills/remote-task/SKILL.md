---
name: remote-task
description: Execute one bounded task from ROADMAP.md remotely via a GitHub PR lane. Load before claiming, implementing or handing off any single open task from the registry.
---

# Remote task execution

Read this skill before claiming or starting any single task from
[ROADMAP.md](../../../ROADMAP.md) remotely. Governing rules:
[AGENTS.md](../../../AGENTS.md) and [docs/COLLABORATION.md](../../../docs/COLLABORATION.md).

---

## 1. Orientation

1. Read fresh `dev` HEAD and open PRs touching the target files.
2. Locate the task row in `ROADMAP.md` and its linked active card in
   `docs/tasks/<id>.md`; read its acceptance criteria, dependencies and owned paths.
   Load only the relevant parent-phase context if the card needs it.
3. Read only the relevant subsystem doc from
   [docs/DOCS_INDEX.md](../../../docs/DOCS_INDEX.md) and its call sites.
4. Verify no other agent currently owns the row and no blocked
   dependency is unresolved.


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


## 4. Testing

Apply the **remote no-database exception** (mirrored from
`.agents/skills/remote/SKILL.md`):

- Run only genuinely DB-exempt tests in an isolated sandbox venv.
- Explicit test paths, no real secrets or external sends:
  `--noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header`.
- Replace DB/LLM/provider boundaries with explicit mocks.
- Hand all shared-DB and integration validation to the local OpenCode
  test-runner with exact branch, SHA, commands and acceptance criteria.
- Pin tested SHA; record exact scopes, counts and known limitations in
  `.agents/tmp/test-report.md` (transient; not committed).


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

- [ ] Branch created from fresh `dev`, open Draft PR
- [ ] Task claimed in ROADMAP.md and linked task card
- [ ] Implementation follows architecture/conventions
- [ ] Isolated remote tests pass with explicit mocks
- [ ] Local DB/integration validation handed off to OpenCode
- [ ] PR description carries durable test fingerprint
- [ ] Task card and ROADMAP row marked READY with evidence
- [ ] ROADMAP.md synchronized to fresh dev before publish
- [ ] No secrets/.env/migrations outside Alembic committed
