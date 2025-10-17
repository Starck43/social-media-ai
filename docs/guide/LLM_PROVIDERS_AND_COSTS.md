# LLM Providers, Presets & Cost Tracking

## Enums and metadata
- `LLMProviderType` and `LLMStrategyType` in `app/types/enums/llm_types.py`
- Provider metadata in `app/core/llm_presets.py` (display name, default API URL, env var names, available models)

Example:
```python
LLMProviderType.OPENAI.display_name
LLMProviderType.OPENAI.available_models
```

## Admin
- `LLMProviderAdmin` in `app/admin/views.py`
  - Autofill metadata for selected provider via `LLMMetadataHelper`
  - Multi-select capabilities (media types)
  - Actions: test connection, toggle active

## Cost tracking
- Aggregation endpoint: `GET /api/v1/dashboard/analytics/aggregate/llm-stats`
- Returns per-provider tokens/request/cost and a summary.
- Extend `ReportAggregator.get_llm_provider_stats()` to align with your pricing model.

## Example response shape
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
