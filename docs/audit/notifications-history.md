# Notification implementation history

Historical source-pinned checks and integration notes. They are not current acceptance evidence.

Historical source-pinned sandbox checks are linked from the original PRs. Owner-local
A/B acceptance recorded 77 checks at `807c0c7`; this did not certify C/D or a
release. See [IMPLEMENTATION_STATUS](../IMPLEMENTATION_STATUS.md).

## Historical transport scope and status

Task branch: `ai/workspace-notification-boundaries`, based on dev
`021105978e1777c7ef7fc813355e563f0f62e7d4`. This is a bounded notification
routing change, not a production-readiness certification. No table, migration,
production configuration or credential value changes are included.

Digest isolation was delivered separately in PR #3 and merged into dev as
`7f1d8c18e54fb93c811951dd781e542684d07c42`. This branch is now synchronized
with that dev, so it includes the DB-only digest recipient/build isolation and
the missing-identifier guard. The notification implementation does not change
digest retries or the general Telegram/MAX channel adapter. Integration removes
obsolete notification-gap statements and validates both implementations together.

## Verification

### Standalone notification validation on dev `0211059`

Completed on the dev baseline above plus this change, with Python 3.13,
PostgreSQL 15.18, the project `.venv`, pinned dependencies plus the existing
`httpx==0.28.1` pyproject dependency. Only sandbox database `digest_tests` and
schema `digest_test` were used; no deployment DB, user credentials or real
Telegram/VK/MAX send.

- Collection: **1055 tests collected** (3.80 s).
- Notification delivery + existing ASGI admin authorization + web notification
  regressions: **79 passed, 5 warnings** (12.94 s).
- Includes **57 new tenancy-marked notification regressions**, real workspace/
  binding/notification queries and mocked HTTP. Covers missing/foreign/inactive/
  ownerless recipients, trusted operator selection, helper/service/admin callers,
  exact transport/recipient backstops, catalog-only alerts, legacy content discard,
  transport errors/HTML/size, source ownership and failure independence.
- Full suite on a freshly reset isolated test schema, coverage disabled:
  **1054 passed, 1 skipped, 10 warnings** (284.55 s). Existing deprecation warnings
  remain. No unrelated scheduler code/test assertion changes were made.
- Alembic heads/current on the local test schema: **0086**;
  check: **No new upgrade operations detected**. No new migration.
- Targeted Black/isort, compileall, whitespace and documentation-link checks passed;
  no repository-wide formatting.

Initial new-test failures were corrected in test setup/assertions: a log
substring also matched the honest failure message, and the dummy admin request
lacked authorization-wrapper state. The action-body test now explicitly unwraps
that wrapper; the unchanged ASGI admin permission suite is run alongside it.
Application authorization was not bypassed or weakened to repair those tests.

These are local regression results, not live delivery/deployment certification.
Recheck fresh dev and the published source before handoff/merge.

### Combined integration validation before the second merge

PR #3 was merged into dev as `7f1d8c18e54fb93c811951dd781e542684d07c42`.
PR #4 was synchronized in `81d483a89101ab1ec45ddf9d16cc28165b53e97c`.
The merged digest sender includes the reviewed missing-identifier guard; its
public builder signature remains unchanged. This follow-up reconciles the
operator-alert setting comment and removes obsolete notification-gap text in
CONFIGURATION/readiness/digest review documentation.

Completed on the combined source snapshot, with mocked external transports:

- Collection: **1076 tests collected** (3.59 s).
- Digest/channel/builder/e2e + notifications + admin authorization + web
  notifications: **130 passed, 5 warnings** (18.06 s).
- Full suite on a freshly reset isolated schema, coverage disabled:
  **1075 passed, 1 skipped, 10 warnings** (278.12 s).
- Local test-schema Alembic heads/current **0086**;
  **No new upgrade operations detected**. No schema/env/deployment changes.
- Targeted Black/isort, compileall, whitespace and documentation-link checks
  pass. The user's analysis/mention-axis changes remain preserved.
- Notification automated review found no issues on the implementation. The
  digest None guard was fixed; the claimed removal of a tenant_id argument
  was disproved by comparison with dev and documented in PR #3.

These results validate the integration locally, not a production deployment or
all remaining release gates. Per-target retries/concurrent publication and the
historically observed scheduler-test instability are not declared solved.
