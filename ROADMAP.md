# Audit execution ledger

This file is an execution checklist populated only for one explicitly
requested audit or long-running Goal. It is not the product backlog; that remains
[docs/design/README.md](docs/design/README.md).

Add task rows only after an audit verifies a gap against current code and
deduplicates findings. Keep this section empty between executions. When an
execution closes, clear completed rows and move still-open product work to the
canonical board.

## Tasks

<!-- Accepted audit tasks are recorded below this line. -->


## Suggested Goal

```text
/goal Проведи доказательный аудит завершенных пунктов в docs/IMPLEMENTATION_STATUS.md
и связанных merged PR против текущего dev. Раздели независимые направления между
небольшим числом Codex subagents; каждый возвращает только подтвержденные пробелы,
ссылки на файлы/PR/SHA, риск и критерий приемки. Удали дубли и внеси в ROADMAP.md
только подтвержденные задачи с зависимостями и непересекающимися файлами. Затем
реализуй независимые задачи отдельными Codex subagents в отдельных worktree,
интегрируй и проверь каждую; после приемки поставь [x] и сделай отдельный локальный
commit. Тесты запускай только через Kilo по правилам AGENTS.md/.agent/rules.md и
последовательно из-за общей БД. Сохраняй незавершенные задачи и блокеры; не
объявляй их выполненными. Остановись, если доказательства или безопасность БД не
позволяют продолжать.
```

- [ ] ACCESS-CATALOG — separate fixed platform/workspace roles and stable domain resources; accept: migration preserves assignments without elevation, registry and enum checks pass; depends: none; owns: types, role/permission models, migration, setup; owner: Codex; status: IN_PROGRESS.
- [ ] ACCESS-POLICY — unify admin/API/web and transactional team controls; accept: cross-workspace, operator/member, revocation and last-owner cases; depends: ACCESS-CATALOG; owns: core/API/admin/web/tenant managers; owner: Codex; status: IN_PROGRESS.
- [ ] ACCESS-MEMORY — personal memory and explicitly granted team AI; accept: personal transcript/facts/watermarks and approval revocation isolation; depends: ACCESS-POLICY; owns: agent runtime/learning/memory/jobs; owner: Codex; status: IN_PROGRESS.

### Access validation evidence

At commit `bcba2befad505425684754ad06134947c661b6ff` plus the current access-model
working diff, all checks used the primary OpenCode runner pinned to
`QwenPlan/deepseek-v4-flash-0731`. The original three-file no-DB scope
(`tests/test_access_policy.py`, `tests/test_access_team_policy.py`,
`tests/test_personal_agent_memory_scope.py`) collected and passed 72 tests.

A separate collection-only check of 56 changed test modules collected 863 tests
with no import errors; no tests ran in that check. The final unified 14-file
isolated scope collected 327 tests and passed 327/327 (exit 0; 49 unittest
subtests; four deprecation warnings). Collection and execution used
`.venv/bin/python -m pytest` with `--noconftest -o addopts=`, collection flags
`--collect-only -q --no-header`, and execution flags `-q --tb=short --no-header`.
The exact files and passed counts were: `tests/test_access_policy.py` 60,
`tests/test_access_team_policy.py` 7, `tests/test_personal_agent_memory_scope.py`
6, `tests/test_api_register_privileges.py` 4, `tests/test_identity_model_layout.py`
40, `tests/test_runtime_identity_authorization.py` 18,
`tests/test_runtime_confirmation_authorization.py` 26,
`tests/test_web_perms_active_default.py` 9,
`tests/unit_web/test_workspace_access.py` 66,
`tests/unit_web/test_settings_scopes.py` 37,
`tests/test_identity_permissions_boundary.py` 19,
`tests/test_digest_send_permissions.py` 10,
`tests/test_action_tool_authorization.py` 9, and
`tests/test_tool_dispatch_permissions.py` 16. The access-policy file includes
the migration-0096 duplicate-model permission-map regression against in-memory
SQLite. Full command evidence is in `.agent/test-report.md`.

Final focused rerun of `tests/test_access_policy.py` collected and passed 62/62
through OpenCode after adding the SQLite migration regression. This is a
separate rerun of one file and is not added to the 327-test unified count.

Migration 0096 was applied to the configured local development PostgreSQL
database `social_manager`, schema `public`; Alembic reports `0096 (head)`.
Before the successful attempt, two failed attempts rolled back fully to 0092;
0093–0095 were not stamped or committed separately. A private `public` archive
was validated at `/private/tmp/smai-pre0096-94rkqwmx/pre-0096-public.dump`
(347 archive entries, directory 0700, file 0600). Postflight found 31 resource
rows with no unmapped or retired metadata, the physical empty `oauth_pending`
table intact, platform roles 3, workspace roles 5, and zero orphans across 56
public foreign keys. Business row counts matched the pre-migration snapshot.
Detailed redacted evidence is in `.agent/access-migration-task.md`.

`alembic check` still reports broad model/schema drift (including metadata
schema and table/index/FK/default/comment differences); that drift was not
repaired as part of this migration. PostgreSQL application behavior under
concurrent team mutations and real ASGI acceptance remain pending, so the
access-model work remains IN_PROGRESS.

## Task format

Each task must have one observable outcome, acceptance evidence, dependencies,
exclusive file ownership and an owner. TODO, IN_PROGRESS and BLOCKED stay
unchecked. Use [x] only for DONE after review and acceptance at the tested SHA.

    - [x] AUD-01 — <outcome>; accept: <evidence>; depends: <IDs or none>;
      owns: <paths>; owner: <agent>; status: TODO

Statuses: TODO, IN_PROGRESS, BLOCKED, DONE, DROPPED. A blocked task stays
unchecked with the blocker and next required input recorded.
