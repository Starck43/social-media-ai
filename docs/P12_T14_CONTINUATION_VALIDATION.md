# P12-T14 continuation validation

## Scope and hard hold

Owner-supplied unfinished source-picker patch was applied with `git apply` to
`7b6331d2` in an isolated worktree. Picker, push/api compatibility,
recovery/reconsent, retention and consumer/migration code checkpoints are in the
single Draft #308. Runtime remains default OFF. No real provider, actual DB,
migration apply/downgrade, merge or deployment was performed or authorized.

## Common safe test command

Exact tested local code SHA: `f3b1cc3a695893524acb4c1bccca1ae486cc51bb`.
The following is the recorded isolated environment command, not a production
bootstrap. The external safe wrapper uses synthetic values, an ephemeral key,
invalid loopback DB address, socket/DNS tripwires and disabled scheduler/runtime.
These scopes intentionally do not load root DB conftest.

```sh
/data/p12t14-continuation/safe-python -m pytest \
  tests/security/test_p12_t14_telegram_account_lock_unit.py \
  tests/security/test_p12_t14_telegram_grant_access_unit.py \
  tests/security/test_p12_t14_telegram_envelope_unit.py \
  tests/security/test_p12_t14_personal_revoke_unit.py \
  tests/security/test_p12_t14_telegram_login_provider.py \
  tests/security/test_p12_t14_oauth_policy.py \
  tests/api/test_p12_t14_connector_contract_unit.py \
  tests/api/test_p12_t14_personal_revoke_unit_http.py \
  tests/unit_web/test_p12_t14_personal_accounts_http.py \
  tests/unit_web/test_p12_t14_personal_accounts_template.py \
  tests/unit_web/test_p12_t14_workspace_grants_http.py \
  tests/unit_web/test_p12_t14_vk_oauth_guidance.py \
  tests/security/test_p12_t14_login_maintenance_unit.py \
  tests/security/test_p12_t14_continuation_unit.py \
  tests/unit_web/test_p12_t14_login_picker_http.py \
  -p pytest_asyncio.plugin --noconftest -p no:cacheprovider \
  -o addopts='' -q --tb=short --no-header
```

Native run-only tester: **511 passed, exit 0, 7 warnings, 35.78s**; clean exact
revision before/after. Log SHA256:
`2fd05921ff93e4272871281c17f4bdb1bbe863b3f022dfcd09521c4f1e084cf0`.
Warnings: SQLAlchemy declarative_base (1), Pydantic class Config (4), slowapi
iscoroutinefunction (2). These are not suppressed as security evidence.

First run at `1b485940`: 485 passed, 22 failed, exit 1. One genuine cursor bug
fixed (Bot API highwater must not become the initial personal-history cursor).
Other failures corrected missing static route, fake Result protocol and stale
copy assertion; no cases excluded. Four additional typed-requeue boundary cases
bring the complete selection from 507 to 511. Unknown commits remain one-attempt,
no automatic replay. Unit/fake-provider/ASGI tests are not PostgreSQL evidence.

## Static checks and open gates

- 1189 tracked Python sources compile via static `compile`, no application start.
- AST-only Alembic graph: 12 revisions, sole head `p12t14_tg_retention`; no migration
  imports or execution. This is NOT upgrade/data-preservation verification.
- Changed Python paths Black/isort and `git diff --check` checked. Formatter run
  one file at a time because Python 3.14 Black's pool opens an AF_UNIX socket that
  the strict mock wrapper intentionally blocks; no guard weakened.
- General `python -m scripts.ci.checks` stopped on existing stdlib boundary
  violation at `scripts/schema_guards.py:649` (local SQLAlchemy import already at
  supplied base). Its later formatting/secret stages were not completed; do not
  label the general offline command PASS or alter baseline to hide it.
- PostgreSQL recovery/consent/retention races, schema upgrade/backfill/rollback,
  encrypted data preservation, normal DB conftest/full suite, actual app E2E and
  provider verification are UNRUN under owner hold. They remain release gates.
- Synthetic local Jinja/Chromium render checks and independent audit are separate
  evidence; final audit outcome must be recorded after it happens.
