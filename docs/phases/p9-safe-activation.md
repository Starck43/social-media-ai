# P9 — Safe activation and platform access (gate PRD-09)

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

Today `admin` is the top of the platform ladder (`docs/ADMIN.md`): the console
backend grants it a `( *, * )` wildcard (`app/admin/authorization.py`), and
`TenantAdmin.is_accessible`, the role catalog, password/role endpoints and
billing actions all gate on `User.is_platform_admin`. "Tools for working with
database tables" are limited by which models have a registered view in
`app/admin/views.py`, not by the role.

## Tasks

- [ ] P9-T02 — platform-owner role `SUPERUSER` with a guarded assignment;
  accept: `PlatformRoleType` and the `roles` catalog gain `superuser` through an
  Alembic migration; the owner account is moved onto it; no console/API/CLI path
  can grant `superuser` to another account, and the owner account cannot be
  demoted or edited by `admin`; escalation attempts fail closed with focused
  tests; depends: none; owns: `app/types/enums/user_types.py`, `app/models/`
  role/user layer, `app/services/user/permissions.py`, `app/admin/catalog.py`,
  `app/admin/endpoints.py`, one Alembic migration; owner: unassigned;
  status: TODO

- [ ] P9-T03 — owner-level gates built on the new role;
  accept: `User.is_platform_admin` covers `admin` and `superuser` so existing
  console/workspace gating is unchanged; role/permission catalog editing and
  other owner-only operations require `superuser`; the console identifies the
  signed-in level truthfully (role label, not a generic "admin"); depends:
  P9-T02; owns: `app/models/identity/user.py`, `app/admin/authorization.py`,
  `app/admin/catalog.py`, `app/admin/endpoints.py`, `app/admin/actions.py`,
  console layout template; owner: unassigned; status: TODO

- [ ] P9-T04 — read-only table registry for the platform owner;
  accept: one console surface lists every registered model (all tables in
  `app/models/**`) with pagination, search and a detail view, gated on
  `superuser` via `is_accessible` rather than model permissions; no create,
  edit, delete, export or SQL box; every other platform role gets 403; depends:
  P9-T02, P9-T03; owns: one new admin view + template, `app/admin/setup.py`
  registration, focused authorization tests; owner: unassigned; status: TODO

- [ ] P9-T05 — owner access documentation and regression guard;
  accept: `docs/ADMIN.md` and `docs/ACCESS_MIGRATION.md` describe the
  three-level model and the assignment rule; tests pin that `operator`/`admin`
  cannot mint `superuser`, that the registry answers 403 for them, and that no
  self-registration path grants a platform role above `user`; depends:
  P9-T02, P9-T03, P9-T04; owns: the two docs, one permission regression test
  file; owner: unassigned; status: TODO

## Out of scope (explicit)

- Arbitrary SQL / query console, DDL or DML from the web.
- Owner membership in every workspace (the owner acts at platform level).
- Client self-registration / corporate onboarding (Q-11).

## Phase approval

- [ ] PHASE DONE — approved by: codex, date: —, dev SHA: —
