# Agent workflow

## Sources of truth
- Read AGENTS.md and .agent/rules.md for engineering and environment constraints.
- Use docs/SOCIAL_MEDIA_AI_AUDIT_AND_ROADMAP.md as the primary strategic entry point. Read its headings and task-relevant sections; do not reload the entire audit on every turn.
- Use docs/design/README.md as the only prioritized open-work queue and ownership board.
- Use docs/IMPLEMENTATION_STATUS.md for delivered revisions and original evidence. MERGED is not whole-gate acceptance.
- Treat .agent/tasks.md and .agent/state.json as historical/supporting records unless reconciled with fresh GitHub heads and the board. Never select their old "next" items blindly. Invalid JSON is unavailable state, not permission to discard or reconstruct another agent's records.
- Do not create a second backlog or additional competing handoff documents.

## Start or resume
1. Identify local or remote execution, branch, HEAD, base dev and dirty files. Read current instructions.
2. Locate the owner's selected task in the board; inspect its ownership row and current PR. If no task was selected, propose one bounded unreserved item rather than implementing the whole roadmap.
3. Read only that task's contract, relevant audit section and call sites. Do not reread unrelated merged packages.
4. Resume from the existing PR description's current handoff. Verify recorded SHA against HEAD and preserve other agents' ownership.
5. If needed, reserve the selected package in the existing board, on the task branch; do not edit another active lane or push directly to dev.

## Keep context small
- Use rg and targeted file ranges; summarize long output and retain only useful error traces.
- Keep stable rules in AGENTS.md, procedures here, ownership on the board and detailed original evidence in the ledger.
- Avoid copying architecture or whole logs into prompts/PRs. Read deeper documentation only when the selected task requires it.
- Reuse completed evidence at its exact revision. Repeat checks only for changed code, dependencies, failures or unresolved risk.
- One bounded task per session/PR. Do not add many speculative skills or MCP tools.

## Checkpoint and handoff
Update the existing PR description before ending, switching sessions or handing off. Before a PR exists, provide the same compact block in chat and carry it into the PR when created. Preserve the original description/evidence; replace only the current continuation block. Never invent a commit SHA for uncommitted work.

### Current handoff
- Task/gate and acceptance criteria:
- Execution mode and owner:
- Branch / base dev SHA / implementation SHA:
- Uncommitted files or patch, if any:
- Completed behavior and relevant paths:
- Decisions and unresolved questions:
- Checks: exact command, tested SHA, result, attribution (executed here / owner-reported / deferred):
- Blockers and required local checks:
- Next concrete action:

Keep this block under about 500 words; link to existing contracts/evidence. The implementation SHA is the revision tested, not necessarily the later documentation-only commit.

## Completion
Update the existing ownership row and implementation ledger when integration/evidence changes. Distinguish IN_PROGRESS, BLOCKED, READY (checks complete), MERGED and gate ACCEPTED. Verify actual merge before releasing reservation; never close a whole gate from one merged slice.
Follow current owner instructions and .agent/rules.md for Ready/merge authority. Do not deploy, reset/migrate schemas or activate live actions from routine development authorization.
