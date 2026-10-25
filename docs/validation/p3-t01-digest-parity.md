# P3-T01 — digest send permission parity acceptance

Acceptance evidence for the ROADMAP row P3-T01. Status/owner live in
[ROADMAP](../../ROADMAP.md#p3); scope lives in the [task card](../tasks/p3-t01.md).

## Tested revision

- Branch: `fix/p3-t01-digest-send-parity`, docs-only on top of origin/dev
  `4bc03d0ae8886eea0b5343a87f5d2df3163ceb83` — the code under test.
- The task pinned baseline `c1069efbef52f956230aebe53d8ca2a9cbac78e9`; origin/dev
  advanced to `4bc03d0` (P10-T18 workspace-access repair, P10-T19 stale-suite
  registry, P10-T03 claim) before publication, so the single permitted rebase was
  applied and all checks were re-run at `4bc03d0` with identical results
  (identical results were also first observed at `c1069ef`).
- PR #176 / `fix/digest-send-permission-parity` not reused, not rebased, not reopened.

## Surface verification (source reads at the tested SHA)

- `app/agent/toolset/reports.py`: `digest_send_now` declares
  `required_permission="notifications.digest_run.create"` and `confirm=True`;
  `report_period` (preview) stays `notifications.digest_run.view` without confirm.
  Enforcement: `app/agent/tools.py` checks `has_permission_by_codename` before
  dispatch and raises `PermissionDeniedError`.
- `app/web/digests.py`: `POST /app/digests/send-now` calls
  `guard_web(request, "digestrun", "create", back="/app/digests")` exactly once.
  `WebPerms.can` (app/web/perms.py) fails closed on missing/inactive user,
  missing tenant, unknown resource/action; it requires an active membership
  whose grants contain the create right. No superuser/owner shortcut in `can`
  (`is_superuser` is always False under `/app`).
- `cli/main.py`: `digest send-now` invokes the same
  `app.services.digest.builder.build_and_publish` as the web surface, wrapped by
  `_run_platform` → `operator_permission_scope()` — a trusted CLI/operator-only
  entrypoint that HTTP/chat requests can never enter, so no viewer bypass.

## Checks actually run (sandbox, Python 3.13.15, no DB; itsdangerous 2.2.0 installed into the environment only)

| Command | Exit | Counts | Failures |
| --- | --- | --- | --- |
| `python3 -m unittest tests.security.test_digest_send_permissions -v` | 0 | ran 10, OK | — |
| `python3 -m unittest tests.security.test_tool_dispatch_permissions -v` | 1 | ran 16, 15 ok | `test_workspace_owner_cannot_reach_global_handler_in_either_path` (PermissionDeniedError not raised) — pre-existing at both tested revisions, tests/security failure out of scope per task limits (stale security suites are registered under P10-T19) |
| `python3 -m unittest tests.notifications.test_digest_send_current_boundaries -v` | 1 | ran 37, 36 ok | `test_replaced_intent_cannot_be_consumed_by_observed_old_snapshot` — stale expectation vs deliberate dedup from `82555f2` ("a staged confirmation is a visible question, never a twin", in baseline); confirmation-staging semantics, not permission parity; reported, not fixed (run-only, no redesign) |

Parity cases verified green: view-only denied (even confirmed), update-only
denied, create requires confirmation on both dispatch paths, confirmed create
dispatches identical arguments, revoked create denies a previously confirmed
send, inactive actor denied, missing identity denied, confirmation single-use,
old update-intent rejected after the permission change, platform-admin viewer
cannot send or widen a posted workspace, missing/invalid CSRF denied, web
create dispatches without owner/superuser.

## Not run (environment-dependent)

`tests/notifications/test_digest_send_postgres_acceptance.py` and
`tests/notifications/test_digest_send_asgi_acceptance.py` require the authorized
local target `localhost:5432/social_manager/test_schema`, which is not
accessible from this sandbox. Requested via PR comment for local helper
validation per `.agents/skills/remote-task/references/local-validation.md`.

## Cleanup

No DB touched, no synthetic records created, no secrets/sessions committed.
`__pycache__` byproducts removed. Branch retained until verified merge.
