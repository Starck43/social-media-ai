# Analytics UI and shared theme/component refactor

Scope: web app templates, shared CSS/theme controller and compatibility of admin/
legacy dashboard theme selectors. No changes to tenant permissions, analytical
axes, scenario media processing, stored analyses or the API's media capability.

## Components

- Shared 24px outline SVG macro replaces emoji in analytical axes, chain heading,
  admin shortcut and scenario choices. Native select labels are plain text (SVG
  is not supported inside native options). Settings use the same segment component. Existing line-icon design language; no icon
  package, font or image assets added.
- `ui.css` provides card, link-card, toolbar, segments, badges and filter components,
  using the shared slate/cyan semantic tokens. 58 duplicate card bundles replaced.
- Audit of all 28 web templates paired missing light/dark surface/text/status colors,
  table separators, hover states and modal backdrops. Existing forms/field names,
  Alpine state, permission gates and confirm/CSRF behavior stay intact.
- Active grouping/type/period links are server-rendered (aria-current), not dependent
  on Alpine initialization. Mention types include an explicit "Все" reset.
- Sentiment uses the same .4/.6 boundaries everywhere and displays a human label
  plus score out of 1; aggregate badges explicitly describe an average in the title.
  Missing scores are not fabricated from legacy chain-service defaults. Plain-text
  digest headings use `Тональность: 0.80 / 1`, not `sent:`.

## Filters and compatibility

Sentiment is a labelled group of normal links (44px targets), preserving scope,
period, entity type and validated origin. It works without JS. Reused on aggregate,
flat-group and chain-list pages. Small screens use a two-column filter layout.
Media is not a visible selector: analysis has already normalized multimodal content.
Legacy media URLs still work in web/API; a visible reset explains and removes that
constraint instead of silently hiding an active filter. API/schema unchanged.

## Theme

One `theme.js` controller for web, admin login/layout and old dashboard entry points:
`light`, `dark`, `system`. Default/missing/invalid preference means system. Initial
resolution runs before paint; OS changes are followed only in system mode. Only a
user selection is persisted. Storage events synchronize tabs and all selectors.
Storage denial does not break rendering or explicit selection. Both desktop and
mobile have a labelled three-option selector. Reduced motion and native color-scheme
are respected. The old two-state controllers and universal transitions are removed.

## Verification

Full suite on updated dev (`7fedbd3`): 1082 passed, 1 skipped, 10 warnings (233.10s).
Parallel digest/notification changes are preserved; master is not changed.
Shared UI unit tests include SVG/badge/filter contracts, three-state theme JS,
light/dark flash contrast, auth/app theme reuse and duplicate-flash prevention.
Browser audit: 100 page/theme/width combinations (390px and 1440px), no horizontal
page overflow; real browser OS-mode changes and explicit theme persistence passed.

Screenshots
use seeded test fixture pages only; the user's localhost/production database is not
accessed. No LLM calls or reanalysis required.
