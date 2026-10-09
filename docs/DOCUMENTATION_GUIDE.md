# Documentation ownership, status and maintenance

## Scope

Navigation refreshed 2026-10-09 against dev `eb49d1d`. One global plan → engineering stages → current-stage task board; historical evidence is preserved separately. This is not a full line-by-line code/reference audit.

## Where each fact belongs

| Question | Primary document | Do not duplicate |
| --- | --- | --- |
| Where do I start? | DOCS_INDEX.md | Full reference content |
| How does the system fit together? | PROJECT_MAP.md | Every ORM field/import |
| Which stage / task next? | ROADMAP_INTEGRATED.md → design/README.md | Competing session-wide backlogs |
| What is merged / evidenced? | IMPLEMENTATION_STATUS.md + current dev/PR | Old PREPARED labels as current status |
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

Keep occupied files with their active implementation lane. PR #22 currently owns shared tracker/next-session edits; this docs branch updates navigation only, without overwriting them. On integration reconcile both lanes. PR #12 is merged; its old preparation notes are evidence, not a new assignment. Do not create a new handoff merely to avoid coordination: use the existing board row and PR/commit handoff.

## Known reference discrepancies and correction queue

Observed at the inspected baseline; do not treat affected prose as authoritative:

- [x] PR #14 corrected the source-linked MODELS inventory/ER and stale inspected-head wording to 0087. This does not verify current graph/drift or deployed DB state.
- [ ] Current graph/drift/target DB evidence remains separate authorized work; no Alembic command ran in this docs task.
- [ ] DEPLOYMENT_ARCHITECTURE.md retains baseline-time warnings about PR #5/#6 not being merged. They are now merged per the inspected tracker; topology is still proposed and no target migration/activation is established.
- [ ] Root AGENTS.md references app/services/llm, but the inspected services directory has no such path. Update the detailed guidance only after checking actual client locations/contracts; PROJECT_MAP uses verified directory paths.
- [x] MODELS already contains the bounded source-linked Mermaid ER reconciliation from PR #14; do not rebuild it. Refresh only evidenced deltas in a separate task.
- [ ] Align older model/API-format, period and provider statements with actual code before claiming reference completeness.

These are tracked, not silently declared fixed by navigation changes. Avoid replacing a large model reference from an incomplete read or editing implementation-owned delivery docs while its PR is open. The prior model-reference reconciliation is complete as bounded documentation, not DB acceptance. Select only unresolved reference deltas; preserve active PR contracts.

## Diagram policy

Use Mermaid in Markdown for GitHub-native rendering, with readable labels and a limited number of nodes per graph. Separate current and target architecture. Identify trust boundaries, source versus recipient, and opt-in versus active components. Label conceptual diagrams that are not exact imports/FKs. Use sequence diagrams for interactions and ER diagrams only after validating relationships.

Validation should include relative links, fenced blocks, whitespace and Mermaid rendering. Do not introduce binary exports as the only maintainable source. A docs-only change does not need repeated full application tests, but must state rendering/link checks not run. No code/test weakening to save credits.

## PR maintenance checklist

1. Read fresh dev and active PR status; use a new thematic branch.
2. Check file ownership. Preserve historical reports and old-path compatibility; migrate known links before archiving. Do not move active PR contracts to tidy a directory.
3. Update the relevant reference, PROJECT_MAP if behavior/placement changes, and DOCS_INDEX if adding a document.
4. Update the current-stage board row and PR/English Owner handoff. Reuse existing technical evidence; add a new document only for a durable nonduplicated contract. No separate cleanup handoff file.
5. Recheck fresh dev before merge; resolve both textual and semantic conflicts, especially shared index/checklists.
6. Merge/deploy only with the owner's explicit instruction.
