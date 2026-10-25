# Agent entry point

Shared rules apply equally to local/remote agents. Owner instructions override
project procedures. No selected skill means general execution, not a special role.

## Read once; then load lazily

1. [README](README.md): what the project does and the document hierarchy.
2. This file: mandatory safety and routing. Declare and read one selected skill.
3. [ROADMAP](ROADMAP.md): select one row; open only its linked task card.

Do not preload other docs. The skill/task selects relevant sections from
[COLLABORATION](docs/COLLABORATION.md) or the [subsystem index](docs/DOCS_INDEX.md).
Record documents actually consulted in the task card, with sections and purpose.

| Work | Shared skill |
| --- | --- |
| Implementation | [task](.agents/skills/task/SKILL.md) |
| Audit/review, authorized refactoring/docs | [audit](.agents/skills/audit/SKILL.md) |
| Remote task / local PR helper | [remote-task](.agents/skills/remote-task/SKILL.md) / [helper](.agents/skills/remote-agent-helper/SKILL.md) |
| OpenCode / Kilo execution | [opencode-task](.agents/skills/opencode-task/SKILL.md) / [kilo-task](.agents/skills/kilo-task/SKILL.md) |
| Test work | [test-runner](.agents/skills/test-runner/SKILL.md), then [tests/AGENTS](tests/AGENTS.md) |

## Always enforce

- ROADMAP owns open status/owner/dialogue links; task cards own scope, acceptance,
  documents read and checks. Claim before edits; use a task-ID branch/worktree,
  never integration `dev`. Preserve foreign files, claims and evidence.
- Executor reports READY; auditor reviews the exact SHA. Accepted work is DONE:
  append result/evidence to its parent phase and remove the queue row. Record
  pending integration separately. BLOCKED means the task's solution/required
  acceptance cannot proceed; session/branch housekeeping never causes BLOCKED.
- Stop/delete owned child sessions after dialogue; report unsupported/already
  unavailable cleanup separately. Only Codex invokes third-party agents;
  unavailable routes fall back to native children. Others use native children only.
- Remote tasks require repo/PR links. PR body holds solution/evidence; all remote
  coordination uses PR comments. One custodian updates shared registry state.
- Resolve DB target/schema before access. Test-schema preparation/migrations are
  authorized under [database rules](docs/COLLABORATION.md#database); shared DB work
  is sequential and all synthetic records are cleaned afterward. No public test
  records without explicit Superuser approval; no public migration before full
  test-schema validation and separately authorized apply. Alembic only.
- Reuse ORM managers/services and transaction conventions. Access fails closed;
  recheck tenant/actor permissions at execution. Preserve approval/dry-run gates,
  payer/usage evidence and secrets. Mock paid APIs and external writes in tests.
- Auditor tests only after its own code edit AND non-obvious/security-critical
  behavior. Reuse executor evidence; full suites need explicit owner request.
- Active runtime: one `python -m app.runtime` owner. Celery/Redis and Streamlit
  remain frozen. No dev push, force-push, deployment or production mutation
  without explicit authorization; delete task branches only after verified merge.
- Scoped edits; English code/docs, brief Russian reports; Black/isort line 120.
  Never commit secrets, sessions, generated data or `.agents/tmp/`.

## Review and close

`/review` follows [audit](.agents/skills/audit/SKILL.md); `$audit` selects that role.
Report findings first. After acceptance: `✅ <ID>: <result>; removed from ROADMAP;
archive: <link>; checks: <actual results>`. Mention pending integration/cleanup
separately; neither changes an accepted task back to BLOCKED.
