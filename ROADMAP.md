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

- [x] TAIL-01 — Close PR #185 home cleanup's deferred real-ASGI/DB role/workspace validation; accept: reviewed focused diff and Kilo TEST RUN PASS for Viewer/Manager/owner/operator, no-rights and cross-workspace cases; depends: none.
  owns: tests/test_merged_home_acceptance.py, app/models/tenancy/tenant_user.py, app/models/managers/tenant_manager.py, docs/TENANCY.md, docs/IMPLEMENTATION_STATUS.md, .kilo/agents/test-runner.md; owner: Codex coordinator; status: DONE — reviewed NULL-role fix and documentation accepted; Kilo TEST RUN PASS at 01f719d plus unchanged working diff: 69 passed / 0 failed / 0 skipped (home, identity boundary, tool permissions, tenancy layout). No schema/data repair or deployment. Commit this item before TAIL-02.
- [ ] TAIL-02 — Close MERGED shell/notification A/B deferred real acceptance only (exclude unmerged C/D); accept: reviewed focused diff and Kilo TEST RUN PASS for scope/count-list/read-update parity, forbidden/revoked entities and ASGI role/workspace boundaries; depends: none; execute after TAIL-01.
  owns: app/web/notifications.py, app/services/notifications/attention.py, app/web/templates/web/base.html, tests/test_web_notifications.py, tests/unit_web/test_assistant_shell.py, tests/unit_web/test_attention_feed.py; owner: Codex coordinator; status: TODO — not started; same real-ASGI/DB preflight prerequisite. Legacy notification tests expect raw titles and use permission bypass; they do not certify the merged typed-label/access contract.

Checkpoint 2026-10-15: dev `01f719d94bf9a939f375a6d3a62f9c3ead9ea994`; concurrent integration of C/D changed 41 files since the test baseline. Owner-selected nemotron-3-ultra:free orchestrator and openrouter/free runner successfully delegated real tests at 20d6b6c: 6 passed / 1 failed, normal fixture teardown, -x stopped the remaining case. NULL role was incorrectly an owner. Pending reviewed fix removes that elevation and returns empty grants; legacy data/schema untouched. Updated home module has 13 cases. Black/isort/diff checks passed; no task commits or board edits. Latest Kilo correctly refused stale expected HEAD before MCP. TAIL-02 not started.
Next: inspect only relevant C/D interface changes against the pending fix, preserve original scope (newly merged C/D acceptance is not added), update .agent/test-task-tail01.md expected HEAD and run its home/identity/tool/layout paths once through Kilo. Target localhost:5432/social_manager / test_schema remains approved. No bootstrap/seed/reset/migrations, direct pytest or data repair. Keep rows unchecked until reviewed diff and TEST RUN PASS, then per-item local commits.

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

## Task format

Each task must have one observable outcome, acceptance evidence, dependencies,
exclusive file ownership and an owner. TODO, IN_PROGRESS and BLOCKED stay
unchecked. Use [x] only for DONE after review and acceptance at the tested SHA.

    - [ ] AUD-01 — <outcome>; accept: <evidence>; depends: <IDs or none>;
      owns: <paths>; owner: <agent>; status: TODO

Statuses: TODO, IN_PROGRESS, BLOCKED, DONE, DROPPED. A blocked task stays
unchecked with the blocker and next required input recorded.
