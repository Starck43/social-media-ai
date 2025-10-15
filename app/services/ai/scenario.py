import logging
from typing import Optional, List

from app.models import BotScenario, Source
from app.types import BotActionType, BotTriggerType

logger = logging.getLogger(__name__)


class ScenarioPromptBuilder:
    """
    Helper class for building AI prompts from scenario configuration.
    
    This class handles:
    — Variable substitution in prompt templates
    — Merging scenario scope with default parameters
    — Building complete prompts ready for LLM consumption
    """

    @staticmethod
    def build_prompt(scenario: BotScenario, context: dict) -> str:
        """
        Build AI prompt from a scenario with runtime context injection.

        Args:
            scenario: BotScenario instance with analysis_types and scope
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
        """
        Build human-readable analysis instructions from a config.
        Used for logging and debugging.
        """
        instructions = []

        if 'sentiment' in analysis_types:
            cfg = config.get('sentiment_config', {})
            categories = cfg.get('categories', [])
            instructions.append(f"Sentiment analysis: {', '.join(categories)}")

        if 'trends' in analysis_types:
            cfg = config.get('trends_config', {})
            min_mentions = cfg.get('min_mentions', 5)
            instructions.append(f"Trends detection (min {min_mentions} mentions)")

        if 'engagement' in analysis_types:
            cfg = config.get('engagement_config', {})
            metrics = cfg.get('metrics', [])
            instructions.append(f"Engagement analysis: {', '.join(metrics)}")

        if 'keywords' in analysis_types:
            cfg = config.get('keywords_config', {})
            keywords = cfg.get('keywords', [])
            if keywords:
                instructions.append(f"Keywords tracking: {', '.join(keywords[:5])}")
            else:
                instructions.append("Keywords extraction")

        if 'topics' in analysis_types:
            cfg = config.get('topics_config', {})
            max_topics = cfg.get('max_topics', 5)
            instructions.append(f"Topics identification (top {max_topics})")

        if 'toxicity' in analysis_types:
            cfg = config.get('toxicity_config', {})
            threshold = cfg.get('threshold', 0.7)
            instructions.append(f"Toxicity detection (threshold: {threshold})")

        if 'demographics' in analysis_types:
            instructions.append("Demographics analysis")

        return '; '.join(instructions) if instructions else "General analysis"


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
        action_type: Optional[BotActionType] = None,
        is_active: bool = True,
        cooldown_minutes: int = 30,
    ) -> BotScenario:
        """
        Create a new bot scenario.

        Args:
            name: Scenario name
            description: Scenario description
            analysis_types: List of analysis type names (e.g., [“sentiment”, “trends”])
            content_types: List of content type values (e.g., [“posts”, “comments”])
            scope: Configuration parameters for analysis (no analysis_types here!)
            ai_prompt: AI prompt template with variables
            action_type: Action to perform after analysis
            is_active: Whether scenario is active
            cooldown_minutes: Cooldown period between triggers.

        Returns:
            Created BotScenario object
        """
        scenario = await BotScenario.objects.create(
            name=name,
            description=description,
            analysis_types=analysis_types or [],
            content_types=content_types or [],
            scope=scope or {},
            ai_prompt=ai_prompt,
            trigger_type=trigger_type,
            trigger_config=trigger_config or {},
            action_type=action_type,
            is_active=is_active,
            collection_interval_hours=collection_interval_hours,
        )

        logger.info(
            f"Created scenario: {name} (ID: {scenario.id}), analysis: {analysis_types}, content: {content_types}"
        )
        return scenario

    async def assign_scenario_to_source(self, source_id: int, scenario_id: Optional[int]) -> Optional[Source]:
        """
        Assign or remove a bot scenario from a source.

        Args:
                source_id: Source ID
                scenario_id: Bot scenario ID (None to remove)

        Returns:
                Updated Source object or None if not found
        """
        source = await Source.objects.assign_scenario(source_id, scenario_id)

        if source:
            action = "assigned" if scenario_id else "removed"
            logger.info(f"Scenario {action} for source {source_id}")
        else:
            logger.warning(f"Source {source_id} not found")

        return source

    async def get_sources_by_scenario(self, scenario_id: int, is_active: Optional[bool] = True) -> list[Source]:
        """
        Get all sources using a specific scenario.

        Args:
                scenario_id: Bot scenario ID
                is_active: Filter by active status

        Returns:
                List of Source objects
        """
        return await Source.objects.get_by_scenario(scenario_id, is_active)

    async def get_scenario_by_id(self, scenario_id: int) -> Optional[BotScenario]:
        """Get scenario by ID."""
        return await BotScenario.objects.get(id=scenario_id)

    async def update_scenario(self, scenario_id: int, **updates) -> Optional[BotScenario]:
        """
        Update a bot scenario.

        Args:
                scenario_id: Scenario ID
                **updates: Fields to update

        Returns:
                Updated BotScenario object or None if not found
        """
        scenario = await BotScenario.objects.update_by_id(scenario_id, **updates)

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
        scenario = await BotScenario.objects.get(id=scenario_id)

        if scenario:
            await BotScenario.objects.delete(scenario.id)
            logger.info(f"Deleted bot scenario {scenario_id}")
            return True

        logger.warning(f"Scenario {scenario_id} not found")
        return False

    async def get_active_scenarios(self) -> list[BotScenario]:
        """Get all active scenarios."""
        return await BotScenario.objects.filter(is_active=True)

    async def toggle_scenario_status(self, scenario_id: int, is_active: bool) -> Optional[BotScenario]:
        """
        Toggle scenario active status.

        Args:
                scenario_id: Scenario ID
                is_active: New active status

        Returns:
                Updated BotScenario object or None if not found
        """
        return await self.update_scenario(scenario_id, is_active=is_active)


# Convenience singleton-like helper
scenario_service = ScenarioService()
