# P20-T06 — independent test run on the unmerged PR #423

Date: 2026-11-06. Reviewer: auditor (Codex session).
PR: #423, branch `feat/p20-t06-workspace-navigation`, head `e1d69f5b`,
Draft, `MERGEABLE/CLEAN`, base `dev`.
Request in the PR body: *"The local OpenCode test-runner should check out … and
record the exact new tested SHA; run focused authorization/template integration
tests under the project's normal environment, including
`tests/security/test_workspace_nav_access.py`, `tests/web/test_web_onboarding.py`
and relevant shell tests."*

## Environment

Project CPython 3.14 venv, `DB_SCHEMA=public DB_TEST_SCHEMA=test_schema`, live
PostgreSQL 15.14. This is the normal test environment, not the sandbox the PR
used.

## Result: the requested tests cannot run against `db` head `0141`

The test schema is at Alembic `0141` (P15-T05, applied to `public` and
`test_schema` on the owner's instruction). `tests/conftest.py` refuses to start
when a live table is absent from the branch's model metadata, and no branch
merged from `dev` carries the `outbox_messages` / `outbox_targets` models yet:

```
RuntimeError: Test bootstrap refused: live tables absent from this branch's
model metadata: outbox_messages, outbox_targets. Preserve their constraints
and evidence; coordinate with the schema owner.
```

Which means, measured:

| Tree | `test_p20_t06_navigation.py` + `test_p12_t01_nav_groups.py` |
|---|---|
| `e1d69f5b` (PR head, as published) | 9 errors at fixture setup — bootstrap refusal |
| PR head merged with `origin/dev` `01f7675c` | 9 errors — same refusal |
| PR head **merged with P15-T05 revision** (`3ff43835`) | **9 passed** |

The third row is the substance: the PR's own tests are green in the project
environment as soon as the branch sits on top of T05. Nothing in the PR's diff
was needed to fix them, and nothing in the PR's diff caused the refusal.

Cross-check on the same combined tree (`T05 + P20-T06`):

- `tests/security/test_workspace_nav_access.py` → passed.
- `tests/web/test_web_onboarding.py` → 2 failed, 10 passed
  (`test_wizard_posts_exactly_the_fields_the_list_pages_expose`,
  `test_wizard_labels_point_at_real_inputs`), both with
  `jinja2.exceptions.UndefinedError: 'collection_defaults' is undefined` from
  `app/web/templates/web/tasks.html`. That template is outside this PR's six
  files (`nav.py`, `_shell_nav.html`, `base.html`, `shell.css`, two nav tests),
  so the failure is a pre-existing planner-template gap, not a P20-T06
  regression. It could not be re-confirmed on bare `dev` because bare `dev`
  cannot reach the fixtures at all.

## What is still unverified

The PR body's own sandbox evidence covers a mocked Jinja shell and sandbox
Chromium at 360×800 (five labels visible, 72×64 px targets, drawer and Escape
behaviour). That is a synthetic render, not an authenticated browser: the
sidebar admission rule, permission-filtered groups and screen-reader order have
no authenticated-browser or assistive-technology evidence in this run either.

## Verdict

- Code-wise the PR is testable and green once it is not the only branch ahead of
  `db`: **9 passed**.
- The PR cannot be merged or accepted while `dev` lacks the T05 models, unless
  the reviewer is willing to accept "green only in combination" — which the
  project's evidence rules do not.
- Order that works: merge P15-T05 first, rebase #423 on it, rerun this exact set
  on the new head, then lift Draft.
- Owner decision `O-3` (mobile bar composition Сегодня / Чат / Входящие /
  Задачи / Ещё vs the previous set) is still open and is the owner's alone.

No branch, PR or schema was modified by this run; all combined trees were
detached worktrees, removed afterwards.
