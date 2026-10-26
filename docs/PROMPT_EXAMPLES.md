# Short task prompts

Give a task ID and action; agents find card, phase, PR and revision. No SHA lookup
by the owner. Skills are shared in `.agents/skills/`; without a selected skill,
an agent follows general execution and all mandatory AGENTS rules.

| Situation | Prompt | Skill |
| --- | --- | --- |
| Local execution | `$task P8-T12 implement` | [task](../.agents/skills/task/SKILL.md) |
| OpenCode implementation | `Apply skill opencode-task: P8-T12 implement` | [opencode-task](../.agents/skills/opencode-task/SKILL.md) |
| Kilo implementation | `Apply skill kilo-task: P8-T12 implement` | [kilo-task](../.agents/skills/kilo-task/SKILL.md) |
| Remote Git/PR execution | `Apply skill remote-task: P8-T12 implement` | [remote-task](../.agents/skills/remote-task/SKILL.md) |
| Local help with real PostgreSQL | `$remote-agent-helper P8-T12 validate on test_schema` | [helper](../.agents/skills/remote-agent-helper/SKILL.md) |
| Codex audits a READY task/remote PR | `$audit P8-T12` | [audit](../.agents/skills/audit/SKILL.md) |
| Assigned focused checks | `Apply skill test-runner: P8-T12 run the card's required checks` | [test-runner](../.agents/skills/test-runner/SKILL.md) |
| Resolve review comments | `Apply skill remote-task: P8-T12 address PR comments` | [remote-task](../.agents/skills/remote-task/SKILL.md) |
| New question / TODO | `Add question: <short question>` | General execution; register it in ROADMAP |

`$skill` is Codex selection syntax. Other clients can use the explicit “Apply
skill” wording and load the named SKILL.md. These examples do not install slash
commands: `/audit` is usable only with a configured adapter; native `/review`
does not itself guarantee the project acceptance workflow. Use `$audit` for it.

## Remote work → local DB help → audit

Remote agent creates/locates a PR with repository/task links and a solution/check
summary in its body. Claim and status changes are published to dev/origin by the
registry custodian via PR comments. Local helper reads that PR and posts exact
checks/cleanup as a comment; remote agent links it in the body. Auditor reads
that body and current diff, then consolidates acceptance into audit/phase history
and removes the active card/queue row. Give a repository/PR URL only if ambiguous.

Use test_schema under [database safeguards](COLLABORATION.md#database). Required
checks must pass before READY; a required unrun check is recorded honestly.
Acceptance, integration and cleanup are separate facts. No approval for production
changes or solution publication is inferred from a short audit prompt.
