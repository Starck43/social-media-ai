# Local collaboration

The owner develops locally, with `dev` as the integration branch. Agents complete
authorized work independently and keep result reports short. The durable workflow
is in `AGENTS.md`; shared database restrictions are in `.agent/rules.md`.

## Branches and pull requests

Inspect `git status` and `git worktree list` first. For one sequential local task,
work directly in `dev` and keep its diff coherent. Preserve unrelated edits.
Create a branch/worktree for parallel work, PR review, risky experiments,
conflicts, or an explicit owner request. At completion, show the proposed commit
split/messages and offer to commit the validated task directly in `dev`. A PR is
optional for ordinary local work. Never push `dev`, merge or deploy automatically.

## Testing delegation

OpenCode is available at `/usr/local/bin/opencode`. Kilo CLI 7.8.8 is available
as `kilo`; its local `test-runner` agent is stored under the ignored
`.kilo/agents/` directory. No provider/model credentials are stored in the
repository or changed by this setup.

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

Kilo uses the same task/report files and a permission-restricted project agent:

```sh
kilo run "Execute the attached testing task and write .agent/test-report.md." \
  --pure --dir /absolute/task/worktree --agent test-runner --format json \
  --file /absolute/task/worktree/.agent/test-task.md
```

Choose a configured Kilo model with `--model provider/model` only when the owner
or coordinator names it. Kilo's `mcp` command connects tools to Kilo; it does not
expose Kilo models to Codex. Delegation therefore uses the headless CLI directly.

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

The phrases `закрываю чат`, `чат закрываем`, `закрываем чат`, `завершаем чат`,
`задача завершена`, `задача с подзадачами завершена`, or
`подготовь новый чат` request this closeout even when the task stops early. These
phrases authorize local `dev` commits for completed, validated and task-scoped
changes without another question. Exclude unrelated, incomplete or unvalidated
changes, record them in the handoff, and do not convert an early stop into completion.

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
