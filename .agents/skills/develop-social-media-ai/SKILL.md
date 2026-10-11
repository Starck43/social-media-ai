---
name: develop-social-media-ai
description: Develop Starck43/social-media-ai on GitHub or a local checkout. Use for new features, bug fixes, focused refactoring, code review, targeted tests, PRs targeting dev, and handoff to a local testing agent.
---

# Develop Social Media AI

## Establish current context
- Reply to the owner in Russian; write code, commit messages and repository documentation in English.
- Use https://github.com/Starck43/social-media-ai as the repository and dev as the integration branch.
- Read the current root AGENTS.md, applicable nested instructions, README.md and pyproject.toml before changing code. Inspect relevant .agent context and subsystem documentation. Treat current code and instructions as authoritative; do not infer implemented features from roadmap prose.
- Read the current docs/SOCIAL_MEDIA_AI_AUDIT_AND_ROADMAP.md as the primary entry point for project work, prioritization and architectural direction. Then use docs/DOCS_INDEX.md and relevant subsystem documents; consult docs/PRODUCT_PLAN.md, docs/ROADMAP_INTEGRATED.md and docs/design/README.md for supporting stage details.
- Verify audit findings against the current branch and code. Identify the applicable priority/release gate, dependencies and acceptance criteria before implementing a roadmap item. Scope one concrete task; do not treat proposed architecture as implemented functionality. Resolve repository-relative references in the current working revision even when audit links point to master.
- Discover available GitHub tools. Confirm read and write capability separately; a successful read does not establish permission to push. If shell checkout is unavailable, use repository APIs and obtain exact file versions.
- Establish the base dev commit, working-tree status and existing work before starting. Preserve uncommitted user changes.

## Implement a focused change
- Create a thematic feature or fix branch from fresh dev; use codex/<short-topic> by default. Open a PR with base dev. Do not push implementation directly to dev, merge, enable auto-merge or deploy without a specific owner instruction.
- Complete the requested implementation autonomously within scope. Ask only for missing decisions that materially affect behavior or compatibility.
- Inspect call sites, tenant scope, authorization and error handling before editing. Keep changes narrowly related to the task; do not reformat the repository or modernize unrelated modules.
- Preserve workspace isolation, owned channel bindings, credential encryption, bounded LLM usage and idempotent job behavior where affected.
- Use current project architecture: FastAPI, SQLAlchemy/Alembic, PostgreSQL and the DB-backed jobs runtime. Do not extend frozen Celery/Redis components unless the owner explicitly requests it.
- Resolve LLM provider/model configuration through existing DB-backed interfaces; do not hardcode a provider, key, model or price.
- Reuse existing SQLAlchemy models, managers, services and database/session lifecycle. Do not create ad-hoc engines, sessions or sessionmakers, or bypass workspace scoping and business rules with direct queries.
- Reuse existing utilities. Add Alembic migrations for schema changes and describe their data and rollback implications; do not apply migrations to production as part of development.
- Keep optional integrations startable when unconfigured. Mock external services in focused tests instead of sending messages, invoking paid APIs or using real user credentials.

## Validate proportionately
- For new behavior, add small meaningful tests of observable outcomes, boundary cases or regressions. Reuse established fixtures and test style. Avoid tests that merely mirror implementation.
- Inspect tests/conftest.py and scripts/setup_test_db.py before any pytest invocation: even targeted tests can initialize the database.
- Resolve the actual test DSN and schema without displaying secrets. Use an isolated test target. Never run destructive reset on a working or production database.
- Run relevant tests and checks from the repository root using .venv/bin/python -m ... or tools under .venv/bin/. Do not use system Python, bare pytest/pip/alembic or globally install project dependencies. Start with the narrowest test using -o addopts='' to disable default coverage overhead; if the project virtualenv is unavailable, report the blocker. Derive exact commands from current project configuration; do not assume dependencies or test markers exist.
- Use black/isort checks on changed Python files when available; do not run a repository-wide formatter.
- Delegate full regression, integration, end-to-end, load and environment-dependent validation to the owner's local agent. Do not mark deferred tests passed.
- When a dependency, database or execution capability blocks testing, record the exact limitation and prepare reproducible commands for local execution. Do not fabricate test results.

## Deliver through a PR
- Review the complete diff and check for unrelated changes, secrets and generated artifacts.
- Commit the change on the task branch and open a PR targeting dev when GitHub write access is available. Prefer a draft while required local validation remains outstanding.
- Include the concrete problem, resulting behavior, checks actually run, deferred checks and any migration/compatibility risks in the PR description.
- Report PR URL, head SHA and a concise handoff using references/local-validation.md.
- Recheck dev and PR head before incorporating another agent's results. Treat reports as applying only to the stated SHA.
- Continue fixing reported failures in the same task branch; rerun relevant checks. Do not merge automatically.

## Coordinate with the local agent
- Use the shared branch, PR and exact commit SHA as the durable exchange. Provide the handoff in chat or the PR description. Post comments to communicate with another person or agent only when the owner specifically authorizes that action.
- Give the local agent one validation or implementation assignment at a time. Avoid concurrent edits to the same branch/files; use separate branches/worktrees for simultaneous implementation.
- Accept a pasted test report from the owner as the default return channel. Fetch PR/CI reports when the owner asks to use them.
- Do not claim access to the owner's local machine, an existing IDE session or automatic agent-to-agent messaging.
- Use MCP coordination only when both sides have an explicitly configured reachable tool/channel and appropriate authorization. MCP does not itself synchronize conversation history or make localhost reachable from this session.
