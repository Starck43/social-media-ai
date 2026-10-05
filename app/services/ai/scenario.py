import logging
from typing import Optional

from app.models import AgentScenario
from app.types import AgentActionType

logger = logging.getLogger(__name__)


class PlanLimitError(Exception):
    """A workspace tier has no room for what was asked for.

    Raised by services that enforce `tenants.plan` quotas, so a REST layer can
    answer 403 with the message a reader can act on rather than letting a quota
    breach surface as an opaque 500.
    """


async def _scenario_limit_reason() -> Optional[str]:
    """Why another scenario cannot be created in the ambient workspace, or None.

    Reads the ambient tenant scope rather than a passed-in row: every caller of
    `ScenarioService` already runs inside the workspace it is writing to, and
    resolving it from one place keeps the service free of a tenant parameter it
    would only forward.
    """
    from app.core.tenant_context import current_tenant_id
    from app.models.managers.tenant_manager import tenants
    from app.services.tenancy.limits import check_scenario_limit

    tenant_id = current_tenant_id()
    if tenant_id is None:
        # No workspace in scope means an operator-level call outside a tenant
        # (seeding, tests). Refusing there would break setup, not protect a
        # quota that is not being consumed.
        return None
    tenant = await tenants.get(id=tenant_id)
    if tenant is None:
        return None
    return await check_scenario_limit(tenant)


def build_output_schema(analysis_types: Optional[list[str]] = None, scope: Optional[dict] = None) -> dict:
    """Generate a JSON Schema for structured LLM output from analysis_types.

    Used lazily at runtime: when a scenario has no explicit output_schema, it is
    derived from the configured analysis types so the LLM returns a parseable
    shape. The field contract itself lives in `JSONSchemaBuilder` — this only
    asks it for the same schema the prompt renders, so the stored contract and
    the instruction cannot drift apart.

    `scope` is the scenario's, so the placeholders in the descriptions resolve to
    this scenario's configured vocabularies rather than the defaults.
    """
    from app.services.ai.json_schema_builder import JSONSchemaBuilder

    return JSONSchemaBuilder.build_json_schema(analysis_types or [], scope or {})



class ScenarioPromptBuilder:
    """
    Helper class for building AI prompts from scenario configuration.
    
    This class handles:
    — Variable substitution in prompt templates
    — Merging scenario scope with default parameters
    — Building complete prompts ready for LLM consumption
    """

    @staticmethod
    def build_prompt(scenario: AgentScenario, context: dict) -> str:
        """
        Build AI prompt from a scenario with runtime context injection.

        Args:
            scenario: AgentScenario instance with analysis_types and scope
            context: Runtime context (platform, source_type, content, etc.)

        Returns:
            Complete prompt ready for LLM
        """
        from app.core.analysis_constants import merge_with_defaults

        prompt_template = scenario.ai_prompt or ""
        if not prompt_template:
            logger.warning(f"Scenario {scenario.id} has no ai_prompt defined")
            return ""

        # Merge scope with defaults for selected analysis types
        config = merge_with_defaults(scenario.analysis_types, scenario.scope or {})

        # Build complete variables dict
        variables = {
            # System variables
            'platform': str(context.get('platform', '')),
            'source_type': str(context.get('source_type', '')),
            'total_posts': str(context.get('total_posts', 0)),
            'date_range': str(context.get('date_range', {})),
            'content': str(context.get('content', '')),
            
            # Analysis types
            'analysis_types': ', '.join(scenario.analysis_types) if scenario.analysis_types else '',
        }
        
        # Add flattened config for template access (e.g., {sentiment_config.categories})
        # Convert nested dicts to dot-notation accessible format
        for key, value in config.items():
            if isinstance(value, dict):
                # Add the whole dict as string
                variables[key] = str(value)
                # Also add individual nested keys for access like {sentiment_config.categories}
                for nested_key, nested_value in value.items():
                    # Convert lists/dicts to comma-separated strings or JSON
                    if isinstance(nested_value, (list, tuple)):
                        formatted_value = ', '.join(str(v) for v in nested_value)
                    elif isinstance(nested_value, dict):
                        formatted_value = str(nested_value)
                    else:
                        formatted_value = str(nested_value)
                    variables[f"{key}.{nested_key}"] = formatted_value
            else:
                variables[key] = str(value)

        # Safe template formatting with fallback
        try:
            prompt = prompt_template.format(**variables)
        except (KeyError, ValueError) as e:
            logger.warning(f"Template formatting issue in scenario {scenario.id}: {e}. Using fallback.")
            # Fallback: manual replacement
            prompt = prompt_template
            for key, value in variables.items():
                # Replace both {key} and {key.nested}
                prompt = prompt.replace(f'{{{key}}}', str(value))

        return prompt

    @staticmethod
    def build_analysis_instructions(analysis_types: list[str], config: dict) -> str:
        """Build human-readable analysis instructions from a config."""
        instructions = []
        config = config or {}

        for at_name in analysis_types:
            cfg = config.get(at_name, {})
            if at_name == "sentiment":
                categories = cfg.get("categories", [])
                instructions.append(f"Sentiment analysis: {', '.join(categories)}")
            elif at_name == "trends":
                min_mentions = cfg.get("min_mentions", 5)
                instructions.append(f"Trends detection (min {min_mentions} mentions)")
            elif at_name == "engagement":
                metrics = cfg.get("metrics", [])
                instructions.append(f"Engagement analysis: {', '.join(metrics)}")
            elif at_name == "keywords":
                keywords = cfg.get("keywords", [])
                if keywords:
                    instructions.append(f"Keywords tracking: {', '.join(keywords[:5])}")
                else:
                    instructions.append("Keywords extraction")
            elif at_name == "topics":
                max_topics = cfg.get("max_topics", 5)
                instructions.append(f"Topics identification (top {max_topics})")
            elif at_name == "toxicity":
                threshold = cfg.get("threshold", 0.7)
                instructions.append(f"Toxicity detection (threshold: {threshold})")
            elif at_name == "demographics":
                instructions.append("Demographics analysis")

        return "; ".join(instructions) if instructions else "General analysis"


