# Collaboration Protocol

Handoff, delegation-launch, and task-file contract. Operational rules: `AGENTS.md`; DB/worktree constraints: `.agent/rules.md`.

## Audits, Goals and Codex subagents

Use Codex subagents for independent read-heavy audit lanes and bounded
implementation tasks. They reduce noise in the coordinating thread, but add
total model work; reserve them for parallelizable tasks. Give each agent one
question or task, a file/claim boundary, relevant SHA, expected evidence and a
compact response format. The coordinator owns audit synthesis, task acceptance,
integration and final verification.

For an audit execution, populate root ROADMAP.md only after findings are
deduplicated and accepted. Clear its task rows between executions;
docs/design/README.md remains the only persistent product queue. Record task
ID, acceptance check, dependencies, exclusive write ownership, status and
verification. Independent write tasks get separate Codex subagents in separate
worktrees/branches.
Integrate one finished task at a time, then update its checkbox and make a local
commit with matching evidence. Never mark a task done because an agent merely
reports completion; inspect the diff and verification at the exact tested SHA.
Commit only task-scoped work. After integration, clear completed rows and
transfer unresolved product work to the canonical board.

Start /goal only when the audit/implementation has a measurable finish line.
Point it at the active ROADMAP.md task list; include verification, constraints
and a blocked stop condition. Goals persist within a thread and do not replace the
checked-in task ledger. Codex-generated memories are opportunistic recall, not
the source of project policy or current task state; do not manually maintain
them for workflow continuity.

Standing defaults for every prompt: the product board (`docs/design/README.md`)
is append-only — rows only change status across executions, evidence stays;
only `ROADMAP.md` ledger rows are cleared when an execution closes, and board
rows are deleted only on an explicit owner command. PR evidence is scoped to
PRs named in the selected rows (`view` without full diffs); open a full diff
only for a file being edited.

Useful OpenAI guidance: [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Codex memories](https://learn.chatgpt.com/docs/customization/memories),
[Using Goals in Codex](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex).

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
kilo run "Delegate the attached test task to test-runner. Return only its TEST RUN summary." \
  --dir /absolute/task/worktree --agent test-orchestrator --format json \
  --file /absolute/task/worktree/.agent/test-task.md
```

- For Kilo 7.8.8, supply the required message **before the flags**. `--file` is an array option: text after it is treated as another file; omitting the message also fails.
- Agent configuration comes from the checkout selected by `--dir`. The orchestrator must be permitted to read the attached task file; moving it inside the worktree does not overcome `read: deny`. External task files additionally require permitted external-directory access. Fix the reviewed configuration/placement or return BLOCKED; do not read through an IDE/shell tool to bypass a denial.
- Kilo uses its configured `test-orchestrator` and `test-runner` agents, plus the connected `pare-test` MCP server. The child passes `<worktree>/.venv/bin/python` and the worktree path to MCP. Do not use `--pure` (it disables external plugins), `--auto`, or shared sessions. If the child or MCP runner is unavailable, report BLOCKED.
- The Codex `subagents` MCP (`codex-as-mcp`) creates Codex agents and is separate from this Kilo test route.
- Fallback (Kilo BLOCKED only, never parallel with Kilo — R8 sequential): Codex subagent `.codex/agents/opencode-test-runner.toml` delegates via `opencode run --dir <worktree> -m kilo/stepfun/step-5-preview-free --auto --file <worktree>/.agent/test-task.md` with the same guards (`git rev-parse HEAD`, `pgrep -fl pytest`), same pytest flags, and same `TEST RUN …` summary contract. The subagent may write only `.agent/test-report.md`.
- No provider/model credentials are stored in the repository.

## Testing task-file contract (`.agent/test-task.md`)

Minimal fields: absolute worktree, branch, expected HEAD, test files, behavior under test (1–2 lines), pointer "follow AGENTS.md and .agent/rules.md".
Runner-side obligations (defined once in the agent definitions, not repeated per task): sequential runs; `pgrep -fl pytest` pre-check (exact command, no operators; exit 1 = free; shared test schema across worktrees ⇒ coordinator serializes); quiet pytest flags; `.agent/test-report.md` evidence (commands, exit codes, counts, failure excerpts, HEAD, dirty paths; no secrets); final `TEST RUN …` summary block to the coordinator.
Infrastructure blockers are not successful tests; do not run a broad suite to validate documentation edits.

## Test bootstrap compatibility

`scripts/setup_test_db.py` inspects an existing target in a read-only catalog
transaction before create/reset/seed. A live table absent from the branch's ORM
metadata (other than Alembic's version table), missing columns on existing tables,
or UNIQUE drift stops the run; bootstrap never adds or drops UNIQUE constraints
to reconcile an existing table. Missing model tables retain the fresh-table path.
Schema reconciliation/reset still needs separate owner approval, never a retry
or weaker baseline to obtain PASS. `--check` remains offline target diagnostics,
not this database preflight. These checks are not a shared-writer lock: R8/R10
coordination and any existing evidence-preservation pauses remain in force.
