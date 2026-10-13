# Collaboration Protocol

Handoff, delegation-launch, and task-file contract. Operational rules: `AGENTS.md`; DB/worktree constraints: `.agent/rules.md`.

## Closeout procedure (triggered by the phrases listed in AGENTS.md)

1. Record the actual state in the board/ledger/PR when it changed.
2. Commit each completed, validated, task-scoped part locally in `dev`; exclude unrelated/incomplete/unvalidated changes and list them in the handoff; an early stop is never converted into completion.
3. Offer a new chat with this copyable handoff (never claim to open/close chats automatically):

```text
Project: <absolute worktree>; integration branch: dev.
Read AGENTS.md, .agent/rules.md, docs/COLLABORATION.md.
Completed: <behavior/result>.
Branch/commit/PR: <references>; uncommitted changes: <summary or none>.
Validation: <checks done; TEST RUN summary block; pending checks>.
Next task: <specific next action, or wait for the owner>.
Constraints: preserve unrelated work; all tests through Kilo's test-orchestrator, test-runner child, and pare-test MCP; sequential shared test DB; no automatic push/merge/deploy/DB reset.
```

## Headless delegation launches (from a task worktree)

All pytest execution goes through Kilo's restricted `test-orchestrator`, which
can only delegate to the project `test-runner` child agent. That child can run
tests only through the connected `pare-test` MCP runner. Codex only coordinates
and consumes the compact final summary. External headless launch:

```sh
# Kilo CLI — test-orchestrator delegates to .kilo/agents/test-runner.md
kilo run --dir /absolute/task/worktree --agent test-orchestrator --format json \
  --file /absolute/task/worktree/.agent/test-task.md \
  "Delegate the attached test task to the project test-runner child using Kilo's task tool. Return only the child's TEST RUN summary."
```

- Kilo uses its configured `test-orchestrator` and `test-runner` agents, plus the connected `pare-test` MCP server. The child passes `<worktree>/.venv/bin/python` and the worktree path to MCP. Do not use `--pure` (it disables external plugins), `--auto`, or shared sessions. If the child or MCP runner is unavailable, report BLOCKED.
- The Codex `subagents` MCP (`codex-as-mcp`) creates Codex agents and is separate from this Kilo test route.
- No provider/model credentials are stored in the repository.

## Testing task-file contract (`.agent/test-task.md`)

Minimal fields: absolute worktree, branch, expected HEAD, test files, behavior under test (1–2 lines), pointer "follow AGENTS.md and .agent/rules.md".
Runner-side obligations (defined once in the agent definitions, not repeated per task): sequential runs; `pgrep -fl pytest` pre-check (exact command, no operators; exit 1 = free; shared test schema across worktrees ⇒ coordinator serializes); quiet pytest flags; `.agent/test-report.md` evidence (commands, exit codes, counts, failure excerpts, HEAD, dirty paths; no secrets); final `TEST RUN …` summary block to the coordinator.
Infrastructure blockers are not successful tests; do not run a broad suite to validate documentation edits.
