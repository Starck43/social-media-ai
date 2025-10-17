# Admin UI (Dashboard pages)

Admin pages render HTML templates with JS widgets.

## Pages
- `GET /dashboard` -> `app/templates/analytics_dashboard.html`
- `GET /dashboard/topic-chains` -> `app/templates/topic_chains_dashboard.html` (planned)

Authentication is read from session in `app/admin/endpoints.py` (`analytics_dashboard()` and `topic_chains_dashboard()` read `request.session['token']` and call `get_authenticated_user`).

## Frontend Assets
- Template: `app/templates/analytics_dashboard.html`
- Styles: `app/static/css/dashboard.css`
- Scripts: `/static/js/dashboard.js` (expected helper utilities: `DashboardUtils` with `fetchAPI`, `showError`, `showLoading`, etc.)

## Widgets (data sources)
- Sentiment Trends: `/api/v1/dashboard/analytics/aggregate/sentiment-trends`
- Top Topics: `/api/v1/dashboard/analytics/aggregate/top-topics`
- LLM Stats: `/api/v1/dashboard/analytics/aggregate/llm-stats`
- Content Mix: `/api/v1/dashboard/analytics/aggregate/content-mix`
- Engagement: `/api/v1/dashboard/analytics/aggregate/engagement`

## Example: boot sequence (JS)
```js
// in analytics_dashboard.html inline script
document.addEventListener('DOMContentLoaded', function() {
  loadAllWidgets();
  setupAutoRefresh(loadAllWidgets);
});
```

## Adding new widget
1. Add API in `dashboard.py` or service method in `ReportAggregator`
2. Add card markup in template
3. Fetch data via `DashboardUtils.fetchAPI()` and render via Chart.js or DOM
