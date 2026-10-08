# Tenant-safe digest destinations

## Scope and baseline

Task branch: `ai/tenant-safe-digest-destinations`, target `dev` only. Started from
`a531a3cfbe4797cdaa8c709925f9f225fb1d6a2f`; synchronized with the user's new dev
`021105978e1777c7ef7fc813355e563f0f62e7d4` for the current recheck.
The new summary-derived analysis headings and mention-axis labels are preserved,
including the latest changes in `docs/DIGEST.md`.

This is the bounded routing portion of PRD-01. No migration, enum, production
configuration/data, provider fleet or live commenting change is included.
It is not the complete retry/concurrency production gate.

## Implemented contract

- Resolve one existing active workspace before any send. A non-operator cannot
  send without ambient workspace context or override it with a foreign ID.
- A trusted unscoped operator run selects `DEFAULT_TENANT_SLUG`; missing or
  inactive bootstrap fails closed rather than creating a workspace on send.
- Scope the **whole** `build_and_publish` run to the selected workspace with
  bypass disabled: aggregation, LLM context, digest-run records and broadcast.
  Restore the caller's context afterward. This prevents operator manual sends
  from building a combined report and only choosing a tenant at delivery time.
- Only active, digest-enabled `tenant_channels` of that workspace are recipients.
  Explicit ownership/activity checks backstop the manager query.
- Deprecated env IDs are ignored for every workspace, including bootstrap.
  `TELEGRAM_ADMIN_CHAT_ID` is never a digest target. All result keys consistently
  use `transport:chat_id`; consumers of legacy `telegram` / `max` keys must update.
- Deduplicate each normalized `(transport, identifier)` within one call;
  trim identifiers, compare `@usernames` case-insensitively, and keep transports
  separate. Do not guess that a numeric ID and a username refer to the same chat.
- Apply `channel_filter` to bound targets, not only env targets. Missing transport
  credentials return a failed result for that owned target; no eligible bindings
  return an empty result. Keep existing builder retry/skip behavior otherwise.

## Compatibility/setup action

An env-only install will no longer deliver until the intended recipient is an
active workspace binding with `is_digest_target=True`. Use the existing
operator/channel-binding workflow and workspace Settings → Channels digest
flag. No binding is silently created, no quota is bypassed and no migration is
required. Prefer one stable identifier per destination.

Manual unscoped operator publication is bootstrap-only, not an all-client
aggregate. Explicitly scoped tasks/direct runs publish only their workspace.
Scheduled idempotency and manual force/resend semantics are otherwise unchanged.

## File groups

| Files | Change |
|---|---|
| `app/channels/registry.py` | Shared active-workspace resolver; authorized bound targets; ignored env destinations; within-call dedup and uniform transport filter. |
| `app/core/config.py` | Document deprecated destination settings and the distinct legacy admin fallback; retain accepted fields for compatibility. |
| `app/services/digest/builder.py` | Non-bypass workspace envelope around the existing entire build, without changing the public call signature. |
| `tests/test_digest_destinations.py` | 21 regressions, marked tenancy to disable legacy manager bypass; real PostgreSQL binding/analytics/run queries, mocked transports/LLM. |
| Existing channel/builder/e2e tests | Explicit bootstrap context and recipient binding fixtures; no assumption that env alone authorizes a send. |
| README, DIGEST/CHANNELS/CLI/CONFIGURATION docs | New contract, setup impact and operator manual-run scope. |
| Readiness/roadmap/proposal/index docs | Mark routing progress and link this evidence; leave remaining PRD-01 work open. |

## Verification

### Earlier validation, before removing bootstrap env aliases

Environment: Python 3.13, PostgreSQL 15.18 on sandbox loopback only; project
`.venv`, pinned requirements plus `httpx==0.28.1` (declared in pyproject but
missing from requirements.txt). Test DB `digest_tests`, schema `digest_test`;
no deployment DB or user credentials. Local DSN/UTF-8 setup errors and a missing
period field in a new test fixture were corrected before the successful runs.

- Focused destinations/channel/builder/e2e suite: **50 passed, 1 warning** in
  5.39 s on a freshly reset isolated test schema.
- Full suite on the starting dev plus routing changes: **994 passed, 1 skipped,
  10 warnings** in 207.95 s. Existing deprecation warnings remain.
- Full suite after synchronizing dev `3256cdf`: **1002 passed, 1 skipped,
  10 warnings** in 216.57 s, on a freshly reset isolated schema. Those results apply to the earlier PR revision.
