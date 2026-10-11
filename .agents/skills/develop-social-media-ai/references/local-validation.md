# Local validation handoff

Use this template in chat or the PR description. Replace every placeholder with observed information; omit unavailable commands rather than inventing them.

## Assignment
- Repository: Starck43/social-media-ai
- PR: <URL>
- Branch: <task branch>
- Expected head SHA: <full SHA>
- Base dev SHA: <full SHA>
- Change and acceptance criteria: <observable behavior>

## Checkout
Preserve local work. Fetch the task branch into a clean worktree or checkout. Verify git rev-parse HEAD matches the expected SHA before testing. Stop and report a mismatch.

## Validation
- Cloud checks actually run: <command, outcome, revision>
- Checks deferred and reason: <check, blocker>
- Local commands: <commands confirmed against current repository>
- Required environment: <Python/dependencies, isolated test database/schema, mocked integrations; no secret values>
- Relevant scenarios: <regression, tenant boundaries, error cases, migration cases as applicable>

Run the requested larger suite against an isolated test target. Do not deploy, migrate production, send real messages or incur paid API calls as part of validation unless specifically requested.

## Return report
- Tested SHA and local changes, if any.
- Exact commands and exit status.
- Passed/failed/skipped counts where available.
- Failure traces with secrets and personal data redacted.
- Environment blockers and untested scenarios.
- Proposed fixes; identify their branch and SHA separately.
- Readiness recommendation; do not merge.

Any new commit invalidates the assumption that the previous report covers the full current change. Recheck affected tests and identify what must be rerun.
