# AGENTS.md — AI Assistant

## Agent entry points and continuity
- Before work read `.agent/rules.md` (worktree/DB constraints) and `.agent/workflow.md` (session handoff); instructions here supersede older procedural rules on conflict.
- Docs: `docs/PRODUCT_PLAN.md` (product), `docs/BUSINESS_PRODUCTION_READINESS.md` (gates), `docs/design/README.md` (tasks/ownership), `docs/IMPLEMENTATION_STATUS.md` (delivered), `docs/DOCS_INDEX.md` (index).
- Subagents: `.codex/agents/` (Codex), `.kilo/agents/` (Kilo). `.agent/test-report.md` is human-audit evidence only — never main-context input.
- Local IDE/CLI: `$develop-social-media-ai-local`; remote GitHub: `$develop-social-media-ai`; same task board and PR handoff.
- Resume from the current handoff after verifying its SHA. Git and PRs hold detailed history; generated Codex memories are optional recall, never project policy or task truth.

## Collaboration workflow
- `dev` is the integration branch. Complete authorized tasks autonomously, without routine confirmations.
- Default: work in the current `dev` checkout when one sequential task owns its changes; inspect status first, preserve unrelated edits. Separate branch/worktree only for parallel tasks, PR review, risky experiments, conflicts, or an explicit owner request — never as routine ceremony.
- Delegate only when requested or when independent lanes materially help. One
  concrete task, a short review, and dependent steps stay in the current agent.
  For a broad audit, use the fewest Codex subagents needed, give each one
  bounded area, and ask for concise findings. Implementation lanes need
  separate worktrees and file ownership. Shared-DB tests always stay sequential.
- On completion in `dev`: follow commit and closeout rules in `docs/COLLABORATION.md`. No PR for ordinary sequential work. Never push `dev`, merge, force-push, deploy, or touch production data without explicit authorization.
- Tests: every pytest run follows `.agent/rules.md` R10 and is delegated through Kilo. Neither Codex coordinator nor Codex subagents run pytest directly.

## Understand common requests
- “Audit completed work”, “check what is done”, or “find gaps in completed
  tasks” means compare `docs/IMPLEMENTATION_STATUS.md` with current code and PR
  evidence. Report verified findings and a prioritized fix plan; do not change
  code unless asked to fix or implement.
- For one named feature, task, or PR, inspect only that slice. Do not launch
  subagents unless the review has independent parts or the user asks for them.
- “Take the next active task” means select one unreserved item from
  `docs/design/README.md`, verify current code and ownership, then implement
  that item. It does not mean execute the whole board.
- “Implement/fix the audit findings” means record accepted items in root
  `ROADMAP.md`, then implement dependency-ready items with subagents only
  when useful. Verify each before marking it done; make per-item commits when
  the request or active Goal asks for them.
- Run focused checks appropriate to changed behavior. Request broad suites only
  when warranted; all pytest commands go through Kilo. Docs-only edits do not
  need pytest.

## Project rules
- Extensible assistant on the existing modular monolith: read `README.md`, `docs/DOCS_INDEX.md`, then relevant `docs/*.md` before changing behavior.
- Verify current branch/code before acting on plans or boards; docs may describe proposed work — never assume a planned feature exists.
- Active runtime (PostgreSQL schedules/jobs, worker, Telegram/MAX long-poll listener) is one process: `.venv/bin/python -m app.runtime`; do not start duplicate owners. No Celery/Redis in the active path; `app/celery/` and Streamlit are frozen without an explicit task.

## Data access and migrations
- Reuse ORM models, managers (`app/models/managers/`), service methods before adding DB access; extend a manager/service instead of duplicating queries across API/CLI/web/agent tools.
- No ad-hoc `engine`/`Session`/`AsyncSession`/`sessionmaker`; follow existing session and transaction conventions; respect workspace scoping, authorization, validation, manager-level rules; `settings.DB_SCHEMA` only, never hardcoded schemas.
- Alembic is the only migration path: no runtime table creation, no edits to applied migrations; review generated migrations; `alembic check` when warranted.

## Architecture and safety
- Small changes consistent with nearby code; reuse over new abstractions/dependencies/frameworks; no repo-wide formatting or unrelated refactors.
- Distinct concepts: `AgentScenario` = methodology; `AgentTask` = scheduled execution (sources/targets); multi-step business workflow = separate concept.
- Interactive access fails closed (unknown identity, missing membership/permission, revoked ⇒ deny); agent tools need explicit permissions and argument validation, re-checked at execution.
- External writes (send/publish/update/delete) are never a side effect of unrelated work; preserve dry-run defaults and approval gates; scope recipients/credentials to the owning workspace; never expose tokens, sessions, `.env` values, secrets in logs/tests/prompts/commits.
- Existing LLM clients and DB-managed provider/model config only; preserve usage/cost tracking and fallbacks; tests mock external APIs/LLMs — no real paid or destructive calls.
- Optional integrations must not block startup when unconfigured; per-source access limits stay explicit; a delivery channel does not imply content collection.

## Style and closeout triggers
- `pyproject.toml`: Black + isort, line 120; format only changed/new files; preserve legacy style in old code.
- Code/docs in English; user-facing responses in Russian, brief: outcome, checks actually completed, remaining issues. No milestone/PRD numbering in comments, docstrings, fields, labels, filenames.
- Never commit `.env*`, credentials, sessions, generated data, local DB files.
- Completion summary: behavior changed, key files, exact tests run, migration implications, remaining risks; no silent scope expansion.
- Closeout phrases — explicit authorization to commit validated task-scoped work in `dev` and run the `docs/COLLABORATION.md` handoff protocol: `закрываю чат`, `чат закрываем`, `переходим в новый чат`, `завершаем чат`, `задача завершена`, `задача с подзадачами завершена`, `подготовь новый чат`. A closeout is not permission to mark unfinished work complete.
