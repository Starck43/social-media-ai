# T-01 Agent token budget and delegation guardrails

[Audit](../../audit/shared/t-01.md) · accepted `49d9c563` on 2026-11-26

Status: READY · OpenCode · 2026-11-26

## Why

The P22-T08 audit session (`01a216c9-aa05-7fc0-9dd1-b23ec24988e0`, 2026-11-26)
burned ~70% of a 5-hour GPT 6.2 Sol High limit in ~15 minutes: 16.4M input
tokens over 180 requests across six threads. Only 2% of that was the actual
verdict. The rest was implementation (35%), independent review (25%), DB
verification research (23%) and orchestration (16%).

Root causes: the prompt allowed "fix templates and styles along the way"; five
forked children re-sent 56–102k of inherited context 20–43 times each; Perch and
`opencode run` were spawned before their routes were known to work; the DB child
researched setup instead of following a recipe; no child had a budget cap.

## Scope

Process-only guardrails in the audit/task/test-runner/orchestrator skills,
`COLLABORATION#delegation` and the prompt examples. No product code.

## Acceptance criteria

1. Audit is verdict-only; a prompt that adds UI/style work is split into a
   separate `task` lane instead of being absorbed into the audit.
2. Every delegated child gets a compact self-contained handoff, not the parent
   transcript/forked history.
3. Third-party routes are preflighted once before spawning; no polling loops.
4. A child exceeding ~1M input tokens or ~20 turns is stopped and narrowed.
5. DB scenarios run from an exact command list on an isolated schema.
6. The incident and the corrected prompt are recorded in the process docs.

## Evidence

Edits in `.agents/skills/{audit,task,test-runner,orchestrator-task}/SKILL.md`,
`.agents/skills/{opencode-task,perch-task,remote-task}/SKILL.md`,
`docs/process/COLLABORATION.md#delegation`, `docs/process/PROMPT_EXAMPLES.md`
and `tests/AGENTS.md`; `AGENTS.md`/`README.md`/`ROADMAP.md` carry the lazy-read
and skill-precedence rules.

Checks: `git diff --check` clean. `python3 scripts/check_markdown_links.py`
reports 45 errors: 39 in `docs/archive` and 6 pre-existing in
`docs/tasks/p22-{t08,t09,t11,t18}.md`/`docs/audit/p20/p20-t06.md`; zero in any
file changed by this task. Documentation-only change; no pytest run.
