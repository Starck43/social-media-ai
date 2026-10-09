# Local experience improvements

## Goal and boundaries

Help an owner reach the first trustworthy report, understand what happened and recover from problems without an operator explaining every screen. Build on the existing Jinja/HTMX UI and shared chat tools; do not create duplicate CRUD flows.

Baseline and proposal traceability: [proposal review](design/proposal_review.md), initially dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`, rechecked against dev `fa5fb1b224527865dd830e9024f37a67b1ea4748`. See the [delta recheck](design/business_readiness_recheck_2026_10_08.md). **This is a proposed backlog, not implemented behavior unless explicitly listed as an existing strength.** “Local” means a bounded product change on the present architecture, not an assertion that it is trivial or safe without tests.

Default lane is no schema change. Items explicitly marked conditional must get a separate design/approval if a durable model change is required. Security/budget dependencies from the [production plan](BUSINESS_PRODUCTION_READINESS.md) take precedence over cosmetic work.

## Existing strengths to preserve

- Scenario presets, prompt suggestion and a scenario wizard already exist.
- Source connection/readiness hints, per-source job outcomes, staged-content visibility and retry controls already exist.
- Analytics group drilldown, cross-filters, chain timeline counts and origin-aware back navigation recently landed; do not rebuild them.
- New dev extends contextual navigation to individual analyses, distinguishes missing/partial/unverified metrics from measured zero, and preserves safe original URLs plus metric/author metadata through staging. Treat these as delivered, not new UX-03/05 tasks. Historical metrics/URLs are not automatically backfilled.
- Report tools aggregate without LLM; the existing agent loop adds a narrative. Keep this boundary and the compact top-N response.
- Confirmation is shared across chat tools; retain human-readable previews and permission recheck.

Evidence: [onboarding](../app/web/onboarding.py), [sources](../app/web/sources.py), [scenario templates](../app/services/ai/scenario_templates.py), [analytics](../app/web/analytics.py), [compact reports](../app/agent/toolset/reports.py), [runtime](../app/agent/runtime.py).

## Small, reviewable work packages

| ID | Outcome and implementation boundary | Acceptance | Schema / dependency |
|---|---|---|---|
| UX-01 | Extend existing onboarding into a readiness checklist: connection → source → active scenario → collect/analyze coverage → preview → configured delivery target → successful test delivery. Show per-platform capabilities and timezone. | A fresh workspace completes one authorized sample window. Existing bootstrap/prune/learn task does not falsely complete analysis setup. Failure identifies the missing dependency and links to its fix. No sources/tasks silently created. | No schema expected. Production delivery PRD-01 required before multi-tenant test sends. |
| UX-02 | The action_send registry binding and explicit action permissions are now delivered: the `@tool` decorator sits on the real handler, the tool is gated on `botaction.update`, registry dispatch is tested, a Viewer is refused before any dispatch and expired confirmations are dropped. Remaining: make run/confirmation outcomes trustworthy — distinguish queued/running/succeeded/partial/failed/skipped/no new data; explain refetch/reanalysis/resend and costs. | Registry dispatch tested, not only direct function calls; Viewer cannot execute writes; expired/revoked approvals are refused. Preview does not consume a pending action or publish. Keep live actions disabled until PRD-02/09. | No migration expected for binding/labels. Durable approval tracking may need separate design. |
| UX-03 | Individual-analysis metric semantics/original links and contextual navigation are now delivered. Preserve them; extend coverage/freshness explanations to aggregate/digest views and recommend grouping from existing aggregate density without changing saved tasks. | Clearly distinguish analyses, unique topics/entities and mention frequency. Window/filter state survives drilldown/back. Empty baseline is “insufficient data”, not zero change. No added LLM call for group counts/preview. | No schema expected. Coordinate with active analytics PRs. |
| UX-04 | Reuse agent_style.language for digest prompt, titles and fallback rendering. Start with explicit RU/EN support and a documented default. | Same aggregate yields correct language with and without LLM; timestamps respect workspace timezone; unknown preference falls back consistently; HTML/splitting stay valid. | No Tenant.language column. |
| UX-05 | Add read-only “why this analysis?” audit panel: model/provider, methodology hash, safe config, source/window and timestamp. Separate methodology hashes from exact rendered prompt hashes. | Available with DEBUG off; credentials/raw text are not revealed. Historical rows say audit unavailable. Reanalysis preview checks raw availability/refetch access and cost before confirmation; unavailable content is explained. | Audit viewer: no schema. Reanalysis/provenance expansion conditional; PRD-03 first for reliable cost. |
| UX-06 | Show spend honestly with units, coverage and unknown attribution; show budget reset timezone (currently UTC). Later add deduplicated threshold warnings. | No cents/USD confusion; failed/retried calls not described as free; reports say when attribution is incomplete. Warning at 80% fires once/day/threshold and never sends another tenant's details. | Viewer may use existing data with caveats; complete warning/accounting depends on PRD-03, may require ledger migration. |
| UX-07 | Turn prompt_advice into a proposal with explicit target and diff; explain trigger thresholds from scoped historical data and show suggested task change. | Owner sees evidence/window/sample size. Apply uses existing scenario_update/task_update confirmation and fresh permission checks. Missing/ambiguous target asks for selection; advice is never executed as instructions. | No new tool/schema initially. Calibration can remain read-only. |
| UX-08 | Expose current memory/provenance safely: inspect, correct and forget, with confirmation on destructive changes. Explain automated reflect hygiene rather than implying model fine-tuning. | Only current workspace facts visible; correction/deletion affects next prompt; memory clear explains which scopes/watermarks it preserves. Forgetting and backups follow PRD-06 policy. | No schema for basic control; TTL/access statistics deferred. |
| UX-09 | Trial an explicit pre-analysis eligibility filter, separate from reaction triggers. Show a dry-run count and reasons before activation. | Rejected-for-analysis items cannot accidentally be treated as analysed hashes; retry/staging policy is explicit. False-negative sample reviewed; tokens/cost and useful-signal recall compared to baseline. Filter off preserves current behavior. | Design spike first, not just moving should_analyze earlier. Conditional metadata; PRD-03/06/07. |
| UX-10 | Validate demand for bounded CSV/PDF/Excel exports before implementing. A report deliverable is business value, not forbidden architecture. | Scope/permission/date limits, formula escaping for spreadsheet exports, redaction and expiration tested; counts match on-screen report; no public permanent download link. | No schema expected for one-shot export; persistent storage/scheduling requires separate design. |

## End-to-end acceptance journeys

1. **First value:** owner authorizes a supported source, picks a preset, chooses a bounded historical window and timezone, collects/analyzes a sample, previews the result and confirms delivery. Unsupported MAX collection is not advertised as available.
2. **Recovery:** expired access and provider timeout show distinct fixes; staged items remain recoverable within policy; retries do not duplicate successful delivery or imply no charge.
3. **Trust:** owner opens an aggregate, follows a group/chain and returns to the exact filters; audit explains methodology; insufficient coverage is visible.
4. **Least privilege:** Viewer can inspect authorized reports but not modify tasks, models, credentials, memory or actions. Workspace owner cannot administer the global provider fleet.
5. **No surprise:** setup and previews do not publish, increase permissions or enable automatic commenting. Destructive changes show an explicit diff/impact.

## Measurement without invented promises

Before/after on the same fixtures and representative pilot data:
- time and operator interventions to first successful report;
- completion/drop-off at each readiness step;
- recoverable failures with an actionable explanation;
- report usefulness feedback and factual errors;
- p95 aggregate/drilldown latency and query count;
- total priced spend per useful report, missing-price rate, eligibility-filter recall.

The research's <200 ms recall and 30–70% savings are hypotheses, not release claims. Define target values only after a baseline and business workload are agreed.

## Recommended local sequence

Start with the remaining UX-02 outcome labels alongside PRD-02 (its registry/permission regression is delivered); then UX-01 after safe delivery, UX-04 and the read-only part of UX-05. Leave analytics navigation stable while the user works there. Spend alerts and filtering follow complete accounting, not precede it. Exports and calibration are selected by pilot demand.

Each package should be one small PR from fresh dev, with relevant unit/permission/navigation tests and changed-contract docs. Do not batch all UX items into one release or reformat unrelated files.
