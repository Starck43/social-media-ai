# Access model migration

## Three platform levels (revision `0110`)

Revision `0110` separates the single platform owner (`superuser`) from platform
staff (`admin`, `operator`) and workspace members. Current role and permission
semantics live in [TENANCY](TENANCY.md#roles-and-permissions). The migration
widens the role catalog and promotes the explicitly selected owner account:

```
alembic -x superuser_user_id=<id> upgrade 0110   # pin the reviewed account
alembic upgrade 0110                             # deploy-time default
```

The optional `-x superuser_user_id=<id>` pins the exact account and only
promotes it when it is already an active `admin`. Without it — the normal deploy
path, where the account sitting at the shell already has prod access — the
migration falls back to the sole active `admin` and refuses when there is none
or when several exist; it never guesses between candidates. No console, API or
CLI path grants `superuser` afterwards: it cannot be assigned to another
account, and the owner account cannot be demoted or deleted from the console.
Owner-only surfaces are the role/permission catalog and the read-only table
registry; see [ADMIN.md](ADMIN.md#authentication-and-platform-authority).

The same levels decide the console's *data* scope. Console entry needs a platform
`operator`, `admin` or `superuser` role; an ordinary (invited) client never
reaches `/admin`. Inside, the owner (`superuser`) retains global workspace
inspection, while every console actor — the owner included — reads only their
**own** personal rows (`owner_user_id`), and an `admin`/`operator` reads workspace
rows only for the one selected active membership, with that membership role's
rights inside it — the rule `/app` and `/api` already apply. Unknown or missing
membership fails closed. See [ADMIN.md](ADMIN.md#admin-actor-scope).

### Bootstrapping the owner without the migration (P9-T06)

[`scripts/setup/bootstrap_owner.py`](../scripts/setup/bootstrap_owner.py) is the
same decision as `0110`, for anchors where the revision has already passed or an
owner disappeared. It is idempotent, runs on the host shell (never over HTTP) and
refuses rather than guesses:

```
python -m scripts.setup.bootstrap_owner --verify          # exit 1 when no owner
python -m scripts.setup.bootstrap_owner --login owner     # promote a named admin
python -m scripts.setup.bootstrap_owner                   # the sole active admin
python -m scripts.setup.bootstrap_owner --create --login owner --email owner@example.com
```

An existing owner is reported and left alone. Promotion requires an active
`admin`; without `--login` the command uses the sole active admin and fails when
there are none or several. `--create` needs `--login`, `--email` and a password
(`--password` or `P9_OWNER_PASSWORD`), because a secret on the command line ends
up in shell history. The role catalog must exist first (`scripts/setup/roles.py`).

Revision `0096` separates platform roles from workspace roles and replaces
screen-oriented permission names with stable resource/action identities. It is
forward-only because the old shared role model cannot represent the new
assignments without losing their meaning.

The expected upgrade path starts at `0092` and advances linearly through
`0093`, `0094`, `0095`, then `0096`. Revisions `0093`–`0095` add empty LLM
receipt, outcome and budget tables with their integrity triggers; `0096` then
rewrites local role and permission metadata and adds workspace ownership data.
No external service calls or application runtime writes occur during these
revisions, but `0096` does perform database data migration.

Before applying it, run the read-only inventory from the same application
configuration as the target database:

```bash
python -m scripts.setup.access_report
```

The report contains counts, legacy role names and registered model table names;
it does not print account addresses, invite codes, credentials or memory. Review
`unmapped_model_tables` and `unresolved` role counts before migrating. An
unmapped model table makes the migration fail closed with its table name, so
investigate it before migrating. Only entries in the migration's explicit
`RETIRED_MODEL_TABLES` allowlist are retired as stale registry metadata;
unknown entries must be reviewed and explicitly mapped or retired before
application.

The legacy `oauth_pending` ModelType registration is retired because OAuth
pending states are `kind="oauth_pending"` records inside the personal
`user_credentials` table, not a separate resource or physical table. Revision
`0096` removes only its `ModelType`, `Permission` and `role_permission` rows.
Any physical `oauth_pending` table or its data is left untouched. The report
shows retired registry entries separately from unmapped entries and counts
them so this cleanup remains visible.

Before normalizing identities, `0096` also inserts any missing `ModelType`
rows from its complete versioned resource snapshot. Existing rows and IDs are
preserved for the subsequent duplicate merge; unknown table registrations stay
untouched and still fail closed during mapping.

Revision `0096` maps legacy platform roles to `admin` or ordinary `user`, and
workspace roles to `owner`, `admin`, `manager`, `analyst` or `viewer`; unknown
assignments remain unresolved. Operators gain no implicit rights. Existing admin
grants are merged by resource/action, while workspace grants use the versioned
matrix. Owners receive AI access by default; other members remain disabled.
Personal `user_id IS NULL` memory remains legacy data and is visible only under
the compatibility rule in [TENANCY](TENANCY.md#roles-and-permissions).

Review the report and migration diff against the target. Validate the same
revisions on `test_schema` before any separately authorized public-schema apply;
follow [database policy](COLLABORATION.md#database). The migration has no
automatic downgrade; rollback requires a separately reviewed data-preservation
procedure.

After applying the reviewed migration, use `python -m scripts.setup.access_report --migrated` to produce the equivalent count-only inventory against the new
schema. The default command deliberately reads the old `is_superuser` column;
`--migrated` never references it and uses independent workspace role IDs.

Cutover requires stopping owners that still run the old schema contract, applying
0096 under separately approved environment procedures, reviewing the post-cutover
inventory, then starting the new version. Do not mix old/new application writers.
No migration, schema reset, runtime start or deployment is implied by code review.

The role enums contain only string codes and work on the project's declared
Python 3.10+ floor; display labels live in `access_catalog`. Physical `roles`
and `users.role_id` names are retained while Python uses `PlatformRole`.

## Profile updates and privilege updates are separate (AUD-03)

Profile endpoints change only account profile fields. Platform role, account
state and password use dedicated guarded operations. See the endpoint contract
in [API](API.md#update-user) and
[ADMIN password rules](ADMIN.md#password-routes-outside-sqladmin).
Regression: [`test_p10_t03_profile_privilege_split.py`](../tests/security/test_p10_t03_profile_privilege_split.py).

## Local development execution evidence (2026-10-15)

Historical local-schema operations and checks are retained in the
[access migration audit record](audit/access-migration-history.md). They do not
establish current or production database state.

## Acceptance before cutover

Isolated policy checks cover the fixed catalogs, canonical resource keys,
unknown-role denial, AI admission and personal-memory context. Test collection
also checks changed enum imports without executing database fixtures. These
checks cannot establish migration preservation or transaction behavior.

On the separately approved migrated test target, verify:

- Compare preflight/postflight assignment and permission counts; unknown
  memberships remain unresolved and ordinary accounts gain no console rights.
  Include duplicate model registrations where the lowest-ID registration has no
  permissions; grants must survive on that canonical registration.
- Exercise real admin/API/web requests for a platform operator, a platform admin
  with only viewer workspace membership, a workspace owner and an ordinary guest.
  Foreign-workspace IDs and posted tenant selectors must not widen access.
- Race two team changes against the last active owner; at least one active bound
  owner must remain. Revoke membership, role or AI access between confirmation
  and execution and confirm denial with no external side effect.
- Redeem an invitation concurrently and against an existing active membership;
  use limits and seat limits remain enforced, and an invite cannot promote an
  existing active member.
- Verify separate transcripts, learned facts and learning watermarks for two
  users in one workspace. Only an explicit owner can read legacy NULL-user facts.

Do not start the new application against the old schema. Migration execution
and the database-backed acceptance above remain separate from isolated checks.
