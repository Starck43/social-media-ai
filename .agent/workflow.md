# Agent workflow

## Sources of truth
- Read AGENTS.md and .agent/rules.md for engineering and environment constraints.
- Use docs/PRODUCT_PLAN.md for product direction and docs/BUSINESS_PRODUCTION_READINESS.md for stable engineering and release gates. Read only task-relevant sections.
- Use docs/design/README.md as the only persistent product queue and ownership board. Root ROADMAP.md is a per-audit execution ledger; keep its task section empty between audits.
- Use docs/IMPLEMENTATION_STATUS.md as the compact index of delivered foundations. Use Git and the linked PR for exact revisions and original evidence. MERGED is not whole-gate acceptance.
- Treat .agent/state.json as historical/supporting state unless reconciled with fresh GitHub heads and the board. Never select old "next" items blindly. Invalid JSON is unavailable state, not permission to discard or reconstruct another agent's records.

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

## Audit and parallel execution

For a requested audit of completed work, first verify branch, HEAD, PR heads and
the task board. Split read-only coverage by independent subsystem or claim family.
Ask each Codex subagent for evidence-backed findings, file/PR/SHA references,
risk and an executable acceptance condition. The parent deduplicates findings,
checks important claims against current code, and writes only accepted bounded
work items to the task section of root ROADMAP.md.

Each item needs an ID, outcome/acceptance check, dependencies, exclusive file
ownership and status (TODO, IN_PROGRESS, BLOCKED, DONE, DROPPED). Mark parallel
items and file boundaries so agents do not own the same write lane. Use the
smallest useful number of subagents (often two or three); skip delegation when
coordination costs exceed the parallel/context benefit. Assign one task per
Codex subagent, each in a separate worktree/branch; request a compact summary
and changed paths, not investigation traces. Parallelize only independent
lanes. The parent integrates and reviews evidence, updates the ledger, and
commits completed items one at a time. Run dependent work in dependency order.
Mark a checkbox [x] only after acceptance evidence is verified. Pytest uses the
primary OpenCode route under .agent/rules.md R10; proven independent no-DB scopes
may run in parallel, while shared-PostgreSQL checks remain sequential under R8.
The coordinator reviews and stages the scoped diff; OpenCode performs every
local `git commit`.

Start or resume a long-running implementation with /goal pointing at the
specific ROADMAP.md and naming completion evidence and constraints. /goal is
thread-scoped continuation state, not a repository task tracker. Do not ask
subagents to edit generated Codex memories; store durable policy here, product
priority on the board, and exact evidence in Git/PRs.

## Checkpoint and handoff
Update the current board, active ROADMAP task rows and any existing PR
description before ending, switching sessions or handing off. When there is no
PR, provide the compact block in chat. Preserve relevant evidence and never
invent a commit SHA for uncommitted work.

Use the compact handoff template in docs/COLLABORATION.md; keep it under about
500 words, link existing evidence, and record the tested implementation SHA.

## Completion
Update the existing ownership row and implementation ledger when integration/evidence changes. Distinguish IN_PROGRESS, BLOCKED, READY (checks complete), MERGED and gate ACCEPTED. Verify actual merge before releasing reservation; never close a whole gate from one merged slice.
Follow current owner instructions and .agent/rules.md for Ready/merge authority. Do not deploy, reset/migrate schemas or activate live actions from routine development authorization.
For a coherent validated patch in `dev`, propose the commit split/messages at
completion and offer to create the commits locally. A worktree or PR is not a
completion requirement unless isolation or publication is actually needed.
When the user uses a closeout phrase from `AGENTS.md`, create those local commits
without another confirmation after excluding unrelated/incomplete/unvalidated
changes. Report anything left uncommitted and never push `dev` implicitly.
