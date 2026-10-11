# Agent workflow

## Sources of truth
- Read AGENTS.md and .agent/rules.md for engineering and environment constraints.
- Use docs/PRODUCT_PLAN.md for product direction and docs/BUSINESS_PRODUCTION_READINESS.md for stable engineering and release gates. Read only task-relevant sections.
- Use docs/design/README.md as the only prioritized open-work queue and ownership board.
- Use docs/IMPLEMENTATION_STATUS.md as the compact index of delivered foundations. Use Git and the linked PR for exact revisions and original evidence. MERGED is not whole-gate acceptance.
- Treat .agent/tasks.md and .agent/state.json as historical/supporting records unless reconciled with fresh GitHub heads and the board. Never select their old "next" items blindly. Invalid JSON is unavailable state, not permission to discard or reconstruct another agent's records.
- Do not create a second backlog or additional competing handoff documents.

## Start or resume
1. Identify local or remote execution, branch, HEAD, base dev and dirty files. Read current instructions.
2. Locate the owner's selected task in the board; inspect its ownership row and current PR. If no task was selected, propose one bounded unreserved item rather than implementing the whole roadmap.
3. Read only the relevant readiness gate, subsystem contract and call sites. Do not reread unrelated merged packages.
4. Resume from the existing handoff or PR description. Verify recorded SHA against HEAD and preserve other agents' ownership.
5. Reserve the selected package in the board when useful. Work in `dev` for one sequential local task; create a branch/worktree only when isolation is needed. Never push `dev` without explicit authorization.

## Keep context small
- Use rg and targeted file ranges; summarize long output and retain only useful error traces.
- Keep stable rules in AGENTS.md, procedures here, ownership on the board and detailed original evidence in Git/PRs.
- Avoid copying architecture or whole logs into prompts/PRs. Read deeper documentation only when the selected task requires it.
- Reuse completed evidence at its exact revision. Repeat checks only for changed code, dependencies, failures or unresolved risk.
- One bounded task per session/PR. Do not add many speculative skills or MCP tools.

## Checkpoint and handoff
Update the current board/ledger and any existing PR description before ending,
switching sessions or handing off. When there is no PR, provide the compact block
in chat. Preserve relevant evidence and never invent a commit SHA for uncommitted work.

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
For a coherent validated patch in `dev`, propose the commit split/messages at
completion and offer to create the commits locally. A worktree or PR is not a
completion requirement unless isolation or publication is actually needed.
When the user uses a closeout phrase from `AGENTS.md`, create those local commits
without another confirmation after excluding unrelated/incomplete/unvalidated
changes. Report anything left uncommitted and never push `dev` implicitly.
