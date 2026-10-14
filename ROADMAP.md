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
- [x] TAIL-02 — Close MERGED shell/notification A/B deferred real acceptance only (exclude newly merged C/D); accept: reviewed focused diff and Kilo TEST RUN PASS for scope/count-list/read-update parity, forbidden/revoked entities and ASGI role/workspace boundaries; depends: TAIL-01; execute after TAIL-01.
  owns: tests/test_merged_notifications_acceptance.py, docs/IMPLEMENTATION_STATUS.md; owner: Codex coordinator; status: DONE — reviewed real ASGI/DB acceptance and corrected middleware-redirect assertions accepted; Kilo TEST RUN PASS at 807c0c7 plus unchanged working diff: 77 passed / 0 failed / 0 skipped (8 real cases plus shell/attention regressions). No notification production changes, schema operations or C/D acceptance.


Checkpoint 2026-10-15: dev `807c0c726c1a4b190e9fffbe45ec944ec336ef5d` plus reviewed test/documentation diff; both tasks accepted after Kilo PASS. TAIL-01 committed as `fdb6c18` (69 PASS). TAIL-02: 77 PASS, zero failures/skips, normal fixture cleanup. Next: commit TAIL-02 separately, then clear completed execution rows. Board and memories untouched; C/D, browser/layout acceptance and legacy notification tests remain outside this focused execution. No migrations, seed/reset, deploy or push.

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
