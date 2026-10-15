# PR #176: local-agent evidence intake

## Assignment

Read-only intake first; return the compact result below and stop. Do not run
tests, bootstrap the app, import settings/models, modify files, switch branches,
or repair the environment during this intake.

Read `AGENTS.md`, `.agent/rules.md`, `.agent/workflow.md` and the linked
[current acceptance record](pr176-digest-send-acceptance.md).
Inspect `git worktree list` before locating the existing review worktree.
The historically assigned path is
`/Users/admin/Projects/social-media-ai-pr176-verify`; it was previously pinned
to `0c18bab557e70b4e2dbcd378f5e8f2a65aac5c7a`. Preserve its dirty files,
private task, plugin and backups. Do not reuse a stale path without checking.

1. Return actual review root, HEAD, branch and dirty **paths**; do not return
   secrets or unrelated file contents. Check whether any prior pytest/Kilo/
   pare-test process is still active, reporting only relevant process IDs/state
   without environment variables or full secret-bearing command lines.
2. Locate these existing private candidates and return existence, SHA-256 and
   tracked/untracked/modified status. Read their test/bootstrap behavior locally
   without importing them; compare with the archived expected hashes:

   | Candidate | Historical SHA-256 |
   | --- | --- |
   | `.agent/digest-db-validation-task.md` | `fbef42d5713322f40d55b417c6a1433bc351f8dc67671a31a032107c0fbbe7e1` |
   | `tests/plugins/bootstrap_free.py` | `809d7880306d1b0dbb76a35afab94e5e446c6b9de6adeb7e52e1703829373be6` |
   | `tests/test_digest_membership_matrix.py` | `f104d3f0bf23dabdde7d1db7847b5719f2f080d7e3f9e6331e879ba4b1a10771` |

   A missing or changed candidate is a blocker to reusing that old task, not
   permission to recreate or overwrite it. Return a sanitized relevant source
   excerpt/diff if necessary for review; never embed DSNs, tokens or private data.
3. From the existing failed web-suite evidence (`exit 3`, zero collected), extract
   the **first** redacted exception: class, message, module/file and line,
   invocation's interpreter/root/plugin arguments, and whether collection,
   tests, database access or fixture cleanup actually started. Do not rerun to
   obtain a nicer error. `.agent/test-report.md` is human-audit-only: do not load
   or return the whole report; use permitted runner output or a human-provided
   sanitized excerpt. Mark unavailable details unknown.
4. Report what existing evidence establishes about interrupted-run cleanup and
   preservation of shared data; distinguish evidence from assumptions. Do not
   connect to or change the database during this intake. If state is uncertain,
   retain the R4 stop and identify the reconciliation needed.
5. Check availability/configuration of the prescribed Kilo → test-runner →
   pare-test route, or the documented OpenCode fallback, without starting tests.
   Read the local test-runner tool/config documentation before proposing exact
   arguments. Report blockers, not global configuration changes.

## Compact return format

```text
ROOT / HEAD / BRANCH:
DIRTY PATHS:
ACTIVE RELEVANT RUNNERS:
CANDIDATE STATUS + HASHES:
FIRST REDACTED EXCEPTION / SOURCE / LINE:
PRIOR INTERPRETER + ROOT + PLUGIN ARGS:
COLLECTION / TESTS / DB ACCESS / CLEANUP: known or unknown, with evidence
DATA-PRESERVATION / RECONCILIATION:
AVAILABLE APPROVED RUNNER ROUTE:
SANITIZED RELEVANT SOURCE DIFF (only if needed):
BLOCKERS:
```

## Boundaries and next phase

The shared target is already approved for ordinary real ASGI/DB test execution:
`localhost:5432/social_manager`, `test_schema`. Do not ask the owner to approve
that same target again. This is **not** approval to reset, drop, create, stamp,
migrate, reseed, run `conftest.py` bootstrap, restore backups, change permission
catalogs/guards, or perform live digest/provider sends. Uncertain prior state
still requires reconciliation, not a reset.

Once intake is reviewed and the state is safe, the remaining historical
integration scope is sequential and bootstrap-free:
`tests/test_web_digests_jobs.py`, `tests/test_agent_tool_registry.py`,
`tests/test_permissions.py`, and the reviewed membership matrix. The last two
require explicit preservation/cleanup checks; do not assume their fixtures are
harmless. No simultaneous pytest/writers. Use the exact agreed PR head,
worktree interpreter/root and positional paths through the documented runner.
Do not silently substitute the old worktree revision for the published head.

Do not repeat the assistant's 35 completed mocks, old plugin-only checks or
unrelated suites. No merge, Ready transition, or acceptance claim during intake.
Return the evidence so the coordinator can diagnose the actual failure and
prepare a bounded integration run without guessing.