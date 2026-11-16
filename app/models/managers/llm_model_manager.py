import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..analysis.agent_scenario import AgentScenario
    from ..analysis.ai_analytics import AIAnalytics
    from ..llm.llm_model import LLMModel
    from ..llm.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

_EPOCH_MIN = datetime(1, 1, 1, tzinfo=timezone.utc)


class LLMModelManager(BaseManager):
    """Manager for LLMModel operations."""

    def __init__(self):
        from ..llm.llm_model import LLMModel

        super().__init__(LLMModel)

    async def get_active_models(self) -> list["LLMModel"]:
        """Get all active LLM models."""
        return await self.filter(is_active=True)

    async def zero_price_active_models(self, limit: int = 10) -> dict:
        """Active models whose ordinary tariff is 0/0.

        The catalog treats an explicit zero as FREE, but the admin form also
        starts new models at 0.0 — so this is the operator's review list, not a
        claim that every row is unpriced. No cache rates or provider secrets
        are returned, only the provider name and model identity.
        """
        from sqlalchemy import func

        if type(limit) is not int or not 0 < limit <= 50:
            raise ValueError("Zero-price model limit must be between 1 and 50")
        zero = self.filter(
            is_active=True,
            input_cost_per_1k=0,
            output_cost_per_1k=0,
        )
        total = await zero.count()
        rows = await zero.prefetch_related("provider").order_by(self.model.name).limit(limit)
        return {
            "total": int(total),
            "models": [
                {
                    "provider": getattr(getattr(model, "provider", None), "name", "—"),
                    "model_id": model.model_id,
                    "name": model.name,
                    "is_default": bool(model.is_default),
                }
                for model in rows
            ],
        }

    async def capability_candidates(
        self, capability: str, *, exclude_id: int | None = None, limit: int = 3
    ) -> list["LLMModel"]:
        """Ordered active models handling this capability: default first, the rest after.

        The analyzer's text-stage failover walks this bounded queue when the
        scenario's model fails: a provider transport failure or unusable output
        must not zero a whole batch while the fleet has a working replacement.
        Classifiers and models that never once succeeded stay excluded, so a
        fallback cannot burn another run on a known-dead pick (the same
        suitability discipline the auto-resolve path already applies).
        """
        from app.core.personal_catalog_context import current_catalog_owner_id
        from app.services.ai.llm_client import _allowed_model_types
        from app.services.ai.model_suitability import analysis_capable
        from app.services.ai.personal_catalog import eligible_owned_model

        default = await self.resolve_default_model(capability)
        owner_id = current_catalog_owner_id()
        allowed_types = await _allowed_model_types()
        candidates: list["LLMModel"] = []
        for model in await self._get_by_capability(capability):
            if exclude_id is not None and model.id == exclude_id:
                continue
            if any(model.id == kept.id for kept in candidates):
                continue
            if not model.is_active or not model.provider or not model.provider.is_active:
                continue
            # The failover queue is a routing decision too: it must not offer a
            # model whose key belongs to another owner.
            if not eligible_owned_model(
                model, user_id=owner_id, capabilities={capability}, allowed_types=allowed_types
            ):
                continue
            if not analysis_capable(model):
                continue
            candidates.append(model)
        # Proven models first: a fleet walk starts with what actually answered
        # before (use_count - fail_count, then most recently used), so a
        # fallback does not re-probe free-heap models that never worked.
        candidates.sort(
            key=lambda model: (
                model.id == (default.id if default else None),
                (model.use_count or 0) - (model.fail_count or 0),
                model.last_used_at or _EPOCH_MIN,
            ),
            reverse=True,
        )
        return candidates[: max(1, limit)]

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
            models = await self.select_related("provider").filter(provider_id=provider_id, is_active=True)

            from app.core.personal_catalog_context import current_catalog_owner_id
            from app.services.ai.personal_catalog import eligible_owned_model

            owner_id = current_catalog_owner_id()
            for model in models:
                if not model.can_handle(capability):
                    continue
                if not eligible_owned_model(model, user_id=owner_id, capabilities={capability}):
                    continue
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
        """Use the shared active-provider default resolver."""
        return await self.resolve_default_model(capability)

    # ──────────────────────────────────────────────────────────────
    # Phase 1 — Single source of truth for lifecycle
    # ──────────────────────────────────────────────────────────────

    async def set_default(self, model_id: int) -> Optional["LLMModel"]:
        """Make `model_id` the only default among the models it overlaps.

        Uniqueness is per capability, not per `model_type` string: resolution
        filters candidates with `can_handle()`, so a "text,video" model and a
        "text" model both answer a "text" request — two defaults among them
        fork the fleet into a provider-priority lottery. Every model sharing
        at least one capability with this one loses the flag, which leaves
        exactly one default per capability.

        The single owner of the rule: `create_model`, `update_model`, the
        default reassignment on delete and the sqladmin save hook all call it.
        Returns the model, or None when it does not exist. Idempotent.
        """
        from app.core.personal_catalog_context import current_catalog_owner_id
        from app.services.ai.personal_catalog import set_default_model
        await set_default_model(user_id=current_catalog_owner_id(), model_id=model_id)
        return await self.select_related("provider").get(id=model_id)

    async def create_model(
        self,
        *,
        provider_id: int,
        name: str,
        model_id: str,
        model_type: str = "text",
        description: str | None = None,
        custom_endpoint_path: str | None = None,
        input_cost_per_1k: float = 0.0,
        output_cost_per_1k: float = 0.0,
        pricing_token_unit: int = 1_000_000,
        cache_read_cost_per_1m: Decimal | None = None,
        cache_write_5m_cost_per_1m: Decimal | None = None,
        cache_write_1h_cost_per_1m: Decimal | None = None,
        max_tokens: int = 4096,
        default_temperature: float = 0.3,
        is_active: bool = True,
        is_default: bool = False,
    ) -> "LLMModel":
        """
        Create a new LLM model.

        If is_default=True, the new model becomes the only default among the
        models it overlaps (see `set_default`).
        """
        from app.core.personal_catalog_context import current_catalog_owner_id
        from app.services.ai.personal_catalog import save_provider_bundle
        fields = dict(
            name=name, model_id=model_id, model_type=model_type, description=description,
            custom_endpoint_path=custom_endpoint_path, input_cost_per_1k=input_cost_per_1k,
            output_cost_per_1k=output_cost_per_1k, pricing_token_unit=pricing_token_unit,
            cache_read_cost_per_1m=cache_read_cost_per_1m, cache_write_5m_cost_per_1m=cache_write_5m_cost_per_1m,
            cache_write_1h_cost_per_1m=cache_write_1h_cost_per_1m, max_tokens=max_tokens,
            default_temperature=default_temperature, is_active=is_active, is_default=is_default,
        )
        result = await save_provider_bundle(
            user_id=current_catalog_owner_id(), provider={"id": provider_id}, models=[fields]
        )
        # New IDs are in insertion order; never resolve a personal model by name.
        identity = result["saved_model_ids"][0]
        return await self.select_related("provider").get(id=identity)

    async def update_model(self, model_id: int, **fields) -> Optional["LLMModel"]:
        """
        Partially update a model.

        A model that is (or stays) the default keeps exactly one default per
        capability — see `set_default`, which also self-heals rows written
        before the rule existed.
        """
        from app.core.personal_catalog_context import current_catalog_owner_id
        from app.services.ai.personal_catalog import save_provider_bundle
        model = await self.get(id=model_id)
        if model is None:
            return None
        await save_provider_bundle(
            user_id=current_catalog_owner_id(), provider={"id": model.provider_id}, models=[{"id": model_id, **fields}]
        )
        return await self.select_related("provider").get(id=model_id)

    async def delete_with_default_reassignment(self, model_id: int) -> dict:
        """
        Delete a model and reassign default if the deleted model was default.

        The replacement default is chosen inside the acting owner's catalog, by
        the model that carries at least the deleted model's capabilities.

        Rows that reference the deleted model lose the reference (FK SET NULL).
        Agent scenarios and workspaces that lose their model are counted before
        the delete; the count is committed with the delete.

        Returns:
            {
                "deleted": model_id,
                "new_default_id": int | None,
                "new_default_name": str | None,
                "scenarios_reset": int,
                "warnings": list[str]
            }

        `warnings` reports what the delete could not repair: no model of the
        same type left in the catalog, or workspaces whose agent model was reset.
        """
        from sqlalchemy import func, or_, select
        from app.core.personal_catalog_context import current_catalog_owner_id
        from app.services.ai.personal_catalog import lock_owner, require_actor, set_default_in_session
        from ..llm.llm_model import LLMModel
        from sqlalchemy.orm import selectinload

        owner_id = current_catalog_owner_id()
        require_actor(owner_id)
        async with self._get_owned_session() as session:
            await lock_owner(session, owner_id)
            model = await self.get(id=model_id, owner_user_id=owner_id, session=session)
            if model is None:
                raise ValueError("Model unavailable")
            from ..analysis.agent_scenario import AgentScenario
            from ..tenancy import Tenant

            model_type = model.model_type
            was_default, caps = model.is_default, set(model.capabilities)
            scenario_refs = [
                AgentScenario.text_llm_model_id == model_id,
                AgentScenario.image_llm_model_id == model_id,
                AgentScenario.video_llm_model_id == model_id,
            ]
            # A scenario that names the model in two media columns loses both links.
            reset_scenarios = (
                await session.execute(select(func.count()).select_from(AgentScenario).where(or_(*scenario_refs)))
            ).scalar_one()
            reset_workspaces = (
                await session.execute(select(func.count()).select_from(Tenant).where(Tenant.agent_model_id == model_id))
            ).scalar_one()
            await session.delete(model)
            await session.flush()
            candidates = (
                await session.execute(
                    select(LLMModel)
                    .where(LLMModel.owner_user_id == owner_id, LLMModel.is_active.is_(True))
                    .options(selectinload(LLMModel.provider))
                    .order_by(LLMModel.id)
                )
            ).scalars().all()
            replacement = next((m for m in candidates if m.provider.is_active and caps <= set(m.capabilities)), None)
            if was_default and replacement is not None:
                await set_default_in_session(session, replacement)
            warnings: list[str] = []
            if was_default and replacement is None:
                warnings.append(f"no model left for type {model_type}")
            if reset_workspaces:
                warnings.append(f"{reset_workspaces} workspace(s) lost their agent model")
            result = {
                "deleted": model_id,
                "new_default_id": replacement.id if was_default and replacement else None,
                "new_default_name": replacement.name if was_default and replacement else None,
                "scenarios_reset": int(reset_scenarios),
                "warnings": warnings,
            }
        return result

    async def _find_default_candidate(self, model_type: str, exclude_id: int | None = None) -> Optional["LLMModel"]:
        """
        Find the best candidate for default of a given model_type.

        Order:
        1. Same type, active, has last_success_at → most recent
        2. Same type, active, name in distinct ai_analytics.llm_model
        3. Same type, active
        """
        from ..analysis.ai_analytics import AIAnalytics
        from app.services.ai.llm_client import default_model_sort_key

        # Get all active models of this type with provider prefetched
        # Reassignment preserves the exact model type and active provider.
        models = await self.select_related("provider").filter(model_type=model_type, is_active=True)
        models = [m for m in models if m.id != exclude_id and m.provider and m.provider.is_active]
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
                analytics_models = (
                    await AIAnalytics.objects.filter(llm_model__isnull=False)
                    .values_list("llm_model", flat=True)
                    .distinct()
                )
                analytics_model_names = set(analytics_models)
                for m in models:
                    if m.name in analytics_model_names or m.model_id in analytics_model_names:
                        return m
            except Exception:
                pass  # Fall through to strategy 3

        # Strategy 3: any active same type (priority: model default flag, id)
        models.sort(key=lambda m: default_model_sort_key(m))
        return models[0] if models else None

    async def resolve_default_model(self, model_type: str, strategy: str | None = None) -> Optional["LLMModel"]:
        """Resolve a capability to a model on an active provider.

        Candidates are restricted to the acting personal catalog for every
        capability, not only chat: a media stage decrypts the same provider key.
        Without a strategy and for quality, the model flagged as default leads.
        Cost-efficient routing minimizes the combined per-1K tariff, using
        defaults only to break tariff ties. Multimodal prefers capabilities
        within equal default ranks. Price is not a proxy for quality.
        """
        from app.core.personal_catalog_context import current_catalog_owner_id
        from app.services.ai.llm_client import _allowed_model_types, default_model_sort_key
        from app.services.ai.personal_catalog import CHAT_CAPABILITIES, eligible_owned_model
        from app.utils.enum_helpers import get_enum_value

        strategy_value = get_enum_value(strategy)
        if strategy_value not in ("", "cost_efficient", "quality", "multimodal"):
            raise ValueError(f"Unknown LLM strategy: {strategy_value}")
        models = await self.select_related("provider").filter(is_active=True)
        # Every capability resolves inside the acting personal catalog. Text,
        # image, video and audio stages all decrypt the provider key on the
        # attempt, so an owner-agnostic candidate list would be a cross-user key
        # use, not a routing preference. The tier gate is per capability:
        # `starter` buys text, not vision.
        needed = CHAT_CAPABILITIES if model_type == "text" else frozenset({model_type})
        owner_id = current_catalog_owner_id()
        allowed_types = await _allowed_model_types()
        models = [
            model
            for model in models
            if eligible_owned_model(model, user_id=owner_id, capabilities=needed, allowed_types=allowed_types)
        ]
        if not models:
            return None

        def sort_key(model):
            defaults = default_model_sort_key(model)
            if strategy_value == "cost_efficient":
                preference = (model.input_cost_per_1k or 0) + (model.output_cost_per_1k or 0)
            elif strategy_value == "multimodal":
                preference = -len(model.capabilities)
            else:
                preference = 0
            if strategy_value == "cost_efficient":
                return (preference, *defaults)
            return (defaults[0], preference, defaults[1])

        return min(models, key=sort_key)
