# Documentation organization: delivery and continuation

Prepared 2026-10-09 on `ai/docs-project-map-and-navigation` from dev `234a23dd7d1d2ce8dd3813f1ed63c7c7ef748669`. Documentation-only PR; not merged/deployed. No application/schema changes, file moves or deletions.

## Prepared

- [x] PROJECT_MAP: six Mermaid views, repository responsibility table, current/merged/in-review/planned separation and boundaries.
- [x] DOCS_INDEX: start-here section, role/task reading routes, component evidence links, all prior reference links retained.
- [x] DOCUMENTATION_GUIDE: ownership, status vocabulary, maintenance, validation and explicit correction queue.
- [x] Preserve PR #12's newer shared checklist in its own branch; do not overwrite IMPLEMENTATION_STATUS with in-review work or mark atomic snapshot complete.
- [x] Inspect current app/services and runtime entrypoint; avoid perpetuating nonexistent dedicated llm directory in new map.

## Validation and limits

Local PROJECT_MAP checks passed: six Mermaid blocks, balanced fences, no trailing whitespace, baseline and status labels. Existing relative targets were checked against inspected docs/design listings and prior reads; new targets are supplied in this PR. Full automated link crawling, Mermaid rendering and exhaustive model/import/reference audit did not run. Git clone is unavailable on this computer due to DNS resolution failure; authenticated GitHub tools performed reads/writes. No application tests, live model/transport calls or target DB migration ran.

Dev contains 0087 migration file; this is not an `alembic heads/current` or production upgrade check. Known stale MODELS head/ER and deployment-baseline claims remain explicitly tracked in DOCUMENTATION_GUIDE, not reported as fixed. Historical reports are deliberately preserved.

## Remaining / next unit

- [ ] Owner reviews GitHub-rendered diagrams and navigation, then explicitly decides merge.
- [ ] Reconcile DOCS_INDEX with any new parallel links before merge. PR #12's tracker remains owned by that task.
- [ ] Targeted MODELS reconciliation: verify ORM fields/relationships and migration graph, correct head/ER and provide source-backed Mermaid ER diagram. Do not invent deployed revision.
- [ ] Reconcile historical-status phrasing in current deployment/reference guidance, retaining original evidence dates.
- [ ] Inspect actual LLM client locations/formats and correct stale root guidance in a separate bounded edit.

Continue by reading fresh dev, this handoff, DOCUMENTATION_GUIDE and active PRs. Do not rebuild already merged delivery helpers. Keep preparation/review/merge/deployment/acceptance distinct; preserve parallel code and checklist changes. A complete documentation audit is not claimed by this navigation package.
