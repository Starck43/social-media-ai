# Analytics aggregation and navigation

This document owns the current analytics grouping, chain and presentation
contracts. Endpoint request/response details live in [API](API.md); table fields
live in [MODELS](MODELS.md).

## Data flow

`AIAnalytics` stores one analysis result for a source/date identity, including
structured summary data, provider response projection, usage fields, media types,
content hashes and an optional topic chain. Reporting reads rows through the
tenant-scoped manager and never widens the active workspace.

Main implementation boundaries:

- `app/services/ai/grouping.py` — shared grouping and row membership.
- `app/services/ai/reporting.py` — aggregates, specialized sections and digest brief.
- `app/services/ai/chain_resolver.py` — future chain assignment.
- `app/services/ai/topic_chain_service.py` — chain timeline projection.
- `app/api/v1/endpoints/dashboard.py` — analytics API.
- `app/web/analytics.py` — read-only analytics pages and authorized mutations.

## Selection before analysis (task scope)

Aggregation and analysis answer different questions: **selection** decides which
collected items reach the model at all, **grouping** only re-arranges rows that
were already analysed. The two must not be conflated — a keyword typed into the
analytics filter narrows the report, never the spend.

Selection is declared on the task, in three layers applied before the provider
call — sources, tracked users, text scope. The contract, matching rules and the
removal of `monitored_users` live in
[task selection](design/task-selection.md). This document keeps only what the
analytics layer depends on:

- items rejected by a selection layer stay staged until normal retention, are
  never written as analysed, and are not hidden from operator diagnostics;
- an item's author is `collected_items.author` (the `Author/Timestamp` snapshot
  of a wall repost), never the carrying source's own identity;
- reaction/action triggers (`KEYWORD_MATCH`, `USER_MENTION`,
  `SENTIMENT_THRESHOLD`, `ACTIVITY_SPIKE`) belong to the action, not to the
  selection: a keyword alarm and a keyword scope are different intentions.

**P5-T14 stage C:** deterministic selection now runs at collection. Rejected
text is not staged; only `selected`/`needs_review` rows reach
`collected_items`, with counts/reasons in `collection_lane_runs` and dedupe in
`collection_seen`. A comment-enabled batch is filtered at thread level (post +
comments). Analysis reads the stored decision for new rows and does not
re-filter; legacy NULL-status rows keep the old analysis-time pass once.
Collection types come from the task's `collection` block; scenario
`content_types` is supported media only. See
[collection plan](design/collection-plan.md).

## Subject scope (concept, not implemented)

For questions of the form "who X, according to platform Y", the graph is
*selected author → item → wall/carrier → text*. A chain is currently anchored on
`(workspace, source, topic_hint)`; the source is the carrier wall, so the same
story across several walls becomes several chains. Proposed, pending a separate
design:

- a chain may be aggregated across sources in one workspace when the hint is
  identical — the resolver compares within a workspace, not per source;
- a task (or scenario) may fix a stable `chain_hint` instead of letting each
  model response name the chain freely, so the story that recurs keeps one
  identity;
- a recurring subject (a person, an event, a company) is an **entity**, not a
  chain. Any "who took part in the trip" / "designers of the city" answer is a
  set of entity rows requiring evidence, not a free-text summary; today the
  analytics schema has no entity-evidence table, so such answers stay prose.

## Grouping contract

The grouping axis and time breakdown are independent query choices.

| Axis | Meaning | Availability |
| --- | --- | --- |
| `days` | Chronological view by analysis date | Web analytics only |
| `themes` | Distinct normalized topics | API, web and digest |
| `sources` | Exact source identity | API, web and digest |
| `entities` | Distinct entity name extracted by the model, optionally by person/brand/org | API, web and digest |
| `intent` | Extracted intent type | API, web and digest |
| `topic_chains` | Stored cross-row chain identity | API and digest; dedicated web chain pages |

`time_breakdown=true` adds dated entries inside any digest/API group. It does not
change the grouping axis. The API rejects `group_by=days`; the web UI presents it
as the chronology tab. The main web axis switcher omits `topic_chains` because
chains have their own page.

`group_analytics()` is the shared grouping implementation. Topics and entities
count each matching analysis once per distinct value. Sources group by source ID,
so equal display names do not merge. Sentiment and content type are cross-filters,
not grouping enum members.

**Entities are model-extracted, not a directory.** The `entities` axis lists names
that the analysis response reported *inside the analysed items*; it is neither a
verified person registry nor a match against a configured target list. A person
absent from every item cannot appear here, and a name found here is not proof of
identity, affiliation or participation. For "who is X" questions the axis ranks
candidates inside the collected corpus; it does not search the platform.

