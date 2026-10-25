---
name: test-runner
description: "Run or explicitly author assigned tests; report exact results without changing unowned code."
---

# Test work

Read tests/AGENTS, the assigned task card and COLLABORATION Tests. Read Database
only for DB-backed work/setup/migrations. Verify expected HEAD and owned scope.
Record documentation actually consulted, command, tested SHA, exit code, counts,
failed names/short cause, unrun scope and data cleanup; return a compact verdict.

Run-only tester executes/analyzes the supplied command: no source edits or new
tests. Explicit authoring is separate. Codex's primary OpenCode build calls native
`tester` via V2 `subagent`, using the River model in opencode.jsonc unchanged.
Build owns preflight/preparation/cleanup. If unavailable use Kilo then a native
Codex child; other agents use native children. Do not bypass HEAD/permission/
ownership guards. No full suite without owner request. Apply AGENTS auditor
retest limits. Session housekeeping does not change a successful test verdict.
