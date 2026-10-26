<!-- Preserved source: docs/tasks/p9-safe-activation.md; historical evidence only. -->

# P9 — Safe activation and platform access (gate: every external write capability activates only through a contract)

Phase scope and accepted-task history. [Active index](../../ROADMAP.md#p9);
detailed cards are siblings in this directory. [Product direction](../PRODUCT_PLAN.md#foundation-directions).
Historical instructions/permissions below are evidence, not current policy.

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
`app/admin/views.py`, not by the role. That ladder is now closed by P9-T02–T06:
`superuser` exists as a distinct level, only the owner holds it, and the table
register is a separate read-only surface.

## Closed tasks

<a id="p9-t02"></a>
- [x] P9-T02 — platform-owner role `SUPERUSER` with a guarded assignment;
  accept: `PlatformRoleType` and the `roles` catalog gain `superuser` through an
  Alembic migration; the owner account is moved onto it; no console/API/CLI path
  can grant `superuser` to another account, and the owner account cannot be
  demoted or edited by `admin`; escalation attempts fail closed with focused
  tests; depends: none; owns: `app/types/enums/user_types.py`, `app/models/`
  role/user layer, `app/services/user/permissions.py`, `app/admin/catalog.py`,
  `app/admin/endpoints.py`, one Alembic migration; owner: codex;
  status: DONE; Codex accepted 2026-10-25 at source tree
  `8b7b340fb9d107b353492b1c9220ebf7e2d83db9` based on dev
  `e3943f0a755118e2edf055ff87c9d66c34801032`.
  Migration
  `0110_platform_owner_superuser` defines the role row and moves the reviewed
  owner account onto it; applying it stays a separate owner-approved deployment
  step. The owner bootstrap, rank ordering and the refusal to mint or demote
  `superuser` are pinned in `tests/permissions/test_p9_owner_role_guards.py`.

<a id="p9-t03"></a>
- [x] P9-T03 — owner-level gates built on the new role;
  accept: `User.is_platform_admin` covers `admin` and `superuser` so existing
  console/workspace gating is unchanged; role/permission catalog editing and
  other owner-only operations require `superuser`; the console identifies the
  signed-in level truthfully (role label, not a generic "admin"); depends:
  P9-T02; owns: `app/models/identity/user.py`, `app/admin/authorization.py`,
  `app/admin/catalog.py`, `app/admin/endpoints.py`, `app/admin/actions.py`,
  console layout template; owner: codex; status: DONE, Codex accepted
  2026-10-25 at the same source tree as P9-T02. `User.is_platform_admin`
  covers `admin` and `superuser`, so
  existing console and workspace gating is unchanged; owner-only operations
  check `is_superuser` in `app/admin/catalog.py`; the console identity block
  names the signed-in level from `app/core/access_catalog.py`
  (`app/templates/sqladmin/layout.html`). Pinned by
  `tests/permissions/test_p9_three_level_access.py`.

<a id="p9-t04"></a>
- [x] P9-T04 — read-only table registry for the platform owner;
  accept: one console surface lists every registered model (all tables in
  `app/models/**`) with pagination, search and a detail view, gated on
  `superuser` via `is_accessible` rather than model permissions; no create,
  edit, delete, export or SQL box; every other platform role gets 403; depends:
  P9-T02, P9-T03; owns: one new admin view + template, `app/admin/setup.py`
  registration, focused authorization tests; owner: codex; status: DONE,
  Codex accepted 2026-10-25 at the same source tree as P9-T02. The register
  lives in
  `app/admin/table_registry.py` and is registered from `app/admin/setup.py`;
  every view is read-only and gated on `is_superuser` rather than on model
  permissions, so `operator`/`admin` receive 403.

<a id="p9-t05"></a>
- [x] P9-T05 — owner access documentation and regression guard;
  accept: `docs/ADMIN.md` and `docs/ACCESS_MIGRATION.md` describe the
  three-level model and the assignment rule; tests pin that `operator`/`admin`
  cannot mint `superuser`, that the registry answers 403 for them, and that no
  self-registration path grants a platform role above `user`; depends:
  P9-T02, P9-T03, P9-T04; owns: the two docs, one permission regression test
  file; owner: codex; status: DONE, Codex accepted 2026-10-25 at the same
  source tree as P9-T02.
  `docs/ADMIN.md` and `docs/ACCESS_MIGRATION.md` describe the three-level model;
  `tests/permissions/test_p9_owner_role_guards.py` and
  `test_p9_three_level_access.py` pin that staff cannot mint `superuser`, that
  the register answers 403 for them, and that self-registration never grants a
  platform role above `user`.

<a id="p9-t06"></a>
- [x] P9-T06 — consistent owner protection across role editing and account
  management; accept: active and inactive owner accounts cannot be re-roled,
  deleted or have passwords changed by staff; the role picker shows the
  protected `superuser` role; operators can assign only lower roles and the
  manager enforces the same rank boundary as the UI; focused tests cover
  allowed and refused writes; depends: P9-T02, P9-T03; owns: user manager,
  role picker, owner-guard tests; owner: codex; status: DONE, Codex accepted
  2026-10-25 at source tree
  `8b7b340fb9d107b353492b1c9220ebf7e2d83db9`, based on dev
  `e3943f0a755118e2edf055ff87c9d66c34801032`. The focused test run used
  baseline `cd7c4f3491c6c81861665777fd275961cdbfd65a` plus patch SHA-256
  `90958a3c41772e52ab145b72371a65b17efd969a9d09d2ce08d11eedca4843ad`;
  the P9 source and test files in that run match the accepted dev tree.
  OpenCode `build` (`river/deepseek-ai/DeepSeek-V4.1-Flash-1017`) ran the
  focused no-DB scope: 72 passed, 0 failed, 0 skipped across
  `tests/permissions/test_p9_owner_role_guards.py`,
  `tests/permissions/test_owner_protection_guards.py`,
  `tests/admin/test_admin_user_role_picker.py`, and
  `tests/admin/test_admin_owner_guards.py`.

## Migration acceptance evidence

- Migration `0110_platform_owner_superuser` ran without `-x` against
  `test_schema`. Following explicit owner authorization, the schema was
  cleaned after a verified private backup and seeded through existing
  managers with one active admin. Alembic advanced `0109` to `0110` and
  promoted that account to the sole active superuser. Postflight: 0 active
  admins, 1 active superuser; role digest
  `d8c5007702b2f8ac9471123a8bf83512`.
- `public` was already at `0110`; its no-`-x` upgrade was a no-op. The
  immediate pre/post active-role digest matched
  `d5fe4aaa3bf263b7fa7a3cc4312acce8`.
- Private recovery archive:
  `/private/tmp/p9-0110-backup-20261025/test_schema.dump`, SHA-256
  `7258bd3cba26cdd000874046c62dce12dbac00f4dbac5faf38f1ebcaabb9d694`.

## Out of scope (explicit)

- Arbitrary SQL / query console, DDL or DML from the web.
- Owner membership in every workspace (the owner acts at platform level).
- Client self-registration / corporate onboarding (Q-11).
