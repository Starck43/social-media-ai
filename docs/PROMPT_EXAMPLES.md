# Short task prompts

Supply a task ID and action. Agents find the card, branch/PR and source revision;
you do not need to find a SHA. Skills are shared by local and remote agents.
If the interface does not support `$skill`, write `Apply skill <name>: <ID> <action>`.

| Where / purpose | Prompt | Skill |
| --- | --- | --- |
| Remote agent in its assigned repository: implement through Git/PR | `$remote-task P8-T12 implement` | [remote-task](../.agents/skills/remote-task/SKILL.md) |
| Local Codex: help that PR validate against PostgreSQL | `$remote-agent-helper P8-T12 validate on test_schema` | [remote-agent-helper](../.agents/skills/remote-agent-helper/SKILL.md) |
| Codex: audit the remote agent's completed PR | `$audit P8-T12` | [audit](../.agents/skills/audit/SKILL.md) |
| Local implementation | `$task P8-T12 implement` | [task](../.agents/skills/task/SKILL.md) |
| OpenCode implementation / native tester coordination | `$opencode-task P8-T12 implement` | [opencode-task](../.agents/skills/opencode-task/SKILL.md) |
| Kilo implementation | `$kilo-task P8-T12 implement` | [kilo-task](../.agents/skills/kilo-task/SKILL.md) |
| Explicit test execution | `$test-runner P8-T12 run the checks assigned in the card` | [test-runner](../.agents/skills/test-runner/SKILL.md) |

## Remote implementation → local DB validation → audit

1. Remote agent implements on a task-ID branch and creates/locates its PR. Its
   body records the solution, documents read, checks and missing local validation.
   Requests for local help specify scope/commands in PR comments.
2. Ask local Codex for helper validation with the prompt above. It resolves the
   PR revision, arranges the assigned checks through the tester and posts results
   and cleanup in a PR comment. Remote agent links the result in the PR body.
3. Ask Codex for `$audit P8-T12`. It reads the PR body, resolves the current head,
   reviews the diff and matching evidence, then posts findings/acceptance. Accepted
   work is archived in its parent phase and removed from ROADMAP.

The helper uses the real PostgreSQL instance's isolated **test_schema** for
synthetic data and migration validation, under [database rules](COLLABORATION.md#database).
Public test records require explicit Superuser approval; public migrations need
prior test-schema validation and separately authorized apply. Auditor testing
follows [AGENTS](../AGENTS.md); local helper validation is an assigned execution role.

## Missing context or follow-up

- If the remote agent has no repository context, give its URL once. If several
  PRs match the task, give the PR link once. Agents ask only for unresolved details.
- For fixes after review: `$remote-task P8-T12 address PR review comments`.
  For the next audit: `$audit P8-T12`. Agents resolve the updated head themselves.
- PR comments hold remote instructions/questions/results; the PR body summarizes
  the solution and evidence. The shared task card links the PR, avoiding copies.
- Audit acceptance does not authorize merge, deployment or public DB changes.

These are prompt examples, not newly installed CLI commands. `$audit` explicitly
selects the project auditor skill; native `/review` remains the CLI review command.
