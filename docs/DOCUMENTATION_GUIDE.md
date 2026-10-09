# Documentation ownership, status and maintenance

## Scope

Navigation and consistency rules established 2026-10-09 against dev `234a23d`. This reorganizes reading paths without moving/deleting existing files or rewriting historical evidence. It is not a complete line-by-line code/reference audit.

## Where each fact belongs

| Question | Primary document | Do not duplicate |
| --- | --- | --- |
| Where do I start? | DOCS_INDEX.md | Full reference content |
| How does the system fit together? | PROJECT_MAP.md | Every ORM field/import |
| What is merged/in review/next? | IMPLEMENTATION_STATUS.md and the current task PR | Rolling checklists in every design file |
| What will the product do? | PRODUCT_PLAN.md and design/vision.md | Shipped-feature promises |
| What are the architectural boundaries? | ASSISTANT_ARCHITECTURE.md | Current execution details |
| Where can it be installed? | DEPLOYMENT_ARCHITECTURE.md | Tested deployment instructions |
| How do I operate the current app? | CONFIGURATION.md, DEPLOYMENT.md, CLI.md | Future profiles as runnable commands |
| How does one subsystem work? | AGENT.md, AGENT_TASKS.md, COLLECTION.md, DIGEST.md, NOTIFICATIONS.md | Historical test counts as current checks |
| What must pass before release? | BUSINESS_PRODUCTION_READINESS.md and ROADMAP_INTEGRATED.md | Feature completion based on one helper |
| What happened in a prior task? | design/* review/handoff and design/archive/ | Retrospective rewriting of baseline evidence |

Source code and migration graph establish repository behavior/schema; target environments establish deployed state. Documentation discrepancies must be reported and corrected, not resolved by assuming the newest prose implements its claims.

## Status vocabulary

- **PROPOSED:** a design/recommendation; no implementation implied.
- **IN REVIEW:** prepared in a named open PR; not in dev.
- **MERGED:** exact bounded change is in dev; not necessarily enabled/deployed.
- **ACTIVE:** execution path uses the component; requires code/config evidence.
- **DEPLOYED:** target environment/version/schema verified.
- **ACCEPTED:** named end-to-end acceptance evidence, scope and date recorded.
- **HISTORICAL:** prior baseline/experiment; not today's validation.

For each changing claim record baseline SHA, date, scope and evidence. A successful component test does not imply end-to-end readiness. A schema merge does not migrate production. A cloud/hybrid plan does not create connectors.

Keep the shared status tracker owned by the active implementation task. Another agent updates its own handoff and references the tracker rather than overwriting in-review edits. On merge, reconcile both task branches. PR #12's newer delivery checklist remains in that branch until merged; this docs task does not mark its factory done.

## Known reference discrepancies and correction queue

Observed at the inspected baseline; do not treat affected prose as authoritative:

- [ ] MODELS.md still says head 0086, while merged migration 0087 and its appended storage section exist. Verify with `alembic heads`; deployed revision requires `alembic current` in the target environment. The script was not run in this docs task.
- [ ] DEPLOYMENT_ARCHITECTURE.md retains baseline-time warnings about PR #5/#6 not being merged. They are now merged per the inspected tracker; topology is still proposed and no target migration/activation is established.
- [ ] Root AGENTS.md references app/services/llm, but the inspected services directory has no such path. Update the detailed guidance only after checking actual client locations/contracts; PROJECT_MAP uses verified directory paths.
- [ ] MODELS.md's text ER diagram/reference has historical assumptions (including source/scenario relationships); replace with a source-verified Mermaid ER view in a separate targeted model-reference pass. Do not infer every listed field from a stale diagram.
- [ ] Align older model/API-format, period and provider statements with actual code before claiming reference completeness.

These are tracked, not silently declared fixed by navigation changes. Avoid replacing a large model reference from an incomplete read or editing implementation-owned delivery docs while its PR is open. Next documentation unit is a targeted model/migration reference reconciliation; preserve the parallel agent's checkpoint contracts.

## Diagram policy

Use Mermaid in Markdown for GitHub-native rendering, with readable labels and a limited number of nodes per graph. Separate current and target architecture. Identify trust boundaries, source versus recipient, and opt-in versus active components. Label conceptual diagrams that are not exact imports/FKs. Use sequence diagrams for interactions and ER diagrams only after validating relationships.

Validation should include relative links, fenced blocks, whitespace and Mermaid rendering. Do not introduce binary exports as the only maintainable source. A docs-only change does not need repeated full application tests, but must state rendering/link checks not run. No code/test weakening to save credits.

## PR maintenance checklist

1. Read fresh dev and active PR status; use a new thematic branch.
2. Preserve all existing links and historical reports; no bulk rename/move without an explicit migration of links.
3. Update the relevant reference, PROJECT_MAP if behavior/placement changes, and DOCS_INDEX if adding a document.
4. Record prepared versus merged status, exact checks, limits and next continuation in the task handoff.
5. Recheck fresh dev before merge; resolve both textual and semantic conflicts, especially shared index/checklists.
6. Merge/deploy only with the owner's explicit instruction.
