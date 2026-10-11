# Local collaboration

The owner develops locally, with `dev` as the integration branch. Agents complete
authorized work independently and keep result reports short. The durable workflow
is in `AGENTS.md`; shared database restrictions are in `.agent/rules.md`.

## Branches and pull requests

Inspect `git status` and `git worktree list` first. Reuse a suitable clean worktree
or create a task branch from local `dev` in a writable location. Preserve existing
branches and unrelated edits. Substantial isolated changes should have a PR into
`dev` when access permits. Do not automatically merge or deploy.

## Testing delegation

OpenCode was found at `/usr/local/bin/opencode`. Its installed `run --help`
supports `--dir`, `--file`, `--format json`, and `--model`. Kilo was not found in
PATH; do not assume an installed IDE extension is callable from the terminal.
No provider/model settings or credentials were changed by this setup.

For a broad check, write a task file under `.agent/` (ignored unless explicitly
tracked), then launch OpenCode from the task worktree:

```sh
opencode run --dir /absolute/task/worktree --format json \
  --file /absolute/task/worktree/.agent/test-task.md \
  "Execute the attached testing task and write the requested report."
```

Use the configured model by default; choose a cheaper configured model only when
its availability is known. Do not enable `--auto` or share sessions. If a launch
requires sandbox escalation, use the normal approval mechanism rather than
bypassing it. Local CLI help is the source of truth for installed flags;
[OpenCode CLI documentation](https://dev.opencode.ai/v2/docs/cli/commands/)
also describes noninteractive runs and file attachments.

Each testing task must specify:

- Absolute worktree, branch, expected commit, changed behavior, and test scope.
- Read `AGENTS.md` and `.agent/rules.md`; use the project `.venv`.
- Run tests only, without application edits, Git changes, publication, or DB
  setup/reset/drop/stamp/migration operations.
- Check for active pytest processes before starting. All existing worktrees
  share the test schema: tests must run sequentially. If another runner is active,
  report the conflict and do not start. The coordinating agent must serialize runs.
- Report exact commands, exit codes, passed/failed/skipped counts, failure excerpts,
  environment blockers, and any incomplete/interrupted runs to `.agent/test-report.md`.
  Never include secrets or full environment dumps.

The coordinating agent reads the report, checks that the tested commit matches,
and reports completed validation accurately. Infrastructure blockers are not a
successful test. Do not run a broad suite simply to validate documentation edits.

## Closing a substantial task

Provide a concise result and offer a new chat with this handoff filled in:

```text
Project: <absolute worktree>; integration branch: dev.
Read AGENTS.md, .agent/rules.md, and docs/COLLABORATION.md.
Completed: <behavior/result>.
Branch/commit/PR: <references>; uncommitted changes: <summary or none>.
Validation: <completed checks; delegated report path; pending checks>.
Next task: <specific next action, or wait for the owner>.
Constraints: preserve unrelated work; broad tests via local OpenCode/Kilo,
sequential shared test DB; no automatic merge/deployment/DB reset.
```

Historical `.agent/tasks.md` and `.agent/state.json` may describe old review queues;
verify current Git state before acting on them. This setup does not resume those queues.
