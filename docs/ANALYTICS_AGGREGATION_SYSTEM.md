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

## Grouping contract

The grouping axis and time breakdown are independent query choices.

| Axis | Meaning | Availability |
| --- | --- | --- |
| `days` | Chronological view by analysis date | Web analytics only |
| `themes` | Distinct normalized topics | API, web and digest |
| `sources` | Exact source identity | API, web and digest |
| `entities` | Distinct entity name, optionally by person/brand/org | API, web and digest |
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
workspace/source and lookback window, token-set Sørensen–Dice similarity of at
least 0.85 may reuse a chain. Otherwise it creates a new ID. A chain may contain a
single row; existing IDs are never silently merged, split or backfilled.

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

- A retry queues one ordinary `analyze` batch (currently up to 100 staged inputs)
  with the current tenant-default scenario. It does not reconstruct the original
  scenario/task payload or rebuild/close the historical row. Check new results
  and remaining inputs after execution; another batch may be needed.
- `staged_window` is an internal analyze-job payload with required ISO
  `start_date`/`end_date` timestamps. Preview, attempt reset and the worker use
  identical inclusive timestamp bounds, not CLI day truncation. Invalid bounds
  fail closed; they never become an unbounded run. No forced hash bypass,
  source configuration mutation, collection, send or publish is introduced.
- Missing/reversed bounds or no remaining raw inputs show an explanation instead
  of a retry button. To rebuild a historical result, configure the desired
  period and explicit full-cycle refresh on a collection task; staged inputs
  are not a permanent archive. A retry cannot manufacture unavailable metrics.
- Pending/running explicit source collect/analyze jobs suppress another manual
  request. This is a best-effort busy check, not a concurrency/idempotency lock
  across simultaneous requests or task jobs with indirectly resolved sources.
- Attempt reset and enqueue share a transaction. Reset failure is not swallowed,
  and enqueue failure cannot commit the reset or report successful admission.

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
