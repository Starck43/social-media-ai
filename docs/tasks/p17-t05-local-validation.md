# P17-T05 — deferred local validation

Assignment: [task card](p17-t05.md) · Draft [PR #487](https://github.com/Starck43/social-media-ai/pull/487)
· branch `feat/p17-t05-onec-rbac`, base `origin/dev`. These checks are **UNRUN**
in the remote lane. The PR body names the exact pushed candidate SHA; require
that SHA before testing, and publish evidence through a PR comment.

## Local runner scope

Author and run the missing PostgreSQL acceptance cases in
`tests/integrations/test_p17_t05_db.py` on this task branch. That file does not
exist yet: this is an explicit local test-authoring assignment, not a claim of
implemented or passed integration checks. Reuse `tests/shared/` fixtures and
existing managers/services; do not bare-import another test suite. Keep actual
authorization, SQL filters, commits/rollbacks and audit persistence real. Mock
only the 1C/LLM/provider transports and paid/external writes. No public-schema
access, production mutation, branch merge or deployment is authorized.

Read `tests/AGENTS.md`, `docs/process/COLLABORATION.md#tests`, `#database` and
`#migration-numbering-and-parallel-tasks`. Acquire exclusive DB writer ownership
before any access (shared runners are sequential). Resolve the effective
host/database, `TEST_POSTGRES_URL`, `POSTGRES_URL`, `DB_TEST_SCHEMA`, `DB_SCHEMA`
and credentials locally; never put secret values in comments or docs.

Use task-isolated schema `p17_t05_validation`, not a foreign active schema.
Record fresh `origin/dev` SHA and its `ScriptDirectory.get_heads()` from a
dev-based checkout, shared `test_schema.alembic_version` (read-only under the
lock), isolated schema's explicit dev baseline and candidate head. Stop for an
unexplained shared mismatch; do not stamp past it or copy unmerged migrations.
Set the isolated schema through both settings names for Alembic/pytest.

## Required real scenarios

1. **Role/account authority:** real active memberships for owner/admin/manager/
   analyst/viewer; the requested capability matrix; owner/admin identical.
   Query/propose/approve/execute/export refusals for missing/deactivated account,
   membership or tenant and for another actor/connection. A revoked or changed
   role is re-read before execution despite stale in-memory/request grants.
   Assert structured `permission_denied` logs, zero provider calls and no forged
   success/unknown events on policy refusals.
2. **Entity scope:** a connection list containing two entities, with a role map
   permitting one, plus malformed/omitted/unknown role entries. Query, metadata
   planning, stored-plan export and write execution cannot use the other entity
   or any field from it; changing the map after a preview is enforced at execution.
   Absence of the optional map preserves the accepted connection-level list.
3. **Audit filtering:** two workspaces, two connections and two users with real
   command approve/execute events, plus a non-OneC event. Combined connection/
   actor/operation/time/cursor filters are applied in SQL before LIMIT and never
   include the other tenant or non-OneC action. The actor filter selects the
   event author, never overrides the requesting actor. Empty/foreign filters
   return no rows; cast the command ID to text rather than coercing arbitrary
   audit resource IDs to integers. T04's actor-scoped audit-log is unchanged.
   `onec.audit_export` has workspace scope and intentionally does not match a
   connection filter (there is no fabricated connection identity in its row).
4. **Audited export:** authorized CSV export commits exactly one
   `onec.audit_export`/workspace success event before returning the download;
   its actor is the requester, not the selected event-author filter. Empty export
   is audited too. CSRF failure or missing OneC/audit export grants releases no
   CSV and adds no success event. Inject an audit-write/commit failure: no download
   escapes, no success is claimed. Projection contains only allowlisted envelope
   fields, never command payloads, credentials or remote error text.
5. **Migration cycle:** from the recorded dev head to placeholder
   `p17t05_onec_audit_export` (current parent `0155`); validate the three audit
   CHECKs, two capability catalog resources, their actions and role links. Empty
   downgrade restores dev CHECKs and removes only this migration's metadata;
   with an export event it refuses without deleting evidence or changing the
   schema/revision. Expire only owned synthetic evidence through the existing
   cleanup path, then empty downgrade/upgrade and verify one source/schema head.
   No migration numbering/public apply in this assignment.

## Commands and evidence

The local native test parent supplies resolved environment/lock ownership and
the exact candidate SHA to its tester; do not run these commands remotely.

```sh
git fetch --prune origin
git rev-parse HEAD
git rev-parse origin/dev
python scripts/setup_test_db.py --check
python scripts/ci/schema_inventory.py p17_t05_validation "$SCRATCH/p17-t05-before.json"
env POSTGRES_URL="$TEST_POSTGRES_URL" DB_SCHEMA=p17_t05_validation python -m alembic current
env POSTGRES_URL="$TEST_POSTGRES_URL" DB_SCHEMA=p17_t05_validation python -m alembic upgrade p17t05_onec_audit_export
```

Provision an empty **owned** schema via the project's Alembic procedure before
the cycle; never `create_all`/stamp as a substitute for migration validation.
Use the same schema in pytest (`DB_TEST_SCHEMA=p17_t05_validation`). After the
local author adds the DB file, run focused acceptance and permission regressions:

```sh
python -m pytest tests/integrations/test_p17_t05_db.py tests/security/test_web_permissions.py tests/security/test_permission_scope.py -q --tb=short --junitxml="$SCRATCH/p17-t05-db.xml"
```

The package coverage gate requires explicit focused tests (the bare command in
the card is shorthand, not a runnable CLI). Derive the current OneC manifest
selection instead of storing a stale test list; add this task's files:

```sh
python - <<'PY'
import subprocess
import sys
import tomllib
from pathlib import Path

manifest = tomllib.loads(Path("scripts/ci/coverage_packages.toml").read_text())
tests = next(entry["tests"] for entry in manifest["package"] if entry["path"] == "app/services/onec")
task_tests = sorted(str(path) for path in Path("tests/integrations").glob("test_p17_t05_*.py"))
raise SystemExit(subprocess.run([
    sys.executable, "scripts/ci/package_coverage.py", "app/services/onec", "--min", "80",
    "--tests", *tests, *task_tests,
], check=False).returncode)
PY
```

This gate is a single bounded package run, not authorization for the full suite.
Do not separately replay already-green mock nodes unless a source change affects
them. Existing T02/T04 transport fixtures now declare their capability doubles;
their passing mock checks do not prove DB policy or transaction behavior.

After every success/failure/interruption: stop owned processes, remove and verify
all owned synthetic records (users, workspaces, memberships, connections,
commands/targets, audit events and temporary role changes). Restore the agreed
isolated baseline via Alembic after evidence cleanup, not destructive row removal
to force a downgrade. Inventory/diff the schema, verify reference rows and
`alembic_version`, and preserve unrelated data. Rollback alone is insufficient.

```sh
python scripts/ci/schema_inventory.py p17_t05_validation "$SCRATCH/p17-t05-after.json"
python scripts/ci/schema_inventory.py p17_t05_validation --diff "$SCRATCH/p17-t05-before.json"
```

Publish command, tested SHA, counts/exit, failed scenario/reason, source/schema
alignment and cleanup verdict in PR #487. JUnit/inventory files are scratch, not
portable evidence. Required unrun checks keep the PR Draft and row IN_PROGRESS;
the executor records READY only after linked passing local evidence or an explicit
owner waiver naming each still-unrun check. Do not self-accept or merge.
