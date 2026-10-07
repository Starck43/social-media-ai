import logging
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..llm_model import LLMModel
    from ..llm_provider import LLMProvider
    from ..ai_analytics import AIAnalytics
    from ..agent_scenario import AgentScenario

logger = logging.getLogger(__name__)


class LLMModelManager(BaseManager):
    """Manager for LLMModel operations."""

    def __init__(self):
        from ..llm_model import LLMModel

        super().__init__(LLMModel)

    async def get_active_models(self) -> list["LLMModel"]:
        """Get all active LLM models."""
        return await self.filter(is_active=True)

    async def get_models_by_provider(self, provider_id: int, is_active: bool = True) -> list["LLMModel"]:
        """
        Get all models for a specific provider.

        Args:
                provider_id: ID of the LLM provider
                is_active: Filter by active status

        Returns:
                List of LLMModel objects
        """
        filters = {"provider_id": provider_id}
        if is_active:
            filters["is_active"] = True

        return await self.filter(**filters)

    async def get_model_for_capability(self, capability: str, provider_id: int = None) -> Optional["LLMModel"]:
        """
        Get the best model for a specific capability.

        Args:
                capability: Capability to filter by (text, image, video)
                provider_id: Optional provider ID to limit search

        Returns:
                LLMModel object or None
        """
        if provider_id:
            models = await self.get_models_by_provider(provider_id=provider_id)

            for model in models:
                if model.can_handle(capability):
                    logger.info(f"Using model for {capability}: {model.name}")
                    return model
        else:
            return await self._get_default_for_capability(capability)

        logger.warning(
            f"No active model found for {capability}" + (f" in provider {provider_id}" if provider_id else "")
        )
        return None

    async def _get_by_capability(self, capability: str, is_active: bool = True) -> list["LLMModel"]:
        """
        Get all LLM models that support a specific capability.

        Args:
                capability: Capability to filter by (text, image, video)
                is_active: Filter by active status

        Returns:
                List of LLMModel objects
        """
        if is_active:
            all_models = await self.select_related("provider").filter(is_active=True)
        else:
            all_models = await self.select_related("provider").all()

        models = [m for m in all_models if m.capabilities and capability in m.capabilities]
        return models

    async def _get_default_for_capability(self, capability: str) -> Optional["LLMModel"]:
        """
        Get default model for a specific capability (without provider restriction).

        Priority: provider.is_default → model.is_default → model.id (lowest first).
        """
        models = await self._get_by_capability(capability, is_active=True)
        if not models:
            logger.warning(f"No active LLM model found for {capability}")
            return None
        models.sort(key=lambda m: (not m.provider.is_default, not m.is_default, m.id))
        logger.info(f"Resolved model for {capability}: {models[0].provider.name}/{models[0].model_id}")
        return models[0]

    # ──────────────────────────────────────────────────────────────
    # Phase 1 — Single source of truth for lifecycle
    # ──────────────────────────────────────────────────────────────

    async def create_model(
        self,
        *,
        provider_id: int,
        name: str,
        model_id: str,
        model_type: str = "text",
        description: str | None = None,
        input_cost_per_1k: float = 0.0,
        output_cost_per_1k: float = 0.0,
        max_tokens: int = 4096,
        default_temperature: float = 0.3,
        is_active: bool = True,
        is_default: bool = False,
    ) -> "LLMModel":
        """
        Create a new LLM model.

        If is_default=True, clears other defaults of the same model_type (global uniqueness).
        """
        from ..llm_provider import LLMProvider

        provider = await LLMProvider.objects.get(id=provider_id)
        if not provider:
            raise ValueError(f"Provider {provider_id} not found")
        if not provider.is_active:
            raise ValueError(f"Provider {provider_id} is not active")

        if is_default:
            # Clear other defaults of the same model_type (exact match)
            # Note: This assumes that a model is a default for its *exact* set of capabilities.
            await self.filter(model_type=model_type, is_default=True).update(is_default=False)

        model = await self.create(
            provider_id=provider_id,
            name=name,
            model_id=model_id,
            model_type=model_type,
            description=description,
            input_cost_per_1k=input_cost_per_1k,
            output_cost_per_1k=output_cost_per_1k,
            max_tokens=max_tokens,
            default_temperature=default_temperature,
            is_active=is_active,
            is_default=is_default,
        )
        logger.info(f"Created LLM model {model.name} (ID: {model.id}, default={model.is_default})")
        return model

    async def update_model(self, model_id: int, **fields) -> Optional["LLMModel"]:
        """
        Partially update a model. If is_default=True, enforces uniqueness per model_type.
        """
        model = await self.get(id=model_id)
        if not model:
            return None

        if fields.get("is_default") is True and not model.is_default:
            scope_type = fields.get("model_type", model.model_type)
            # Clear other defaults of the same model_type (exact match)
            await self.filter(model_type=scope_type, is_default=True).update(is_default=False)

        await self.update_by_id(model_id, **fields)
        updated = await self.get(id=model_id)
        logger.info(f"Updated LLM model {updated.name} (ID: {updated.id}, default={updated.is_default})")
        return updated

    async def delete_with_default_reassignment(self, model_id: int) -> dict:
        """
        Delete a model and reassign default if the deleted model was default.

        Candidate order (same model_type, active):
        1. last_success_at desc (most recent success)
        2. name present in distinct ai_analytics.llm_model (legacy signal)
        3. any active same type
        4. none → warning

        Also: scenarios referencing the model get FK SET NULL (count them).

        Returns:
            {
                "deleted": model_id,
                "new_default_id": int | None,
                "new_default_name": str | None,
                "scenarios_reset": int,
                "warnings": list[str]
            }
        """
        from ..llm_model import LLMModel
        from ..agent_scenario import AgentScenario
        from ..ai_analytics import AIAnalytics

        model = await self.get(id=model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")

        was_default = model.is_default
        model_type = model.model_type
        warnings: list[str] = []

        # Count scenarios that reference this model
        scenarios_reset = 0
        for field_name in ("text_llm_model_id", "image_llm_model_id", "video_llm_model_id"):
            count = await AgentScenario.objects.filter(**{field_name: model_id}).count()
            if count:
                scenarios_reset += count

        # Delete the model (FK SET NULL on scenarios is handled by DB)
        await self.delete(id=model_id)

        new_default_id = None
        new_default_name = None

        if was_default:
            # Find candidate for new default
            candidate = await self._find_default_candidate(model_type)
            if candidate:
                candidate.is_default = True
                await self.update_by_id(candidate.id, is_default=True)
                new_default_id = candidate.id
                new_default_name = candidate.name
                logger.info(f"Reassigned default for type {model_type} to {candidate.name} (ID: {candidate.id})")
            else:
                warning = f"no model left for type {model_type}"
                warnings.append(warning)
                logger.warning(warning)

        return {
            "deleted": model_id,
            "new_default_id": new_default_id,
            "new_default_name": new_default_name,
            "scenarios_reset": scenarios_reset,
            "warnings": warnings,
        }

    async def _find_default_candidate(self, model_type: str) -> Optional["LLMModel"]:
        """
        Find the best candidate for default of a given model_type.

        Order:
        1. Same type, active, has last_success_at → most recent
        2. Same type, active, name in distinct ai_analytics.llm_model
        3. Same type, active
        """
        from ..ai_analytics import AIAnalytics

        # Get all active models of this type with provider prefetched
        # Use contains to find models that support this capability
        models = await self.select_related("provider").filter(model_type__contains=model_type, is_active=True)
        if not models:
            return None

        # Strategy 1: last_success_at desc (need to fetch from DB)
        # We'll do this in Python after fetching the models with their last_success_at
        # Since last_success_at is a column on LLMModel, it's already loaded

        # Check if any model has last_success_at
        models_with_success = [m for m in models if m.last_success_at is not None]
        if models_with_success:
            # Sort by last_success_at desc
            models_with_success.sort(key=lambda m: m.last_success_at, reverse=True)
            return models_with_success[0]

        # Strategy 2: name appears in ai_analytics.llm_model
        if hasattr(AIAnalytics, "llm_model"):
            try:
                analytics_models = await AIAnalytics.objects.filter(llm_model__isnull=False).values_list("llm_model", flat=True).distinct()
                analytics_model_names = set(analytics_models)
                for m in models:
                    if m.name in analytics_model_names:
                        return m
            except Exception:
                pass  # Fall through to strategy 3

        # Strategy 3: any active same type (priority: provider.is_default, model.is_default, id)
        models.sort(key=lambda m: (not m.provider.is_default, not m.is_default, m.id))
        return models[0] if models else None

    async def resolve_default_model(self, model_type: str) -> Optional["LLMModel"]:
        """
        Single entry point for default model resolution.
        Priority: is_default → first active (same ordering as _get_default_for_capability).
        """
        # Use contains to find models that support this capability
        models = await self.select_related("provider").filter(model_type__contains=model_type, is_active=True)
        if not models:
            return None
        models.sort(key=lambda m: (not m.provider.is_default, not m.is_default, m.id))
        return models[0]
