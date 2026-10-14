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

- [ ] TAIL-01 — Close PR #185 home cleanup's deferred real-ASGI/DB role/workspace validation; accept: reviewed focused diff and Kilo TEST RUN PASS for Viewer/Manager/owner/operator, no-rights and cross-workspace cases; depends: none.
  owns: app/web/dashboard.py, app/web/templates/web/dashboard.html, tests/test_dashboard_role_aware.py, tests/test_merged_home_acceptance.py; owner: Codex coordinator; status: BLOCKED — owner approved target, but Kilo reported unresolved test connection environment (0 collected); HEAD changed during the run from f2e6bbd to c0c89a9. Draft fixture also lacks required AgentTask.cron_expr; diff is not accepted.
- [ ] TAIL-02 — Close MERGED shell/notification A/B deferred real acceptance only (exclude unmerged C/D); accept: reviewed focused diff and Kilo TEST RUN PASS for scope/count-list/read-update parity, forbidden/revoked entities and ASGI role/workspace boundaries; depends: none; execute after TAIL-01.
  owns: app/web/notifications.py, app/services/notifications/attention.py, app/web/templates/web/base.html, tests/test_web_notifications.py, tests/unit_web/test_assistant_shell.py, tests/unit_web/test_attention_feed.py; owner: Codex coordinator; status: TODO — not started; same real-ASGI/DB preflight prerequisite. Legacy notification tests expect raw titles and use permission bypass; they do not certify the merged typed-label/access contract.

Checkpoint 2026-10-14: dev `c0c89a943f5326fd7dcbffad2f28fcf09c723339`; TAIL-01 draft tests added, Black/isort and git diff --check passed. Kilo TEST RUN BLOCKED, 0 collected, no acceptance or task commit; TAIL-02 not started. Owner's .agent/rules.md changes preserved separately; board unchanged.
Next: reconcile the new HEAD, fix the unaccepted fixture's missing cron_expr, and establish inherited test connection settings for Kilo/pare-test without exposing credentials. Owner-approved target remains localhost:5432/social_manager / test_schema; no reconfirmation needed. No bootstrap/seed/reset/migrations or fallback pytest. Stop retained pending this handoff.
Update 2026-10-14: owner pre-approval recorded in R8 (`.agent/rules.md`) — the resolved `test_schema` target is pre-approved for real ASGI/DB runs (own data only, no reset/drop/migrate/reseed); BLOCKED lifted, resume TAIL-01.

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
