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
source or add tests. Test authoring requires explicit assignment. Codex routes
to configured OpenCode `build`/V2 `tester` first, then the [Perch route](../perch-task/SKILL.md),
then a native child if unavailable. Other agents use native children only. Never bypass ownership,
revision or DB guards. Serialize shared test-schema use and remove/verify all
synthetic records after the run. No full suite without owner request. Session
cleanup is housekeeping and does not alter the test verdict.
