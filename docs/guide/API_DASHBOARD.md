# Dashboard Aggregation API

Latest endpoints are implemented in `app/api/v1/endpoints/dashboard.py`. They use `ReportAggregator` and DB session dependency `get_db`.

Base path examples here assume `/api/v1` prefix.

## Sentiment Trends
- GET `/dashboard/analytics/aggregate/sentiment-trends`
- Query:
  - `source_id: int?`
  - `scenario_id: int?`
  - `days: int = 7` (1..90)
  - `group_by: str = day` (day|week)
- Response:
```json
{
  "trends": [
    {
      "date": "2025-10-10",
      "avg_sentiment_score": 0.23,
      "distribution": {"positive": 12, "neutral": 5, "negative": 3},
      "total_analyses": 20
    }
  ],
  "period_days": 7,
  "group_by": "day"
}
```

## Top Topics
- GET `/dashboard/analytics/aggregate/top-topics`
- Query: `source_id?`, `scenario_id?`, `days=7`, `limit=10`
- Response:
```json
{
  "topics": [
    {"topic": "AI", "count": 42, "avg_sentiment": 0.35}
  ],
  "period_days": 7,
  "total_topics": 1
}
```

## LLM Provider Stats
- GET `/dashboard/analytics/aggregate/llm-stats`
- Query: `source_id?`, `scenario_id?`, `days=30`
- Response (example):
```json
{
  "providers": {
    "openai": {
      "requests": 120,
      "total_tokens": 154000,
      "avg_tokens_per_request": 1283.3,
      "estimated_cost_usd": 12.45
    }
  },
  "summary": {
    "total_cost_usd": 23.10,
    "total_requests": 220
  }
}
```

## Content Mix
- GET `/dashboard/analytics/aggregate/content-mix`
- Query: `source_id?`, `scenario_id?`, `days=7`
- Response:
```json
{
  "media_types": {
    "text": {"count": 80, "percentage": 72.7},
    "image": {"count": 20, "percentage": 18.2},
    "video": {"count": 10, "percentage": 9.1}
  }
}
```

## Engagement Metrics
- GET `/dashboard/analytics/aggregate/engagement`
- Query: `source_id?`, `scenario_id?`, `days=7`
- Response:
```json
{
  "avg_reactions_per_post": 3.2,
  "avg_comments_per_post": 1.1,
  "total_reactions": 256,
  "total_comments": 84
}
```

## Topic Chains
- GET `/dashboard/topic-chains`
- GET `/dashboard/topic-chains/{chain_id}`
- GET `/dashboard/topic-chains/{chain_id}/evolution`

Evolution response item (simplified for charts):
```json
{
  "analysis_date": "2025-10-10",
  "topics": ["deepseek", "openai"],
  "sentiment_score": 0.2,
  "post_url": null
}
```

## Examples (curl)
```bash
curl "http://localhost:8000/api/v1/dashboard/analytics/aggregate/sentiment-trends?days=14&group_by=day"

curl "http://localhost:8000/api/v1/dashboard/analytics/aggregate/top-topics?days=14&limit=5"

curl "http://localhost:8000/api/v1/dashboard/analytics/aggregate/llm-stats?days=30"
```
