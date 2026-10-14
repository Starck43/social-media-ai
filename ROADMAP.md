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

- [x] AUD-01 — Enforce the home source-loading gate independently of task VIEW rights; accept: `agenttask.view` without `source.view` loads neither linked Source objects nor active Source IDs, still shows permitted tasks, and does not infer inactive/unscheduled state from unavailable evidence; reviewed diff and focused Kilo PASS; depends: none.
  owns: app/web/dashboard.py, app/web/templates/web/dashboard.html, tests/test_merged_home_acceptance.py, tests/unit_web/test_dashboard_overview.py; owner: Codex coordinator; status: DONE — reviewed diff accepted; Kilo PASS at `2dd3161` plus working diff: 26 passed / 0 failed / 0 skipped, including 15 real home cases and 11 isolated home contracts. No linked-source or active-source SQL without source VIEW; unknown status is explicit.

- [ ] AUD-02 — Preserve known attention during partial-source failures while applying fresh successful-source authorization and deduplication; accept: failed-source items are not silently lost, successfully refreshed forbidden/resolved items disappear, task/event overlap stays deduplicated, count/list agree and state stays explicitly incomplete until recovery; reviewed diff and focused Kilo PASS; depends: none; execute after AUD-01.
  owns: app/static/js/shell.js, app/services/notifications/attention.py, tests/web_shell_state.cjs, tests/test_web_notifications.py, tests/test_merged_notifications_acceptance.py, tests/unit_web/test_attention_feed.py; owner: Codex coordinator; status: TODO — confirmed at `2dd3161`: `load()` replaces the whole list on `complete:false`. Whole-list retention is also unsafe after successful authorization refresh; settle reconciliation first. Update legacy raw-title/read-POST expectations against the typed-label, workspace-context and actual read-permission contracts within this task, not as a separate refactor.

Checkpoint: follow-up logic audit of the original home cleanup and shell/notification A/B only; C/D remain outside scope. AUD-01 is accepted against `dev` `2dd3161` plus working diff; AUD-02 persists; the intervening membership fixes do not alter these paths. Prior Kilo PASS results remain scenario evidence, not acceptance of these newly identified gaps.
Execution order: review requirements and solution logic, make bounded fixes, reassess obsolete tests, then run necessary checks sequentially through Kilo under R8/R10. No tests or code changes were made while recording these tasks. The coordinator owns documentation/evidence updates; keep the product board unchanged and clear these rows only after acceptance.

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

    - [x] AUD-01 — <outcome>; accept: <evidence>; depends: <IDs or none>;
      owns: <paths>; owner: <agent>; status: TODO

Statuses: TODO, IN_PROGRESS, BLOCKED, DONE, DROPPED. A blocked task stays
unchecked with the blocker and next required input recorded.
