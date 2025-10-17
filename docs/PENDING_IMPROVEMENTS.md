# Pending Improvements (Consolidated)

This file aggregates outstanding follow-ups mentioned across code/comments and planning notes. Track progress here.

## High priority
- Update API docs (`docs/API_REFERENCE.md`, `docs/openapi.yaml`) to include new dashboard aggregation endpoints under `/dashboard/analytics/aggregate/*` and topic chains endpoints
- Add rate limiting on media counts during analysis (to prevent cost spikes) — see TODO in scratch and analyzer
- Implement CLI command to estimate request costs and available quotas per configured providers; expose read-only calculated fields in admin

## Dashboard & Admin
- Add `topic_chains_dashboard.html` (template) and respective frontend JS to render chains and evolution
- Add Admin widgets to `BotScenario`/`Source` pages showing recent analytics and costs
- Export PDF/Excel for dashboard (server-side generation or client-side export with an endpoint)
- Real-time updates via WebSocket for dashboard widgets
- Customizable widget layout and persistence per user

## AI & Providers
- Validate provider type strictly via `LLMProviderType` when reading from DB/configs
- Enforce or document limits on numbers of media items per analysis request
- Finalize legacy fields strategy in `BotScenario` (individual FK provider fields kept for backward compatibility) and plan deprecation

## Tests
- Extend tests to validate AIAnalytics token/cost fields in analyzer flow
- Add tests for `ReportAggregator` methods (unit with fixtures) beyond endpoint contracts

## Documentation
- Keep `docs/guide/*` synced with implementation changes
- Cross-link `UNIFIED_DASHBOARD_SYSTEM.md` and new guide files

## Nice to have
- Public demo mode with masked data
- Theming support for dashboard (light/dark toggle) reflected in CSS variables

---
Last updated: generated from current codebase state. Keep this list in PRs that touch dashboard, providers, or analyzer.
