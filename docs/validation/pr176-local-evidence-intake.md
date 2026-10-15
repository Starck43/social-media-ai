# Manual digest-send: current-model local intake

This assignment **replaces** the previous intake and old pinned DB task.
Source of truth: dev `eb6cad6c56e78768b6482eba312082b849d24712` and the current
PR #176 head. Do not restore old roles, platform fallback, bare tool codenames,
authorization version 2 or Kilo-primary/step5 instructions.

## Read-only first; return and stop

Read current `AGENTS.md`, `.agent/rules.md`, `.agent/workflow.md`,
`.codex/agents/test-runner.toml`, `docs/ACCESS_MIGRATION.md` and the
[acceptance record](pr176-digest-send-acceptance.md).

1. Inspect `git worktree list`; locate the existing verification worktree.
   Historically it was `/Users/admin/Projects/social-media-ai-pr176-verify` at
   `0c18bab`, but neither that revision nor its private files are current truth.
   Return actual root/HEAD/branch/dirty paths; preserve all owner changes and
   backups. Do not switch, repin, reset, overwrite or copy old files into dev.
2. Check active pytest processes using the exact project preflight
   `pgrep -fl pytest`, without operators. Return only relevant IDs/state, not
   environment variables or secret-bearing command lines. Check for relevant
   previous runner activity; do not kill processes.
3. Report the current worktree interpreter and whether the new enum/catalog
   imports and model locations are present by read-only source inspection.
   Do not import settings/models or trigger application/test bootstrap.
4. From already available count-only/catalog evidence, report the shared
   `test_schema` migration/column/role-catalog state. Distinguish it from the
   documented migration of development `public` to 0096. If the test schema's
   state or previous cleanup is unknown, mark it BLOCKED; do not connect or run
   tests to guess, and do not migrate/reset/reseed it. Identify the minimal
   read-only reconciliation needed for the coordinator's next assignment.
5. Locate the existing private `.agent/digest-db-validation-task.md`,
   `tests/plugins/bootstrap_free.py` and `tests/test_digest_membership_matrix.py`.
   Return existence, tracked/dirty state and hashes. Inspect locally for old
   role/enum/import/schema assumptions, permission keys and approval version.
   Treat them as historical candidates, not executable acceptance. Return only
   relevant sanitized mismatches; never recreate/overwrite missing candidates.
6. Confirm the current primary route: OpenCode `build`, model
   `QwenPlan/deepseek-v4-flash-0731`, worktree interpreter/root and readable task
   attachment. Kilo is secondary only when primary is BLOCKED. Do not change
   global config, install tools, start agents or execute tests during intake.
7. If existing permitted runner output contains the first old `exit 3` exception,
   return its redacted class/message/file/line and actual interpreter/root/plugin
   arguments. Do not rerun an obsolete revision to recover it. Never load or
   return the full human-audit-only `.agent/test-report.md`; request a sanitized
   human excerpt when no permitted runner output exists. Unknown stays unknown.

```text
ROOT / HEAD / BRANCH / DIRTY PATHS:
ACTIVE RUNNERS:
INTERPRETER / CURRENT SOURCE COMPATIBILITY:
TEST_SCHEMA 0096 EVIDENCE (not development public):
PREVIOUS CLEANUP / REQUIRED READ-ONLY RECONCILIATION:
PRIVATE CANDIDATES: status, hashes, relevant stale assumptions
CURRENT OPENCODE ROUTE / BLOCKERS:
HISTORICAL REDACTED EXCEPTION (only if available and relevant):
```

## Next phase, not authorized by this intake

After intake review, prepare exact current-model integration scope using the
maintained dev tests, a reviewed bootstrap-free fixture strategy and any missing
digest-specific matrix. Cover non-owner/non-superuser workspace grants; all five
presets; platform-admin/workspace-viewer denial; AI and role revocation before
and after pending consumption; real middleware refresh, CSRF and tenant binding;
and database CAS/cleanup. Publisher, LLM and transports stay mocked.

Existing `test_web_digests_jobs.py`, `test_agent_tool_registry.py` and
`test_permissions.py` have changed in dev; old file names alone are not a valid
run assignment. No blind reuse of the private matrix or old plugin. Preserve
reference catalogs, other users' data, guards and journal evidence before/after.
Run current enum/import collection before execution, through OpenCode-primary
R10, with exact assigned positional paths, interpreter/root and isolated flags.
No coordinator pytest fallback. Shared PostgreSQL tests stay sequential.

The existing shared target approval is not revoked or requested again. It does
not authorize schema reset/drop/create/stamp/migrate/reseed, bootstrap, live
providers/senders or deployment. Unknown prior state still stops under R4.
Do not repeat the 47 completed mocks; do not mark Ready, merge or claim acceptance.
