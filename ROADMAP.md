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

Audit scope: [2026-10-15 dev source audit](docs/audits/2026-10-15-dev.md).
These are verified TODO proposals, not implementation acceptance.

- [ ] AUD-01 — P0: Replace unauthenticated password mutation with authorized recovery; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; owns: app/admin/endpoints.py (reset_password_page/reset_password only); app/core/hashing.py (temporary-password helper only); app/templates/sqladmin/user/reset_password.html; new recovery regression tests; owner: unassigned; status: TODO.
- [ ] AUD-02 — P1: Remove password hashes from HTTP user responses; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; coordinate the same user.py/user schema write lane with AUD-03; owns: app/api/v1/endpoints/user.py (response_model declarations only); app/schemas/user.py (public response types only); new user-response regression tests; owner: unassigned; status: TODO.
- [ ] AUD-03 — P1: Separate self-service profile updates from privilege updates; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; serialize user.py/schema edits with AUD-02; owns: app/api/v1/endpoints/user.py (update_user only); app/schemas/user.py (update inputs only); app/models/managers/user_manager.py (update_user only); new privilege-update regressions; owner: unassigned; status: TODO.
- [ ] AUD-04 — P1: Use shared, cookie-independent login protection; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; keep admin/endpoints.py recovery ownership with AUD-01; owns: app/services/user/auth.py (authentication/failure accounting); app/admin/auth.py (login/lockout); app/api/v1/endpoints/auth.py (login); app/web/auth.py (login_submit only); new login-protection tests; owner: unassigned; status: TODO.
- [ ] AUD-05 — P2: Align legacy API DB probes with bounded redacted readiness; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; owns: app/api/v1/entry.py (health_check/test_database only); app/core/health.py (shared probe if needed); new legacy-probe regressions; owner: unassigned; status: TODO.
- [ ] AUD-06 — P1: Centralize safe provider-error envelopes without dropping accounting; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; accounting/admission activation is explicitly excluded; owns: app/services/ai/llm_client.py (analyze error/validation branches only); new adapter privacy regressions; owner: unassigned; status: TODO.
- [ ] AUD-07 — P1: Make digest scope, date window and row set consistent; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; do not alter delivery/checkpoint/force-resend contracts or AUD-06 adapter lane; owns: app/services/digest/builder.py (aggregate); app/services/ai/reporting.py (query/scope and aggregate methods); app/models/managers/ai_analytics_manager.py (build_period_rollups); focused digest/reporting regressions; owner: unassigned; status: TODO.
- [ ] AUD-08 — P1: Gate home pipeline queries and links per data component; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; AUD-09 follows this shared-file lane; owns: app/web/dashboard.py (_pipeline/_offers only); app/web/templates/web/dashboard.html (pipeline permission rendering only); focused pipeline permission tests; owner: unassigned; status: TODO.
- [ ] AUD-09 — P2: Make pipeline counts and activity labels truthful; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: AUD-08 (same files; never parallel writes); owns: app/web/dashboard.py (_pipeline projection after AUD-08); app/web/templates/web/dashboard.html (pipeline count/type labels); focused pipeline projection tests; owner: unassigned; status: TODO.
- [ ] AUD-10 — P2: Make distributable package include runtime code and encryption dependency; accept: [bounded contract](docs/audits/2026-10-15-dev.md#verified-findings-and-acceptance-contracts); depends: none; current checkout/requirements.txt installation is not declared broken; owns: pyproject.toml; package-data/build manifests if needed; dedicated packaging smoke checks; owner: unassigned; status: TODO.



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
