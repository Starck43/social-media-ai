# Individual analysis detail: navigation and truthful metric states

Implementation base: dev commit `6f5b4511519685447a4d187ec9101c07d9b04a54`.
Before publishing, incorporated dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`
(documentation-only competitive_analysis description update) without overwriting it.
Target: dev only. No migration or automatic historical reanalysis/data rewrite.

## Outcomes

- Complete origin-aware navigation into/out of individual analysis, including
  group/source/dashboard/chronology/chain and other-analysis entry points.
- Direct historic links use all-time source fallback; chain links require >=2
  scoped rows and return to the analysis. Themes open flat filtered groups.
- Context makes it explicit that this is one saved analysis, with platform,
  publication window, saved timestamp, volume and optional original URLs.
- Missing/partial/unverified metrics render —; confirmed measured zero renders
  0. Legacy nonzero stored values retain a completeness disclaimer. New metric
  coverage propagates from normalizers through staged/deferred content.
- Sentiment shown out of 1; material-level averages replace misleading ER.
  Author/account count is not an audience count; rollup sums are not uniqueness.
- Distinct empty/missing highlight states and no false statement that a missing
  summary proves analysis did not run. No live provider call was required.

## Compatibility and limits

Stored legacy numeric totals and engagement_rate formula remain unchanged.
Availability is metadata in existing JSON fields, not a new migration. The
change does not retroactively establish collection completeness or recover
original URLs that were never retained. Legacy zeros are conservatively
unverified. Old LLM summaries are displayed as stored, not regenerated.

Collection already constructs/stores supported platform permalinks. These
saved values now reach immediate analysis as well as deferred replay, before
staging retirement. The renderer never synthesizes a post URL from an analysis
ID. Up to 50 distinct safe originals are retained. New availability flags are
not an assertion that platform-side API information is independently audited.

The JSON staging tests exposed untyped CAST binds failing for Python dicts;
metrics/author use SQLAlchemy JSON binds, preserving SQL NULL behavior. This
is required to persist and replay availability flags safely. Hashes still exclude
metrics/URLs, so mutable metadata does not defeat dedup/retirement.

## Validation

Focused rendering/navigation/staging/period tests passed after the JSON bind
fix. Additional normalizer checks cover missing API fields versus provided zero.
Focused suites: 80 passed and 39 passed.
Full suite: 974 passed, 1 skipped, 10 warnings (215.53s).

git diff --check passed. API.md and migrations are unchanged. Production data
and the master branch are not modified.

## File groups

- app/web/analytics.py + web templates: detail context, route guards, safe
  origin destinations and contextual back links, themes and chain navigation.
- app/services/ai/analysis_render.py: compatible payload extraction, conservative
  metric states, original URL safety and explicit semantic display metadata.
- app/services/ai/analyzer.py: retain coverage/originals with numeric compatibility.
- app/services/ai/grouping.py: parsed text payload support for topic drill-down.
- VK/Telegram/ingest normalizers: metric availability, preserving numeric contract.
- Collection/CollectedItem/manager: persist and replay metric/author/permalink
  metadata using typed JSON binds, independent of transient staging lifetime.
- sqladmin detail template: shared renderer unknown values handled safely.
- tests: measured-zero/unknown/partial, malformed scores/URLs, originals, staged
  JSON replay, direct historic fallback, full navigation and Viewer isolation.
