---
name: test-runner
description: "Use when assigned one exact test command or test-authoring scope; runs, parses and reports evidence without changing source."
---

# Test runner

Declare this skill at session start. Read AGENTS, the task card,
[test layout](../../../tests/AGENTS.md), and only the relevant COLLABORATION
test/DB sections. Confirm the expected revision and scope. Report command, tested
SHA, exit status, counts, failing names/cause, unrun checks and cleanup. Record
documentation actually read in the card/PR.

Run-only assignments execute and analyze the supplied command; do not edit source
or add tests. Test authoring requires explicit assignment.

## Budget and DB recipe

- Run the commands you were given. A DB scenario arrives as an ordered command
  list plus schema/reset/cleanup steps; do not research `initdb`,
  `setup_test_db.py`, `conftest` or schema inventory on your own. The P22-T08
  verification child spent 3.85M input tokens exploring setup and was killed by
  the limit without evidence.
- Stop at ~1M input tokens or ~20 turns and report the exact blocker instead of
  continuing to explore.
- Use an isolated schema, serialize shared test-schema use, and remove synthetic
  records before reporting.
- Do not spawn a reviewer or implementer; a test run reports evidence only.

## Route

Run one bounded test command through the `tester` child
(`cent.ai/hy4-flash:free`), then `free-pi`, then the executor's own model. The
tester is independent from the task executor and must receive the exact command,
revision and scope. Never use the audit model as a test runner. Return the
route/model actually used. A permission/HEAD/ownership failure is a blocked
route, not permission to bypass a guard.

## Evidence rules

- A pass is the command's own output on the named SHA: exit code and counts. A
  previous run, a different revision or "should pass" is not evidence.
- A regression test counts only after a red-green check: it fails on the
  pre-change revision and passes on the candidate.
- Mocks cover only external, paid or irreversible boundaries, never the module
  under test, and must assert the interaction. Never weaken an assertion, edit an
  expectation or reshape a fixture to reach PASS.
- Required DB/integration scenarios run on real PostgreSQL; a mocked or unit
  result never substitutes for them. Call mocked evidence mocked.
- Never bypass ownership, revision or DB guards. Serialize shared test-schema use
  and remove/verify all synthetic records after the run. No full suite without an
  owner request. Session cleanup is housekeeping and does not alter the verdict.