- Targeted Black/isort checks passed. No repository-wide reformat.
- `alembic heads/current`: **0086** on the local test schema; `alembic check`
  reports **No new upgrade operations detected** with DB_SCHEMA=digest_test.
  This does not describe any deployed database revision.
- Targeted compileall, `git diff --check` and changed review-link checks passed.
  Remote code/content and base ancestry are verified at handoff.

### Current validation on dev `0211059` and DB-only destinations

- Collection: **1019 tests collected** (11.86 s); no enum collection/import failures.
- Final focused destinations/channel/builder/e2e: **51 passed, 1 warning**
  (6.98 s). Includes both transports with a matching/changed deprecated env ID,
  and no fallback to a configured admin chat when no bindings exist.
- Initial fresh-schema full run with coverage: **1 failed, 1017 passed,
  1 skipped, 10 warnings** (372.96 s). Failure:
  `test_task_timezone.py::test_tick_advances_a_task_in_the_workspace_zone`
  expected one enqueued task but got two.
- Fresh-schema isolated timezone file: **2 passed, 5 warnings** (3.40 s).
- Another fresh-schema full run, coverage disabled: **1018 passed, 1 skipped,
  10 warnings** (271.83 s). Do not erase the initial failure: the scheduler test
  is not yet proven stable. Its test file, runner and shared fixtures are unchanged
  from dev. The legacy test fixture bypasses manager tenancy guards, so unrelated
  due tasks/timing are a possible cause, not a conclusively isolated diagnosis.
  No scheduler application logic or assertions were weakened to get this result.
- Local test-schema Alembic heads/current: **0086**;
  check: **No new upgrade operations detected**. No new migration.
- Final targeted Black/isort, compileall and diff whitespace checks passed.
  Latest analysis/mention-label changes from dev are preserved.

Real integration regressions verify both default bootstrap and explicitly
selected operator runs: analytics reads contain only the selected fixture,
DigestRun is written to that tenant and only its bound destination receives the
rendered text. Context/bypass is restored after return. Other regressions cover
two client workspaces with global env configured, foreign overrides, missing
scope/workspace, inactive/disabled/foreign bindings, filtering, duplicate IDs
and unavailable transports.

No deployment, live Telegram/MAX send, production migration or complete
production-readiness certification is part of this task.

## Configuration decision and next boundary

Application VK credentials/callback and the shared Telegram bot token stay in
env. Personal VK tokens and MTProto sessions stay in the encrypted user vault.
Workspace digest recipients stay in existing `tenant_channels`. No tables,
migrations or deployment `.env` files were changed.

The legacy notification service still defaults to `TELEGRAM_ADMIN_CHAT_ID`.
Collection errors can include customer source names/raw error text; report/trend
helpers omit a recipient too. That path is not fixed by digest isolation.
The next bounded notification task should require explicit owned recipients
for workspace messages and allow only scrubbed, explicitly designated operator
alerts to use the admin destination. See the
[configuration warning](../CONFIGURATION.md#application-credentials-and-recipient-boundaries).

## Deliberate remaining work

- Durable per-destination/part progress and retry only unfinished deliveries.
- Concurrent-run publication locking and ambiguous timeout acknowledgement policy.
- Canonical platform identity resolution for numeric-ID/username aliases and
  ownership changes; identifier normalization is not a remote identity proof.
- General agent authorization/global fleet boundaries, complete spend accounting,
  operational health/restore and retention gates remain separate tasks.

Do not mark PRD-01 fully complete or enable new publication features because
this routing PR passes. Merge/deploy only after the owner reviews the setup
impact; recheck fresh dev again if it changes before merge.


## Pre-merge automated review follow-up

- `_destination_id(None)` now returns an empty identifier, so malformed manager
  rows cannot send to the literal string `None`. The defensive binding regression
  includes this case. Real `tenant_channels.chat_id` is already non-nullable;
  this is a defensive improvement, not evidence of a live data disclosure.
- The reported removal of a `build_and_publish(tenant_id=...)` argument is not
  reproduced: dev `0211059` and this branch have the same public signature,
  neither accepts it. Existing CLI/job/tool callers rely on workspace context.
  Tests cover bootstrap and selected operator scope without inventing a new API.
- After the guard change: focused destinations/channel/builder/e2e suite:
  **51 passed, 1 warning** (6.44 s); targeted Black/isort and whitespace checks pass.
  Combined notification/digest validation is recorded in the notification review
  when both changes are synchronized. Earlier full-suite results remain historical.
