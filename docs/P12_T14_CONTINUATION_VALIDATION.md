# P12-T14 continuation validation

## Owner-authorized local completion — 2026-11-03

The owner superseded the historical hold below and authorized publication,
independent acceptance, `dev` integration and test/public migrations. Live
connections remain owner-run after merge and are waived for repository acceptance.
No live-provider success, runtime activation or deployment is claimed.

Tested Python 3.14.0 code checkpoint: `8304f4b77c687b6cad7281790961405b63e343c6`.
Application/migration/script code equals `6e8474635bd8761aa50cfb0e965c20151e83361b`;
later commits corrected regression doubles and added non-vacuous form fixtures.
OpenCode `build` delegated every test run to configured V2 River `tester`.

The following 24-file command used the project interpreter, normal root conftest,
`DEBUG=false`, `ENVIRONMENT=test`, synthetic keys, `DB_TEST_SCHEMA=test_schema`,
test URL `postgresql://admin@127.0.0.1:55439/sma_complete_t14`, and a separate
empty `sma_complete_working` pre-redirect working database:

```sh
python -m pytest -q --tb=short --no-header \
  tests/security/test_p12_t14_continuation_unit.py \
  tests/admin/test_p12_t14_source_binding_admin.py \
  tests/collection/test_telegram_durable_admission.py \
  tests/security/test_p12_t14_telegram_grant_access.py \
  tests/security/test_p12_t14_telegram_login.py \
  tests/security/test_p12_t14_refresh_claim.py \
  tests/security/test_p12_t14_refresh_migration.py \
  tests/security/test_p12_t14_key_rotation.py \
  tests/migrations/test_p12_t14_login_attempts_schema.py \
  tests/migrations/test_p12_t14_account_grants.py \
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
  tests/unit_web/test_p12_t14_login_picker_http.py
```

Exit 0: **658 passed**, seven existing deprecation warnings (66.77s).
The first run was 649 passed / 9 failed: stale transaction/select doubles and a
removed raw MTProto test. Corrected the doubles without weakening assertions,
replaced the raw-client case with the actual fenced oldest-first history path,
and explicitly set an existing personal cursor in preservation tests while
retaining the missing-cursor-first-push test. Intermediate failing runs remain
recorded in PR review evidence; they are not counted as passing.

Additional normal-conftest command:

```sh
python -m pytest -q -o addopts='' --tb=short --no-header \
  tests/web/test_p12_t14_workspace_grants.py \
  tests/api/test_p12_t14_account_reuse_http.py \
  tests/api/test_p12_t14_telegram_login_http.py
```

Exit 0: **32 passed**, seven existing warnings (32.64s). Real synthetic
PostgreSQL transactions cover cookie/JWT/CSRF, ownership, revoke/detach races,
rollback/cancellation and explicit login/reuse; providers are mocked.

Full real Alembic empty-schema `baseline_0118 -> p12t14_tg_retention` and canonical
reference seed passed. Before/after complete schema inventory and every table
row count matched; no temporary schemas or fixture rows remained.

Synthetic browser commands (all requests intercepted, no live app/provider):

```sh
python tests/shared/connector_account_page_fixtures.py "$fixture_dir"
node tests/browser/p12_t14_personal_accounts.cjs "$fixture_dir"
python tests/shared/p12_t14_telegram_form_fixtures.py "$form_fixture_dir"
node tests/browser/p12_t14_telegram_forms.cjs "$form_fixture_dir"
```

Exit 0: **24 personal-account cases + 28 actual form cases passed**. Form checks
require the real select/input and exercise keyboard focus and 3:1 border/focus
contrast in both themes and at 390/1440px. Eight source-picker/login screenshots
were visually inspected without observed overflow or obscured controls. The
first strict form render failed for missing `ui_label`; the fixture now registers
the shared filters and the full 28-case run passes. No CDN/live-browser claim.

The historical stdlib CI-boundary finding below is corrected by the combined
file-workspace integration's schema-guard work; its own offline check result is
recorded there. Repository acceptance does not certify live provider availability.

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
- Synthetic Jinja/Chromium visual review: 17 states in two configurations
  (1280px/light and 390px/dark), 34 renders, no observed horizontal overflow.
  Focus/Tab sampling covered picker-selected and login start/code/password/error
  in those configurations. Light input border against white measured ~1.23:1;
  dark sample ~4.78:1. This is not live E2E or a complete accessibility audit.
- Independent read-only audit of `d1a4b852` started but failed before its final
  report; replacement session creation failed. Partial findings and the exact
  next actions are in the task card. No independent audit PASS.
