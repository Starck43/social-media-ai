# Tenant-safe digest destinations

## Scope and baseline

Task branch: `ai/tenant-safe-digest-destinations`, target `dev` only. Started from
`a531a3cfbe4797cdaa8c709925f9f225fb1d6a2f`; synchronized with the user's new dev
`3256cdf5fd841425fd3224bb7d37c671d8d38ca1` before the final full-suite run.
The new analysis-heading work has no overlapping files and is preserved.

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
- Env IDs are not global recipients. For bootstrap only, a configured env ID may
  alias a matching owned active digest binding; it retains the legacy result
  key but never adds another send. Unbound/foreign/disabled env values are ignored.
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
| `app/channels/registry.py` | Shared active-workspace resolver; authorized bound targets; bootstrap env aliases; within-call dedup and uniform transport filter. |
| `app/services/digest/builder.py` | Non-bypass workspace envelope around the existing entire build, without changing the public call signature. |
| `tests/test_digest_destinations.py` | 20 regressions, marked tenancy to disable legacy manager bypass; real PostgreSQL binding/analytics/run queries, mocked transports/LLM. |
| Existing channel/builder/e2e tests | Explicit bootstrap context and recipient binding fixtures; no assumption that env alone authorizes a send. |
| README, DIGEST/CHANNELS/CLI/CONFIGURATION docs | New contract, setup impact and operator manual-run scope. |
| Readiness/roadmap/proposal/index docs | Mark routing progress and link this evidence; leave remaining PRD-01 work open. |

## Verification

Environment: Python 3.13, PostgreSQL 15.18 on sandbox loopback only; project
`.venv`, pinned requirements plus `httpx==0.28.1` (declared in pyproject but
missing from requirements.txt). Test DB `digest_tests`, schema `digest_test`;
no deployment DB or user credentials. Local DSN/UTF-8 setup errors and a missing
period field in a new test fixture were corrected before the successful runs.

- Focused destinations/channel/builder/e2e suite: **50 passed, 1 warning** in
  5.39 s on a freshly reset isolated test schema.
- Full suite on the starting dev plus routing changes: **994 passed, 1 skipped,
  10 warnings** in 207.95 s. Existing deprecation warnings remain.
- Full suite after synchronizing latest dev: **in progress**; the completed
  result will be recorded before this PR is handed off as ready.
- Targeted Black/isort checks passed. No repository-wide reformat.
- Final diff/local-link/migration-head and remote-content verification pending
  the completed synchronized run.

Real integration regressions verify both default bootstrap and explicitly
selected operator runs: analytics reads contain only the selected fixture,
DigestRun is written to that tenant and only its bound destination receives the
rendered text. Context/bypass is restored after return. Other regressions cover
two client workspaces with global env configured, foreign overrides, missing
scope/workspace, inactive/disabled/foreign bindings, filtering, duplicate IDs
and unavailable transports.

No deployment, live Telegram/MAX send, production migration or complete
production-readiness certification is part of this task.

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