class ScenarioService:
    """Service for managing bot scenarios, and their application to sources."""

    async def create_scenario(
        self,
        name: str,
        description: Optional[str] = None,
        analysis_types: Optional[list[str]] = None,
        content_types: Optional[list[str]] = None,
        scope: Optional[dict] = None,
        ai_prompt: Optional[str] = None,
        base_prompt: Optional[str] = None,
        media_overrides: Optional[dict] = None,
        summary_prompt: Optional[str] = None,
        action_type: Optional[AgentActionType] = None,
        trigger_type: Optional[str] = None,
        trigger_config: Optional[dict] = None,
        max_tokens: Optional[int] = None,
        output_schema: Optional[dict] = None,
        is_active: bool = True,
        is_default: bool = False,
        analyze_type: Optional[str] = None,
        llm_strategy: Optional[str] = None,
        text_llm_model_id: Optional[int] = None,
        image_llm_model_id: Optional[int] = None,
        video_llm_model_id: Optional[int] = None,
        tenant_id: Optional[int] = None,
    ) -> AgentScenario:
        """
        Create a new agent scenario.

        Args:
            name: Scenario name
            description: Scenario description
            analysis_types: List of analysis type names (e.g., [“sentiment”, “trends”])
            content_types: List of content type values (e.g., [“posts”, “comments”])
            scope: Configuration parameters for analysis (no analysis_types here!)
            ai_prompt: [Deprecated] Legacy single prompt, maps to base_prompt
            base_prompt: Core LLM instruction for analysis (media-agnostic)
            media_overrides: Per-media prompt overrides: {"image": "...", "video": "..."}
            summary_prompt: Custom prompt for the unified summary
            max_tokens: Max tokens for LLM responses
            output_schema: JSON Schema for structured output
            is_active: Whether scenario is active
            is_default: Whether this is the tenant's default scenario

        Returns:
            Created AgentScenario object

        Raises:
            PlanLimitError: the workspace tier has no room for another scenario.
        """
        # Tier check before the insert, so a refused scenario leaves no row. The
        # service is the single funnel every scenario creation passes through
        # (REST, admin, CLI), which is why the check lives here rather than in
        # one endpoint that the others would bypass.
        blocked = await _scenario_limit_reason()
        if blocked:
            raise PlanLimitError(blocked)

        scenario = await AgentScenario.objects.create(
            name=name,
            description=description,
            tenant_id=tenant_id,
            analysis_types=analysis_types or [],
            content_types=content_types or [],
            scope=scope or {},
            base_prompt=base_prompt or ai_prompt,
            media_overrides=media_overrides or {},
            summary_prompt=summary_prompt,
            max_tokens=max_tokens,
            output_schema=output_schema,
            is_active=is_active,
            is_default=is_default,
            analyze_type=analyze_type,
            llm_strategy=llm_strategy,
            text_llm_model_id=text_llm_model_id,
            image_llm_model_id=image_llm_model_id,
            video_llm_model_id=video_llm_model_id,
        )

        logger.info(
            f"Created scenario: {name} (ID: {scenario.id}), analysis: {analysis_types}, content: {content_types}"
        )
        return scenario

    async def get_scenario_by_id(self, scenario_id: int) -> Optional[AgentScenario]:
        """Get scenario by ID."""
        return await AgentScenario.objects.get(id=scenario_id)

    async def update_scenario(self, scenario_id: int, **updates) -> Optional[AgentScenario]:
        """
        Update a bot scenario.

        Args:
                scenario_id: Scenario ID
                **updates: Fields to update

        Returns:
                Updated AgentScenario object or None if not found
        """
        scenario = await AgentScenario.objects.update_by_id(scenario_id, **updates)

        if scenario:
            logger.info(f"Updated bot scenario {scenario_id}")
        else:
            logger.warning(f"Scenario {scenario_id} not found")

        return scenario

    async def delete_scenario(self, scenario_id: int) -> bool:
        """
        Delete a bot scenario.

        Args:
                scenario_id: Scenario ID

        Returns:
                True if deleted, False if not found
        """
        scenario = await AgentScenario.objects.get(id=scenario_id)

        if scenario:
            await AgentScenario.objects.delete(scenario.id)
            logger.info(f"Deleted bot scenario {scenario_id}")
            return True

        logger.warning(f"Scenario {scenario_id} not found")
        return False

    async def get_active_scenarios(self) -> list[AgentScenario]:
        """Get all active scenarios."""
        return await AgentScenario.objects.filter(is_active=True)

    async def toggle_scenario_status(self, scenario_id: int, is_active: bool) -> Optional[AgentScenario]:
        """
        Toggle scenario active status.

        Args:
                scenario_id: Scenario ID
                is_active: New active status

        Returns:
                Updated AgentScenario object or None if not found
        """
        return await self.update_scenario(scenario_id, is_active=is_active)


# Convenience singleton-like helper
scenario_service = ScenarioService()
