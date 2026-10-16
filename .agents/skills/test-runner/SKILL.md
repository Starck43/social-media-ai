---
name: test-runner
description: OpenCode's contract as the primary pytest executor for social-media-ai — invoked by Codex/Kilo coordinators via `opencode run --file <task-file>` or directly by the owner. Use for any delegated TEST RUN task.
---

# OpenCode test-runner contract

Follow routing and safeguards in AGENTS.md and docs/RULES.md (R8/R10). Task files and evidence: `.agents/tmp/`.

## Test execution (task file attached)

1. Verify: `git rev-parse HEAD` == expected HEAD; `pgrep -fl pytest` (exact
   command, no operators; exit 1 = free). Mismatch or an active/unknown pytest
   process ⇒ reply `TEST RUN BLOCKED reason=<one line>` and stop.
2. Run only the assigned scope: `<worktree>/.venv/bin/python -m pytest <paths>
   --noconftest -o addopts= -q --tb=short --no-header` (paths positional;
   collect-only first after enum/import changes or when the task asks).
   Shared-DB scopes strictly sequential; no-DB scopes may parallelize only if
   the task file proves independence.
3. Never: reset/drop/create/stamp/migrate/seed schemas, call live providers,
   senders, workers, listeners, deploy, touch Git state or unrelated files.
4. Routine fixes allowed only inside file ownership explicitly assigned in the
   task (test fixtures/assertions, mechanical stale names/imports that do not
   change application behavior). Preserve and strengthen the contract; never
   weaken tests to pass. Ambiguous failure or needed behavior change ⇒ report
   to the parent, do not decide alone.
5. Evidence → `.agents/tmp/test-report.md`: tested commit, exact commands,
   exit codes, passed/failed/skipped, concise failure excerpts, dirty paths,
   blocked work. No secrets, no environment dumps.
6. Final reply (≤120 tokens, nothing else):

   ```
   TEST RUN <PASS|FAIL|BLOCKED> commit=<short-hash> passed=<n> failed=<n> skipped=<n>
   - <test_id>: <one-line failure reason>   (only when FAIL)
   ```

## Routing boundary

Routine work uses `routine-worker`; this agent runs pytest.
