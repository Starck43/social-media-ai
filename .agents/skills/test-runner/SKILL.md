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
bounded test run in the order defined by `task`: the [Perch route](../perch-task/SKILL.md)
(`perch run --json`; only Codex may invoke third-party agents), then the `free-pi`
subagent (`free-pi/deepseek-v4.1-flash#medium`), then the configured OpenCode
`build`/V2 `tester` on `river/deepseek-ai/DeepSeek-V4.1-Flash-1017` (do not
override that model, and never select `tester` as the parent). Fall to the next
route only when the previous one is unavailable or fails, and report which route
ran. Never bypass ownership, revision or DB guards. Serialize shared test-schema
use and remove/verify all synthetic records after the run. No full suite without
owner request. Session cleanup is housekeeping and does not alter the test verdict.
