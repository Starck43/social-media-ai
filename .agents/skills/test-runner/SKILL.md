---
name: test-runner
description: "Run or explicitly author assigned tests and report exact results."
---

# Test runner

Declare this skill at session start. Read AGENTS, the task card, [test layout](../../../tests/AGENTS.md),
and only the relevant COLLABORATION test/DB rules. Confirm expected revision and
scope. Report command, tested SHA, exit status, counts, failing names/cause,
unrun checks and cleanup. Record documentation actually read in the card/PR.

Run-only assignments execute and analyze the supplied command; do not edit
source or add tests. Test authoring requires explicit assignment. Route one
bounded test run through the configured `tester` subagent on
`cent.ai/hy4-flash:free`. The tester is independent from the task executor and
must receive the exact command, revision and scope. If the tester is
unavailable, the executor's own model is the sole test fallback; never use the
audit model as a test runner. Return the route/model actually used.

Never bypass ownership, revision or DB guards. Serialize shared test-schema use
and remove/verify all synthetic records after the run. No full suite without
owner request. Session cleanup is housekeeping and does not alter the test
verdict.
