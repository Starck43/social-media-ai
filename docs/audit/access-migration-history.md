# Access migration history

Historical source-pinned operations and checks, not a procedure or permission to repeat them. Current database policy in [COLLABORATION](../COLLABORATION.md#database) supersedes this record.

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
not create, migrate or seed the shared target. Separate schema preparation
follows the target, backup and cleanup rules in [database policy](../COLLABORATION.md#database).
