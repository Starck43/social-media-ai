# Development rules (environment, DB, delegation)

Shared by all agents (Codex, OpenCode, Kilo, remote). Operational workflow:
[AGENTS.md](../AGENTS.md); handoff/commit protocol:
[COLLABORATION.md](COLLABORATION.md).

- **R1 — Schema-qualified DDL.** Always use the explicitly resolved schema
  (`settings.DB_SCHEMA` or Alembic context); never rely on search_path or
  hardcode public/application schemas in migrations or maintenance commands.
- **R2 — Enum refactors.** Run `pytest --co` after changing an enum or its
  imports before running tests, to catch stale members and import failures early.
- **R3 — Full-suite-only failures.** First inspect logs, test database state,
  fixtures and cleanup ordering without modifying data. Reproduce only after
  verifying the shared review environment is safe and no other pytest is
  active. A reset/new schema is NOT automatic: obtain explicit approval under
  R8 before any schema operation. Do not change application logic from an
  unverified cause.
- **R4 — Interrupted runs.** An interrupted run may leave dirty data or a live
  child process; it is incomplete evidence, never a green result. Check both
  read-only before another run. If state is uncertain, stop and ask; do not
  automatically reset/drop/create/stamp/migrate. Any separately approved reset
  must target the explicitly resolved TEST schema/database, never production.
- **R5 — Documentation sync.** Finish each phase by syncing docs to the actual
  code, migrations, permissions and CLI/API contracts. Record test results and
  limitations; do not claim a green suite without a completed run.
- **R6 — Preserve local owner work.** `/Users/admin/Projects/social-media-ai`
  (branch `dev`) is the primary sequential development and integration
  checkout. Inspect status before changes; never discard, stash, or commit
  unrelated work. For ordinary work, direct local commits in `dev` require
  owner acceptance of the proposed coherent commit split/messages. A
  user-started ROADMAP Goal that explicitly requires a commit after each
  accepted task authorizes those local, task-scoped commits without another
  prompt. Closeout phrases in `AGENTS.md` authorize validated task-scoped
  commits as well. Use an isolated worktree only when parallel work, PR
  review, risky experimentation or conflicting changes require it.
- **R7 — Isolate PR review and parallel work.** Never verify two PRs in one
  working tree; each review lane gets its own worktree. Check
  `git worktree list` before creating anything; reuse existing folders; prune
  only stale entries the owner confirmed.
- **R8 — Shared test schema, sequential only.** Every worktree resolves the
  same `test_schema` on `localhost:5432/social_manager`, so checks that use it
  must remain sequential; do not overlap DB-backed tests or separate execution
  routes against that target. Independently proven no-DB scopes may run in
  parallel. Parallel PR checks that use the shared schema remain
  code-review-only until separate schemas are prepared. The default pytest
  startup validates the existing schema read-only. Never create, reset, drop,
  stamp or migrate schemas automatically — `setup_test_db --reset`, `DROP`,
  `alembic` and manual column fixes require an explicit owner instruction. A
  direct owner authorization may permit a bounded exceptional change to the
  local `test_schema` only after read-only target/writer checks and a verified
  private backup; preserve other schemas and do not fake an Alembic baseline.
  That authorization is limited to its stated test-schema scope and does not
  permit public/business-schema changes. These restrictions override any older
  automatic-reset advice. Test delegation follows R10 and these restrictions.
  The resolved target itself is pre-approved for real ASGI/DB test execution
  (including `httpx` `ASGITransport` tests that create and clean up their own
  data); never ask the owner to reconfirm the target for such runs. This
  pre-approval covers running tests only — never reset, drop, migrate or
  reseed — and an uncertain or interrupted state still stops under R4.
  Preserve the private environment.
- **R9 — Delegated routine Ready/merge, safeguards retained.** The owner gives
  standing authorization to perform routine Ready/merge of agreed bounded
  packages after verifying fresh PR/dev heads, compatibility, required checks
  and relevant review evidence. Keep Draft while necessary evidence is
  pending; do not ask for confirmation at each routine step. Stop and clarify
  conflicts, unexpected PR-head changes, failing checks, unclear
  dependencies/ownership or elevated risk. Do not rerun completed checks
  without a specific changed risk. This is NOT schema/reset/migration/live/
  deploy or required-check-change permission. No direct dev push or
  force-push; never restore anonymous access or enable publication as a merge
  conflict workaround.
- **R10 — OpenCode owns primary test execution.** Delegate every assigned
  pytest scope through `.codex/agents/test-runner.toml`, using OpenCode `build`
  pinned to `QwenPlan/deepseek-v4.1-flash-1017` with that exact model name (no
  `opencode/` prefix). The primary invocation is
  `opencode run --dir <worktree> -m QwenPlan/deepseek-v4.1-flash-1017 --auto "<message>" --file <task-file>`.
  Verify the task file is attached and readable. Collect before execution
  after enum/import changes. Use `.venv/bin/python -m pytest`, assigned paths
  as positional arguments, and isolated flags
  `--noconftest -o addopts= -q --tb=short --no-header`. Independent scopes may
  run in parallel only after proving they do not touch the shared DB/schema or
  other shared mutable state; shared PostgreSQL checks remain sequential under
  R8. Neither the coordinating Codex process nor Codex subagents execute
  pytest directly; OpenCode executes assigned pytest commands through its
  tools (contract: `.agents/skills/test-runner/SKILL.md`). Never reset/drop/
  stamp/migrate/seed, call live providers, or run external writes as part of
  test delegation. Task files and evidence live in `.agents/tmp/`
  (`test-task*.md`, `test-report.md`); the report is human-audit evidence,
  never main-context input. Kilo's `test-orchestrator` → `test-runner` route
  is secondary and may be used only when the primary OpenCode route is
  BLOCKED; preserve the same scope, DB, evidence and no-destructive-action
  safeguards.

## Local-only paths

- `.agents/tmp/` — shared temp lane for task files, test reports and agent
  session notes (git-ignored). Each agent creates, reads, edits and removes its
  own files without per-file approval and cleans up finished items; coordinate
  before removing another agent's artifacts. Never used for durable task truth
  or checked-in deliverables.
- `.agent/private-budget-review/` — legacy local-only artifact path still
  referenced by `scripts/prepare_llm_budget_fixture.py` and its acceptance
  test; do not commit, do not repurpose the rest of `.agent/`.
