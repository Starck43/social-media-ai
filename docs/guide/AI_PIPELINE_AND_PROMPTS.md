# AI Pipeline & Unified Prompts

The analyzer uses a unified prompt system across modalities.

## Key files
- `app/services/ai/analyzer.py` — uses `PromptBuilder.get_prompt(MediaType, ...)` for media analysis and `PromptBuilder.get_unified_summary_prompt(...)` for final summary (see updated calls around image/video and unified summary).
- `app/services/ai/llm_client.py` and factories — create provider-specific clients.
- `app/types/enums/llm_types.py` — `LLMProviderType`, `LLMStrategyType`; `MediaType` is imported from content types.

## Flow
1. Collect media (text/images/video) and context (platform/source)
2. Build prompts via unified PromptBuilder
3. Send to LLM (provider chosen from `LLMProvider` or scenario defaults)
4. Store structured result in `AIAnalytics` (`summary_data`, `response_payload`, optional `topic_chain_id`)

## PromptBuilder usage
```python
# Image analysis prompt
prompt = PromptBuilder.get_prompt(
    MediaType.IMAGE,
    scenario=bot_scenario,
    count=len(media_urls),
    platform_name=platform_name,
)

# Unified summary prompt
prompt = PromptBuilder.get_unified_summary_prompt(
    text_analysis,
    image_analysis,
    video_analysis,
    scenario=bot_scenario,
)
```

## Custom prompts in Admin
`BotScenarioAdmin` fields (see `app/admin/views.py`) include specific per-media prompt fields:
- `text_prompt`, `image_prompt`, `video_prompt`, `audio_prompt`, `unified_summary_prompt`
Each has help text and textarea sizing via `form_widget_args`.

## Cost and tokens
LLM usage (tokens, cost) is tracked and surfaced via the LLM stats aggregation endpoint.
