# Topic Chains (Evolution & Analytics)

Topic chains link related analyses over time (`AIAnalytics.topic_chain_id`).

## Endpoints
- `GET /api/v1/dashboard/topic-chains` — list chains with counts, date range, topics, and source info
- `GET /api/v1/dashboard/topic-chains/{chain_id}` — details with topic statistics
- `GET /api/v1/dashboard/topic-chains/{chain_id}/evolution` — simplified evolution for charts

## Data assembly
- Implemented in `app/api/v1/endpoints/dashboard.py`
- Uses `TopicChainService` to build and summarize chains
- Enriches chains with `Source` and `Platform` details

## Evolution item shape
```json
{
  "analysis_date": "2025-10-10",
  "topics": ["economy", "inflation"],
  "sentiment_score": 0.1,
  "post_url": null
}
```

## Example usage
```bash
curl "http://localhost:8000/api/v1/dashboard/topic-chains?limit=50"
curl "http://localhost:8000/api/v1/dashboard/topic-chains/CHAIN_123"
curl "http://localhost:8000/api/v1/dashboard/topic-chains/CHAIN_123/evolution"
```
