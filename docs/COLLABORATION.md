# Collaboration Protocol

Handoff, delegation-launch, and task-file contract. Roles and operational
rules: [AGENTS.md](../AGENTS.md); environment/DB/test-delegation constraints:
[docs/RULES.md](RULES.md); task registry: [ROADMAP.md](../ROADMAP.md) +
[docs/phases/](phases/README.md).

## Start or resume

1. Identify local or remote execution, branch, HEAD, base dev and dirty files.
2. Locate the selected task in ROADMAP.md / its phase file; if none was
   selected, propose one bounded unclaimed item rather than the whole board.
3. Read only the relevant phase file, readiness gate, subsystem contract and
   call sites; do not reread unrelated merged packages.
4. Resume from the existing handoff or PR description; verify the recorded SHA
   against HEAD and preserve other agents' ownership.
5. Work in `dev` for one sequential local task; branch/worktree only when
   isolation is needed (RULES.md R6/R7). Never push `dev` without explicit
   authorization.

## Audits, Goals and subagents

Follow routine and test routing in [AGENTS.md](../AGENTS.md) and
[docs/RULES.md](RULES.md) (R10). Codex owns architecture, review and acceptance;
preserve HEAD, permission and ownership blockers.

Use subagents for independent read-heavy audit lanes and bounded implementation
tasks; reserve them for parallelizable work. Give each agent one question or
task, a file/claim boundary, relevant SHA, expected evidence and a compact
response format (verdict + refs, no logs). The coordinator owns audit
synthesis, task acceptance, integration and final verification.

Audit findings become tasks only after deduplication and acceptance: add them
to the matching phase file (`P<n>-T<nn>` rows with acceptance, dependencies,
exclusive ownership) and mirror ID+link rows in ROADMAP.md. Independent write
tasks get separate agents in separate worktrees/branches. Integrate one
finished task at a time, then update its status and commit with matching
evidence. Never mark a task DONE because an agent merely reports completion;
inspect the diff and verification at the exact tested SHA. After integration,
delete the ROADMAP row; phase closure requires Codex approval in the phase file.

Start /goal only when the work has a measurable finish line; point it at the
active ROADMAP.md rows. Goals persist within a thread and do not replace the
checked-in registry. Codex-generated memories are opportunistic recall, not
the source of project policy or current task state.

PR evidence is scoped to PRs named in the selected rows (`view` without full
diffs); open a full diff only for a file being edited.

## Closeout procedure (triggered by the phrases listed in AGENTS.md)

1. Record the actual state in ROADMAP.md, the phase files and the PR when it changed.
2. Commit each completed, validated, task-scoped part locally in `dev`; exclude unrelated/incomplete/unvalidated changes and list them in the handoff; an early stop is never converted into completion.
3. Offer a new chat with this copyable handoff (never claim to open/close chats automatically):

```text
Project: <absolute worktree>; integration branch: dev.
Read AGENTS.md, docs/RULES.md, docs/COLLABORATION.md.
Completed: <behavior/result>.
Branch/commit/PR: <references>; uncommitted changes: <summary or none>.
Validation: <checks done; TEST RUN summary block; pending checks>.
Next task: <ROADMAP row ID, or wait for the owner>.
Constraints: preserve unrelated work; pytest only via the primary OpenCode
test-runner (R10); sequential shared test DB (R8); no automatic
push/merge/deploy/DB reset.
```

## Headless delegation launches (from a task worktree)

Primary test route (R10): Codex subagent `.codex/agents/test-runner.toml` delegates
to OpenCode:

```sh
opencode run --dir /absolute/task/worktree -m QwenPlan/deepseek-v4-flash-0731 \
  --auto "Выполни тестовое задание из прикреплённого файла и верни итоговый блок TEST RUN." \
  --file /absolute/task/worktree/.agents/tmp/test-task.md
```

Secondary route (only when the primary is BLOCKED; never parallel with it —
R8 sequential): Codex subagent `.codex/agents/kilo-test-runner.toml` starts
Kilo's restricted `test-orchestrator`, which delegates to the project
`test-runner` child running tests only through the `pare-test` MCP:

```sh
kilo run "Передай задачу из файла дочернему агенту test-runner и верни только итоговый блок TEST RUN." \
  --dir /absolute/task/worktree --agent test-orchestrator --format json \
  --file /absolute/task/worktree/.agents/tmp/test-task.md
```

- For Kilo 7.8.8, supply the required message **before the flags**. `--file` is an array option: text after it is treated as another file.
- Agent configuration comes from the checkout selected by `--dir`. The orchestrator must be permitted to read the attached task file; moving it inside the worktree does not overcome `read: deny`. Fix the reviewed configuration/placement or return BLOCKED; do not bypass a denial through an IDE/shell tool.
- Do not use `--pure` (disables external plugins), `--auto`, or shared sessions with Kilo. If the child or MCP runner is unavailable, report BLOCKED.
- The Codex `subagents` MCP (`codex-as-mcp`) creates Codex agents and is separate from these test routes.
- No provider/model credentials are stored in the repository.

## Testing task-file contract (`.agents/tmp/test-task.md`)

Minimal fields: absolute worktree, branch, expected HEAD, test files, behavior
under test (1–2 lines), pointer "follow AGENTS.md and docs/RULES.md".
Runner-side obligations (defined once in the agent/skill definitions, not
repeated per task): sequential runs; `pgrep -fl pytest` pre-check (exact
command, no operators; exit 1 = free; shared test schema across worktrees ⇒
coordinator serializes); quiet pytest flags; `.agents/tmp/test-report.md`
evidence (commands, exit codes, counts, failure excerpts, HEAD, dirty paths;
no secrets); final `TEST RUN …` summary block to the coordinator.
Infrastructure blockers are not successful tests; do not run a broad suite to
validate documentation edits.

## Test bootstrap compatibility

`scripts/setup_test_db.py` inspects an existing target in a read-only catalog
transaction before create/reset/seed. A live table absent from the branch's ORM
metadata (other than Alembic's version table), missing columns on existing
tables, or UNIQUE drift stops the run; bootstrap never adds or drops UNIQUE
constraints to reconcile an existing table. Missing model tables retain the
fresh-table path. Schema reconciliation/reset still needs separate owner
approval, never a retry or weaker baseline to obtain PASS. `--check` remains
offline target diagnostics, not this database preflight. These checks are not a
shared-writer lock: R8/R10 coordination and any existing evidence-preservation
pauses remain in force.
