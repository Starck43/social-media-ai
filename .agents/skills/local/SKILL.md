---
name: local
description: Run any local social-media-ai task — direct prompt, planned launch or /goal — through one path: the orchestrator triages and owns plan/review/integration, delegates read-heavy or parallel lanes to subagents, and routes pytest through the primary OpenCode test-runner. Use in a local IDE/CLI checkout.
---

# Local Social Media AI

The main thread keeps plan, decisions, acceptance and integration; noisy work goes to subagents or the delegated test runner. Detail lives in AGENTS.md, docs/RULES.md (R6/R8/R10) and docs/COLLABORATION.md — read only the section the current step needs.

## Kernel — the same for every entry

1. **Triage:** `git status`, `git worktree list`, open rows in ROADMAP.md and the selected docs/phases/ file; read only task-relevant slices.
2. **Route each unit of work:**
   - self — bounded edit under existing manager/service/session conventions;
   - subagent — read-heavy exploration or an independent task with exclusive files: one task per agent, smallest useful number (often 2–3); reply contract: verdict + file/line refs + changed paths, ≤150 tokens, no logs or file dumps;
   - OpenCode — executor, committer and primary route for every pytest run (R10 `test-runner`, exact QwenPlan model; contract in .agents/skills/test-runner/). Proven independent no-DB scopes may run in parallel; shared PostgreSQL checks stay sequential under R8. Kilo's test-orchestrator → test-runner route is secondary when OpenCode is BLOCKED. Coordinating Codex never runs pytest.
   Skip delegation when its coordination cost exceeds the context it saves.
3. **Accept:** verify evidence at the exact SHA/diff; integrate one finished task at a time, never on a bare completion report.
4. **Close:** update the phase file (status/evidence), delete the resolved ROADMAP.md row, handoff per docs/COLLABORATION.md, propose the commit split; AGENTS.md closeout phrases authorize commits. Phase closure needs Codex approval.

## Entries — front half differs, kernel is identical

- **Direct prompt** → start at step 1; no planning phase, subagents only if step 2 selects them.
- **Planned launch** → the plan already fixed scope, ownership and acceptance; run the kernel, do not re-plan.
- **/goal** → measurable finish line; accepted rows with acceptance checks in ROADMAP.md per docs/COLLABORATION.md; loop the kernel per row, checkpoint before stopping, blocked ⇒ stop.

## Token rules

- Lazy reads (rg, line ranges); never copy architecture, whole logs or raw test output into the main thread.
- One task list only: ROADMAP.md rows + their docs/phases/ files; never copy task text between them.
- Compact by default: subagent answers and delegated TEST RUN blocks are summaries, evidence stays in files/PRs.

## Standing defaults

- Phase files keep task history (statuses change, evidence stays); ROADMAP.md holds only open rows and is cleaned on resolution. Deleting DONE phase rows requires an explicit owner command.
- Scope PR evidence to PRs named in the selected rows: `view` without full diffs; open the diff only for the file being edited.
