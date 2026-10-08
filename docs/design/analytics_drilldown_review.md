# Analytics drill-down navigation review

## Scope

Based on dev commit `ee894bd4bd22b9fbb29eb5db564a718ac1f37877`.
Target: dev only. No migration, provider calls or production database changes.
This is a web feature: no new API route and no API.md changes.

## Implementation

- `/app/analytics/group` is a read-only, tenant-scoped row list. It supports the
  requested six group filters and chronology day/week/month keys. It includes
  analyses without chains, validates query values, sorts date/ID descending and
  shows source/title/sentiment plus optional retrospective links.
- Group cards use encoded links to the flat list. Chronology headings link
  without invalid nested anchors around existing analysis links. Top chain
  cards and direct topic_chains groups link to the chain detail.
- Navigation carries effective days (including session-selected days), source,
  entity type, tenant and cross-filters. Chain sorting keeps its existing
  selection/filter behavior; detail/list/title/open/back links preserve query
  state. Chain detail respects tenant/source/date scope and nested titles.
- Grouping now counts every distinct topic per analysis, case-insensitively,
  rather than only its first topic. Duplicate entity mentions count once per
  analysis/name. These are membership counts, not occurrence frequencies.
  The shared extractor/matcher supports nested and flat stored shapes.
- Source groups retain stable source_id apart from the human display key.
  Same-named sources therefore link to different correct lists.
- Sentiment/content_type drill-down names remain filters, not restored enum
  members. topic_chains stays out of the switcher; ordinary loads still compute
  exactly five group-count aggregations.

## Permissions and isolation

`aianalytics.view` is checked before loading drill-down rows. The new page has no
mutation affordances. The query explicitly narrows to the active/selected
workspace as well as retaining manager scoping; crafted foreign tenant IDs do
not broaden a normal member's scope. Source IDs and URL query values are not
interpolated into SQL or unescaped HTML.

The legacy test suite globally forces the manager's bypass flag. Web fixtures
now stamp tenant_id explicitly, avoiding false-positive isolation tests and
bootstrap-tenant records accidentally masquerading as workspace records.

## Validation

- Focused web/grouping suite: 65 passed, including all six axes, unchained and
  empty-topic analyses, duplicate/case-variant topics and entities, same-named
  sources, chronology links, Viewer rights, denied-before-read and foreign
  tenant isolation, URL-encoded values and filter-preserving round trips.
- Full suite: `942 passed, 1 skipped, 10 warnings in 197.83s (0:03:17)` on a freshly reset local PostgreSQL drill_test schema
  (`python -m pytest --no-cov -q`). Existing deprecation warnings remain.
- `git diff --check`: passed.

## File-by-file changes

| File | Change |
| --- | --- |
| app/web/analytics.py | Scoped query/encoded URL helpers, flat group route, navigation links and chain timeline title fallback |
| app/services/ai/grouping.py | Shared row matcher, flat/nested extraction, all-topic membership, deduplicated counts and stable source_id |
| app/web/templates/web/analytics_group.html | New read-only list, breadcrumb, sentiment and optional chain links |
| app/web/templates/web/analytics.html | Full group-card links, chronology heading links, source/entity filter persistence and chain links |
| app/web/templates/web/analytics_chains.html | Preserve filters on existing title/open/back links; remove nested anchors |
| app/web/templates/web/analytics_chain_detail.html | Filter-preserving back link; existing date/title timeline retained |
| tests/test_web_analytics.py | Drill-down/navigation/permissions/isolation regressions and explicit tenant fixture stamps |
| tests/test_grouping.py | All-topic/casefold/duplicate membership regressions and corrected secondary-topic count |
| docs/ANALYTICS_AGGREGATION_SYSTEM.md | Web-only drill-down route, matching rules and navigation contracts |

## Design boundaries

The chain list remains a retrospective over rows with chain IDs, not the
membership source for ordinary groups. Chain detail retains the timeline rather
than applying an entity/sentiment filter to its narrative; the filter parameters
are retained for returning to the list. Ordinary groups include unchained rows.
No LLM/token-routing, roadmap, scheduling or publication behavior was changed.
