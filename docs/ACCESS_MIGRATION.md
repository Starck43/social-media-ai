# Access model migration

## Three platform levels (revision `0110`)

The access model has three non-overlapping levels: the **platform owner**
(`PlatformRoleType.SUPERUSER`, one account), **platform staff** (`admin`,
`operator`) and **clients** (`user` plus workspace membership). Revision `0110`
widens the `roles.codename` CHECK, inserts the `superuser` catalog row and moves
the platform owner onto it. The owner is addressed explicitly:

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
registry; see [ADMIN.md](ADMIN.md#additional-restrictions).

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

Existing platform `ADMIN`/`SUPERUSER` roles and users with the legacy
`is_superuser` flag become platform administrators. Other platform assignments
become ordinary users; operators receive no implicit permissions. Workspace
`SUPERUSER`, `ADMIN`, `MANAGER`, `ANALYST` and `VIEWER` assignments map to
`owner`, `admin`, `manager`, `analyst` and `viewer`. Other workspace assignments
remain unresolved (`NULL`). Owners receive AI chat by default; other members
remain disabled.

Existing administrator permission grants are merged by resource/action and
retained on the canonical platform administrator. Workspace grants are seeded
from the versioned action matrix in revision `0096`. Personal memory rows remain
in the legacy `user_id IS NULL` bucket; they must only be exposed to the owning
workspace owner until explicitly re-authored into a personal memory.

Review the report and migration diff against the target environment before
running Alembic. The migration has no automatic downgrade; rollback requires a
separately reviewed data-preservation procedure.

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

## Local development execution evidence (2026-10-15)

Revision `0096` was applied to the explicitly configured local development
PostgreSQL target `social_manager/public`; Alembic reports `0096 (head)`. This
records a local migration run, not a production deployment. Before the
successful attempt, two runs failed inside PostgreSQL transactions and fully
rolled back to `0092`; revisions `0093`–`0095` were not separately committed or
stamped. A private `public` archive was validated before the successful run at
`/private/tmp/smai-pre0096-94rkqwmx/pre-0096-public.dump` (`pg_restore --list`
reported 347 entries; directory mode 0700, file mode 0600).

The migrated count report found 31 resource rows, no unmapped or retired
metadata, and no unresolved user, membership or invite assignments. Platform
and workspace role counts and business row counts matched the expected
postflight; all 56 public foreign keys had zero orphans. The physical empty
`oauth_pending` table remains intact. The complete redacted before/after counts
and assignment summary are recorded in Git history at the accepted revision.

`alembic check` still reports metadata drift. Among task-owned objects, it
reports schema qualification differences for foreign keys and the action enum,
comment-only differences on several role/user columns, and two membership
indexes present in PostgreSQL but absent from model metadata. Direct catalog
inspection confirmed the 0096 public columns, role catalogs, permission enum,
FK targets, and both partial memory indexes match the migration contract;
56-key integrity checks found no orphans. Objects reported under `test_schema`
and broad existing defaults/comments were not treated as migration defects and
were not changed.

The OpenCode no-DB authorization suite passed 327 tests, followed by a focused
62-test `test_access_policy.py` run after the resource-seeding regression was
added. Subsequent real PostgreSQL/ASGI acceptance passed 29 selected cases
through OpenCode in three sequential batches (21 + 4 + 4). Coverage includes
API/workspace boundaries, explicit operator grants, ordinary-user admin denial,
foreign queue/digest isolation, concurrent last-owner self-demotion, fresh AI
and membership revocation at actual tool dispatch, and personal facts,
transcript selection and learning cursors. Foreign learning evidence is refused
without advancing the cursor. Exact scopes and tested revision/diff are in the historical audit ledger (`git show edbe0fc:ROADMAP.md`); these checks do not certify the full suite or production cutover.

The owner also authorized an exceptional in-place update of the existing local
`social_manager/test_schema`. This schema had no Alembic version table and
already contained the receipt/outcome/budget tables and their integrity guards.
After verifying the local target, absence of test writers and a private custom
archive (399 entries; directory 0700, file 0600), the unchanged revision `0096`
operations ran through Alembic `MigrationContext`/`Operations` in one transaction
against that schema. Revisions `0093`–`0095` were not replayed; no version table
was created and no revision was stamped. This is a bounded update of an
unversioned test fixture, not evidence of a normal Alembic upgrade chain.

Postflight preserved the checked business counts, all five LLM integrity
triggers and three supporting functions; `public` counts were unchanged.
The test catalog contains three platform roles, five workspace roles and 31
resource identities, with no unmapped or retired resource metadata. Ordinary
pytest startup now validates schema/reference completeness read-only and does
not create, migrate or seed the shared target. Exceptional future schema changes
follow the explicit authorization and backup requirements in `docs/RULES.md`.

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
