# P9 — Safe activation and platform access (gate: every external write capability activates only through a contract)

Goal: every external write capability is disabled by default and activated
only via a capability-specific contract with fresh authorization, idempotency,
audit and kill switch; release claims match verified capability.

Platform access follows the same rule: the platform owner is one named account
with a distinct role, no console operation can mint that role, and every
wide-reaching read surface (the table registry) is read-only and reachable by
that role alone.

## Platform access model (decided by owner, 2026-10-24)

Three non-overlapping levels:

| Level | Where it lives | Who |
| --- | --- | --- |
| Platform owner | `PlatformRoleType.SUPERUSER` + one `roles` row | the single owner account |
| Platform staff | `operator` / `admin` (`user_types.py`) | internal operators and admins |
| Clients | `user` + workspace membership (`WorkspaceRoleType`) | self-registered individuals and companies |

Owner decisions that bound this scope:

- No arbitrary SQL from the web. Schema change stays Alembic-only; data edits
  stay in the console or CLI. The owner sees tables, but cannot run queries.
- `admin` keeps managing `admin` accounts as today (`app/admin/catalog.py`,
  `app/admin/endpoints.py`); only the owner cannot be demoted or impersonated.
- Client registration (self-serve individual vs corporate onboarding) is not
  decided yet — tracked as Q-11, not as part of these tasks.

Before this phase, `admin` was the top of the platform ladder: the console
backend granted it a `( *, * )` wildcard (`app/admin/authorization.py`), and
`TenantAdmin.is_accessible`, the role catalog, password/role endpoints and
billing actions all gated on `User.is_platform_admin`; "tools for working with
database tables" were limited by which models had a registered view in
`app/admin/views.py`, not by the role. That ladder is now closed by P9-T02–T05:
`superuser` exists as a distinct level, only the owner holds it, and the table
register is a separate read-only surface.

## Tasks

- [ ] P9-T01 — one capability-specific activation contract; accept: a single
  external write capability is named and released only through a contract that
  carries fresh authorization, idempotency, audit and a kill switch, with
  release claims matching verified capability; the registry row was published
  before this entry existed, so the concrete capability is still to be chosen
  from the current inventory rather than assumed; depends: none; owns: per
  selection; owner: unassigned; status: TODO — needs the owner to name the
  first capability before it can be claimed.

- [ ] P9-T02 — platform-owner role `SUPERUSER` with a guarded assignment;
  accept: `PlatformRoleType` and the `roles` catalog gain `superuser` through an
  Alembic migration; the owner account is moved onto it; no console/API/CLI path
  can grant `superuser` to another account, and the owner account cannot be
  demoted or edited by `admin`; escalation attempts fail closed with focused
  tests; depends: none; owns: `app/types/enums/user_types.py`, `app/models/`
  role/user layer, `app/services/user/permissions.py`, `app/admin/catalog.py`,
  `app/admin/endpoints.py`, one Alembic migration; owner: codex;
  status: READY at local dev `5365b3c` (2026-10-24). Migration
  `0110_platform_owner_superuser` defines the role row and moves the reviewed
  owner account onto it; applying it stays a separate owner-approved deployment
  step. The owner bootstrap, rank ordering and the refusal to mint or demote
  `superuser` are pinned in `tests/permissions/test_p9_owner_role_guards.py`.

- [ ] P9-T03 — owner-level gates built on the new role;
  accept: `User.is_platform_admin` covers `admin` and `superuser` so existing
  console/workspace gating is unchanged; role/permission catalog editing and
  other owner-only operations require `superuser`; the console identifies the
  signed-in level truthfully (role label, not a generic "admin"); depends:
  P9-T02; owns: `app/models/identity/user.py`, `app/admin/authorization.py`,
  `app/admin/catalog.py`, `app/admin/endpoints.py`, `app/admin/actions.py`,
  console layout template; owner: codex; status: READY at local dev `5365b3c`
  (2026-10-24). `User.is_platform_admin` covers `admin` and `superuser`, so
  existing console and workspace gating is unchanged; owner-only operations
  check `is_superuser` in `app/admin/catalog.py`; the console identity block
  names the signed-in level from `app/core/access_catalog.py`
  (`app/templates/sqladmin/layout.html`). Pinned by
  `tests/permissions/test_p9_three_level_access.py`.

- [ ] P9-T04 — read-only table registry for the platform owner;
  accept: one console surface lists every registered model (all tables in
  `app/models/**`) with pagination, search and a detail view, gated on
  `superuser` via `is_accessible` rather than model permissions; no create,
  edit, delete, export or SQL box; every other platform role gets 403; depends:
  P9-T02, P9-T03; owns: one new admin view + template, `app/admin/setup.py`
  registration, focused authorization tests; owner: codex; status: READY at
  local dev `e321a7a` (2026-10-24). The register lives in
  `app/admin/table_registry.py` and is registered from `app/admin/setup.py`;
  every view is read-only and gated on `is_superuser` rather than on model
  permissions, so `operator`/`admin` receive 403.

- [ ] P9-T05 — owner access documentation and regression guard;
  accept: `docs/ADMIN.md` and `docs/ACCESS_MIGRATION.md` describe the
  three-level model and the assignment rule; tests pin that `operator`/`admin`
  cannot mint `superuser`, that the registry answers 403 for them, and that no
  self-registration path grants a platform role above `user`; depends:
  P9-T02, P9-T03, P9-T04; owns: the two docs, one permission regression test
  file; owner: codex; status: READY at local dev `eecd30f` (2026-10-24).
  `docs/ADMIN.md` and `docs/ACCESS_MIGRATION.md` describe the three-level model;
  `tests/permissions/test_p9_owner_role_guards.py` and
  `test_p9_three_level_access.py` pin that staff cannot mint `superuser`, that
  the register answers 403 for them, and that self-registration never grants a
  platform role above `user`.

## Out of scope (explicit)

- Arbitrary SQL / query console, DDL or DML from the web.
- Owner membership in every workspace (the owner acts at platform level).
- Client self-registration / corporate onboarding (Q-11).

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