## Drill-down

`GET /app/analytics/group?axis=...&value=...` shows matching analyses newest first
using the same tenant, period, source and cross-filter scope as the group cards.
It requires `aianalytics.view`; invalid axes and filter values return HTTP 400.

| Axis | Membership rule |
| --- | --- |
| `themes` | Case-insensitive membership in distinct topics; the empty label matches missing topics. |
| `sources` | Exact integer `source_id`, never display name. |
| `entities` | Exact name with optional validated entity type. |
| `intent` | Extracted intent; unknown matches missing intent. |
| `days` | The selected chronology bucket. |

Sentiment uses the shared buckets: positive above 0.6, negative below 0.4 and
neutral at the boundaries. Content types are normalized to text/image/video.
Breadcrumb and back links may preserve only local read-only analytics URLs;
external, malformed, fragment or mutation targets fall back to a safe local page.
All stored labels and titles are escaped in HTML.

## Topic chains

The resolver normalizes topic hints by lowercasing, replacing `ё` with `е`,
removing punctuation and applying lightweight Russian stemming. Within the same
workspace/source and lookback window (30 days), token-set Sørensen–Dice
similarity of at least 0.85 may reuse a chain. Otherwise it creates a new ID. A
chain may contain a single row; existing IDs are never silently merged, split or
backfilled.

What the current contract does not promise, and an operator must not assume:

