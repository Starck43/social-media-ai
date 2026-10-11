# AI pipeline, scenarios and prompts

This document owns the current analysis-methodology, prompt and typed-output
contract. API shapes are documented in [API](API.md); stored fields and relations
are documented in [MODELS](MODELS.md).

## Concepts

- `AgentScenario` defines an analysis methodology: content types, analysis types,
  scope, prompts, optional output schema and model preferences.
- `AgentTask` defines when that methodology runs and the sources, runtime payload,
  reaction and delivery targets for that run.
- A future multi-step business workflow is a separate concept. Do not encode its
  permissions, schedule or action state inside a scenario prompt.

Methodology belongs in `AgentScenario.scope`. Concrete run targets such as brands,
competitors or named people belong in `AgentTask.payload`. The parameter registry
in `app/services/ai/param_registry.py` enforces that separation.

## Analysis flow

1. Collection produces normalized text and attachment snapshots with source and
   workspace identity.
2. The analyzer selects eligible content and preserves explicit coverage gaps.
3. `PromptBuilder` builds a media prompt for text, image or video.
4. Model resolution uses an explicit scenario model or the shared strategy and
   model-level default rules. The selected model's provider supplies the client.
5. Provider output is parsed and validated against the compiled scenario contract.
6. A unified summary combines valid modality results.
7. The result is saved to `AIAnalytics` with source/date, structured payload,
   usage projection, analyzed media types, content hashes and optional topic chain.

Useful valid siblings may be saved when another modality is unavailable or
invalid. The row must then preserve incomplete coverage; missing media or an empty
model result must not certify the parent content hash as fully analyzed.

## Prompt assembly

`app/services/ai/prompts.py` owns prompt composition. It combines:

- system-owned context such as platform, media type, count and output instructions;
- the scenario's selected prompt (`text_prompt`, `image_prompt`, `video_prompt`,
  `audio_prompt` or `unified_summary_prompt`);
- scenario methodology variables from `scope`;
- concrete run variables from `AgentTask.payload`;
- structured-output instructions derived from the scenario contract.

Variable precedence is system-owned values, then task payload, then scenario scope.
Lower-trust values cannot replace system-owned instructions. Unknown placeholders
must fail validation instead of leaking unresolved template text to a provider.
Collected content, documents, messages and provider output are untrusted data, not
instructions that can grant tools or permissions.

Typical calls:

```python
PromptBuilder.get_prompt(
    MediaType.IMAGE,
    scenario=scenario,
    count=len(media_urls),
    platform_name=platform_name,
    task_payload=task_payload,
)

PromptBuilder.get_unified_summary_prompt(
    text_analysis,
    image_analysis,
    video_analysis,
    scenario=scenario,
    task_payload=task_payload,
)
```

## Structured output

`app/services/ai/json_schema_builder.py` compiles common fields plus the fields for
the selected `analysis_types`. Scope supplies methodology limits and vocabularies.
An explicit `AgentScenario.output_schema` is used when present; otherwise the
runtime derives the schema from `analysis_types` and `scope`.

Provider output is validated before persistence. Unknown analysis types are not
guessed. Empty typed objects, malformed media output and incompatible field types
remain validation failures. Validation does not authorize a write: tenant scope,
actor permissions and manager rules still apply at the operation boundary.

## Relevance and coverage

When `scope.relevance_filter` is enabled, the analyzer applies the configured
minimum confidence before saving the result. Submitted text sampling and media
availability are tracked separately from output validity:

- omitted eligible text remains available for later analysis;
- recognized but unavailable image/video attachments keep the parent incomplete;
- unknown or malformed attachments cannot create false coverage;
- cancellation or a failed save cannot retire hashes as completed work;
- a partial result cannot overwrite a stronger existing daily result merely to
  retain usage data.

## Scenario creation

Scenarios can be created through the existing admin, web/API or agent tools. Reuse
the presets in `app/services/ai/scenario_templates.py` and the builder in
`app/services/ai/scenario.py`. Validate enum values, scope keys, prompt variables
and model capability requirements before persistence.

Agent tool confirmation is a user-intent check, not authorization. The tool must
also enforce the current actor, workspace, permissions and normalized arguments at
execution time. Preferences saved in `scenario_prefs` may seed a later draft but
must not silently execute or grant access.

## Usage and cost boundary

Current provider responses project tokens and estimated cost into caller-specific
records and analytics. These projections do not form a complete billing ledger:
fallbacks, failed calls, missing usage and unknown tariffs can be incomplete.
Unknown cost must not be treated as free. The durable every-attempt ledger and
concurrent budget admission remain an open PRD-03 gate in
[business readiness](BUSINESS_PRODUCTION_READINESS.md).

## Key implementation files

- `app/services/ai/analyzer.py` — modality analysis, validation, coverage and save flow.
- `app/services/ai/prompts.py` — prompt assembly and variables.
- `app/services/ai/json_schema_builder.py` — output schema/instruction compiler.
- `app/services/ai/scenario.py` and `scenario_templates.py` — scenario construction.
- `app/services/ai/llm_client.py` and `llm_provider_resolver.py` — clients and model selection.
- `app/services/ai/param_registry.py` — methodology versus run-target parameters.
