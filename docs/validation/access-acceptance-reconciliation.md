# Access acceptance reconciliation after the failed local run

## Status and evidence boundaries

Authoritative remote dev: `caec98ea02b61cad9f5c55a91924d1c60b4533c7`.
The owner relayed a 21-case local access run: 12 passed, 9 failed, no skips;
process exit unknown. A repeated nine-failure batch is not nine new regressions.
The effective run tree included owner changes; no later commit is retroactively
called the tested revision. No new tests were run for this reconciliation.

The owner's subsequent read-only R4 report establishes current observed state,
not historical cleanup: three platform roles, five workspace roles, 31 resources,
21 platforms, 234 permissions; five guard-trigger entries and three guard
functions; no competing test process or relevant blocking lock reported.
These are owner-relayed observations, not remote database verification.
Pre-0096 backups are not snapshots immediately before the failed test run.
Whole-day row markers cannot identify that run's records. No run-attributed
cleanup, absence of orphan rows or historical preservation is established.
Do not delete presumed leftovers or restore a backup to make counts agree.

## Source-confirmed failure classification

### Runtime import: four API-scope failures

`RoleManager.get_by_codename` evaluates `PlatformRoleType` although the enum was
imported only under TYPE_CHECKING. [Draft PR #198](https://github.com/Starck43/social-media-ai/pull/198)
adds only the method-local runtime import, six isolated regression methods and
its evidence record. The new tests remain NOT RUN while the remote sandbox shell
is unavailable. No claim that this one fix makes all nine cases pass.

### Workspace manager: the test asks the wrong authority

`tests/test_web_permissions.py::test_member_role_decides_for_a_non_owner`
uses a platform `user` account and a workspace `manager` membership. At current
dev, `User.has_perm_for` deliberately returns platform rights; it must NOT become
a workspace-role predicate. Membership grants are read by `tenant_users.member_grants`
and bound by the workspace context. The manager preset grants both source CREATE
and task CREATE, unlike the old test's second premise and expected task denial.

Repair the test, not the application policy: assert no platform source authority,
assert real manager membership grants for source/task creation, and verify both
positive web mutations through real middleware/CSRF. Keep ordinary platform USER,
non-owner membership and explicit workspace binding. Preserve viewer/foreign-
workspace negative coverage; do not promote the actor or stub authorization.

Sources: [user predicate](https://github.com/Starck43/social-media-ai/blob/caec98ea02b61cad9f5c55a91924d1c60b4533c7/app/models/identity/user.py),
[fixed catalog](https://github.com/Starck43/social-media-ai/blob/caec98ea02b61cad9f5c55a91924d1c60b4533c7/app/core/access_catalog.py).

### Queue header: a workspace page is not global mode

`test_jobs_page_lists_queue_health_and_failures` expects a workspace column.
The template intentionally renders it only under `is_superuser`; current /app
capabilities never grant global mode. Keep status/error/content assertions and
pin the absence of the global column for this owner request. Do not change HTML,
restore global mode or remove the separate cross-workspace isolation tests.
[Current template](https://github.com/Starck43/social-media-ai/blob/caec98ea02b61cad9f5c55a91924d1c60b4533c7/app/web/templates/web/jobs.html).

### Admin VIEWER: the old platform role no longer exists

The enum's member names are `USER`, `OPERATOR`, `ADMIN`; their VALUES are
`user`, `operator`, `admin`. Thus the reported `KeyError('VIEWER')` is NOT merely
a letter-case error. Do not replace the index with lower-case indexing, map
VIEWER to an implicitly privileged operator, or recreate the legacy enum.
An ordinary platform USER cannot enter the operator console, regardless of
workspace ownership. OPERATOR needs explicit platform grants; ADMIN is distinct.

Update the two selected legacy viewer cases to explicit ordinary-user console
read/mutation refusal. Assert the real authentication denial or exact redirect
to admin login; a generic non-200 assertion or a followed login-page 200 is not
proof. Keep all authorization predicates and handlers real. Grant-derivation
unit tests and unrelated operator-grant fixtures are a separate unvalidated
scope, not implicitly green from these selected replacements.

Sources: [role enums](https://github.com/Starck43/social-media-ai/blob/caec98ea02b61cad9f5c55a91924d1c60b4533c7/app/types/enums/user_types.py),
[admin admission](https://github.com/Starck43/social-media-ai/blob/caec98ea02b61cad9f5c55a91924d1c60b4533c7/app/admin/auth.py).

### Admin role URL: test the registered model view

`Role` is a compatibility alias for the mapped `PlatformRole`. Admin setup still
registers RoleAdmin; the registered view identity is the route authority, not the
old `/admin/role/list` string. The platform-admin test should assert the live
RoleAdmin view is registered for PlatformRole and request its actual list URL,
along with the other intended models. Do not add a legacy route or accept 404.
[Model alias](https://github.com/Starck43/social-media-ai/blob/caec98ea02b61cad9f5c55a91924d1c60b4533c7/app/models/identity/role.py),
[view registration](https://github.com/Starck43/social-media-ai/blob/caec98ea02b61cad9f5c55a91924d1c60b4533c7/app/admin/setup.py).

The existing `_operator` helper creates a user without its own cleanup context.
The selected tests must register cleanup immediately after successful creation,
and delete only their tracked fresh users in finally/fixture teardown, even
when the body fails. This source gap does not identify an actual leftover row
from the old run. Do not mutate shared role/grant catalogs for these replacements.

## Bounded local implementation assignment — no DB execution yet

1. Verify fresh dev/PR heads, current rules and dirty paths. Preserve changes
   already made by the owner; if a listed issue is already fixed, do not undo it.
   Use an isolated PR-review worktree when needed; do not resurrect the old pin.
2. Ownership: only the selected manager-membership test and necessary helpers
   in `tests/test_web_permissions.py`; the queue-header assertion in
   `tests/test_web_digests_jobs.py`; the three selected admin ASGI cases and
   necessary identity/owned-user helpers in `tests/test_admin_authorization.py`.
   No application, enum, migration, preset, catalog, conftest or setup changes.
   Do not bundle a rewrite of the rest of the admin suite.
3. Implement the exact current contracts above. Add finally cleanup of every
   new task/source and fresh account; preserve viewer/foreign-scope assertions.
   Return the scoped diff, current HEAD, dirty-path/hash attribution and exact
   replacement node IDs. If renaming legacy tests, retain old-to-new mapping;
   do not relabel old outcomes as results for the new cases.
4. Syntax/format/diff checks may inspect only the owned source. No app imports,
   tests, runner starts, bootstrap, schema changes, fixture cleanup-by-deletion,
   commit/push/merge or live provider calls in this preparatory assignment.
   Remote shell unavailability is not permission for an unreviewed test route.

## Before a future DB phase

The 931 grouped users versus 932 raw users is unresolved, not automatically
corruption. access_report builds the grouping through User.objects and emits
missing role assignments as `unresolved`; its grouping is not a join that simply
drops unknown role IDs. Different snapshots, query scopes or concurrent writers
must be investigated, not assumed. In one read-only consistent snapshot, compare
raw count, sum of role groups, NULL and missing role references, and the actual
manager query scope; return counts only. No repair or secrets.

A fresh complete baseline can support FUTURE comparisons, not certify old
cleanup. If historical state cannot be reconciled, report the remaining R4 risk
to the owner for a bounded decision about continuing from current state; do not
treat an idle process lane as that decision. Never reset/drop/create/stamp/
migrate/reseed or require the owner to reconfirm the already approved target.

After review and safety resolution, use current OpenCode-primary R10, exact
positional node IDs and separate collection/execution exits. Do not repeat the
entire 21-case batch or the 47 mocks merely to close PR #176. Those 21 selections
include queue tests, not actual manual digest-send acceptance; plan only the
remaining real membership/confirmation/CSRF/CAS coverage after deduplication.