- **The hint is model-chosen.** `topic_hint` is a free string in every model
  response; two runs over the same story can name it differently and produce two
  chains. A task cannot pin the hint today — see [subject
  scope](#subject-scope-concept-not-implemented).
- **Chains do not cross sources.** The lookup is scoped by `source_id`, so the
  same story on three walls is three chains. Cross-source reading is an
  analytics concern (see the proposals above), not a resolver option.
- **Short hints link loosely.** With one or two tokens the 0.85 ratio is reached
  by a single shared word, which can join different stories. Treat a chain as a
  grouping hint, not as proof of semantic identity.
- **A second matcher exists.** `app/services/ai/theme_matcher.py` links a stored
  row to a similar chain by `main_topics` overlap at a different threshold
  (0.30). Chain identity is therefore produced by two rules; a review that
  changes one must check the other.

The chain list and detail pages are retrospective views. Chain links from a group
are shown only when the scoped timeline contains at least two matching rows. Chain
detail honors tenant/source/period scope. Merge/delete actions use their explicit
permission and confirmation paths; read-only navigation does not grant them.

Operator inspection uses the existing CLI commands:

```bash
.venv/bin/python -m cli.main chains groups --min-size 2
.venv/bin/python -m cli.main chains merge --apply
```

The mutating command requires the normal operator review and tenant boundaries.

## Metrics and presentation

The dashboard exposes sentiment trends, topics, content mix, engagement, provider
usage and grouped analytics. A value is shown only when the source data supports
its meaning. Missing reach, views or reactions stay unavailable; they are not
rendered as zero. Cost fields are estimates/projections and do not prove complete
spend accounting.

Web cards, drill-down, detail pages, digest previews and published digests reuse
shared extraction/grouping semantics. A presentation change must preserve:

- workspace, source, scenario and date filters;
- the distinction between unavailable and zero;
- stable local navigation and escaped labels;
- explicit partial coverage and source errors;
- no per-row database query for chain counts or labels.

### Incomplete analysis

The analyzer writes `analysis_metadata.analysis_complete = false` when text was
sampled, an attachment was not analyzed or a stage failed, plus display-only
`coverage_limits` codes (`text_sampled`, `media_uncovered`, `stage_failed`).
`render_analysis()["coverage"]` turns only an explicit boolean `false` into an
incomplete state. The detail page shows a notice with the recorded reasons (or a
generic one for older rows without codes); the dashboard, day feed and group lists
show an "incomplete" badge. A row without the marker is unknown coverage and gets
no badge — it is never labelled complete. Digests are unchanged; API responses that
return raw `summary_data` only gain the additive `coverage_limits` key.

### Operator retry of remaining inputs

The analysis detail offers `POST /app/analytics/{id}/retry` when remaining staged
inputs exist in its stored content time range. The form uses the existing violet
analysis button and shared confirmation dialog, CSRF, `aianalytics.view` plus
`source.analyze`, and an explicit owning workspace for operator POSTs. The server
rechecks the row/source workspace and available inputs before enqueueing. A
missing summary or legacy metric provenance offers investigation, not an
assertion that analysis never ran or that every absent field should exist.

- A retry admits one ordinary `analyze` batch (currently up to 100 staged inputs)
  and starts that exact committed Job in the web process with the normal atomic
  claim/heartbeat path. It does not require a separate runtime worker. An active
  stored scenario is retained when available; otherwise the existing task/source
  methodology fallback applies. Original task targets are not reconstructed.
  Saved rows follow the normal context-aware upsert rules; an unchanged old
  report is never presented as proof that the new batch completed its coverage.
- `staged_window` is an internal analyze-job payload with required ISO
  `start_date`/`end_date` timestamps. Preview, attempt reset and the worker use
  identical inclusive timestamp bounds, not CLI day truncation. Invalid bounds
  fail closed; they never become an unbounded run. No forced hash bypass,
  source configuration mutation, collection, send or publish is introduced.
- Missing/reversed bounds or no remaining raw inputs show an explanation instead
  of a retry button. To rebuild a historical result, configure the desired
  period and explicit full-cycle refresh on a collection task; staged inputs
  are not a permanent archive. A retry cannot manufacture unavailable metrics.
- Pending/running collect/analyze jobs suppress another explicit run, including
  sources resolved from task links or all active workspace sources. The shared
  manager admission gate serializes check-and-insert across explicit task/source
  starts; the read-only page hint is not the enforcement boundary. See
  [source-aware admission](AGENT_TASKS.md#source-aware-run-admission) for lock order,
  busy-task bookkeeping and the distinct new-input wakeup boundary.
- Attempt reset and enqueue share a transaction. Reset failure is not swallowed,
  and enqueue failure cannot commit the reset or report successful admission.

### Inline recovery and progress

`GET /app/analytics/{id}/retry-status` renders a shared progress component on
the analysis page. A tracked retry requires matching tenant, analysis marker,
exact single source and stored timestamp window; task-backed or mismatched Jobs
cannot be substituted. Other active work on the same inputs appears separately
as a blocking run. Pending means waiting for execution, not confirmed LLM
activity. Missing/stale heartbeat means unconfirmed activity. Neither state is
evidence that a provider is busy. Polling GETs never launch or modify Jobs.

`POST /app/analytics/{id}/retry/{job_id}/resume` explicitly resumes a compatible
legacy pending analysis that an older page enqueued without a web executor.
It rechecks CSRF and permissions, authenticated actor and owning workspace,
single-source/window identity, due time, zero attempts and absence of competing
work under admission/row locks. It never adopts a task-backed run or another
actor's work. Missing initiator and analysis correlation are stamped before the
web executor is launched after commit. Repeated requests still use the normal
atomic claim; a lost web launch does not create a permanent resume latch.

Each source result includes bounded `analytics_ids` from freshly persisted
analyzer output, recorded before trigger filtering. Historical fallback rows
are excluded. On completion the page refreshes an upserted current result, or
opens a unique saved result with the same source/date/period/window; other saved
results remain available as links. The user stays in analytics, without a
queue/source navigation detour. Related task links require `agenttask.view`.

The detail layout prioritizes summary, originals, topics and highlights. Empty
sections and unavailable metric cards are hidden; confirmed zeros are retained.
Provenance and extra metrics/chain history are collapsed. The explicit incomplete
marker and its saved reasons remain visible until persisted coverage changes.

The source detail shows remaining and exhausted inputs separately, uses the
shared stored-data sentiment projection (no dashboard-private imports), and
marks explicitly incomplete recent rows with the existing badge. Its general
analysis action keeps the configured task/source date window, resets only that
window, and does not enqueue an empty staged batch. The exhausted-input action
is hidden without `source.analyze`. Dashboard, chronology and group badges already
link to the detail; they remain read-only. Missing summary/sentiment/topics,
legacy metrics, missing timestamps and unknown coverage are not reclassified as
complete or failed. Digest and sqladmin presentation are unchanged.

Validation for this slice is remote pure/mocked/template evidence only. Real
ASGI/DB permissions, transaction rollback, worker execution and owner localhost
records require local sequential Kilo validation; no schema migration is needed.

## Validation focus

For grouping changes, test row membership, duplicate normalization, empty fields,
boundary sentiment values and tenant/source filters. For navigation, test encoded
return paths, invalid values, permissions and HTML escaping. For chain changes,
test workspace/source/lookback isolation, similarity boundaries and singleton
behavior. Mock providers and external APIs; use the project virtualenv and the
shared-schema restrictions in `AGENTS.md`.
