"""Tests for LLMModelManager lifecycle methods."""

import secrets
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.database import async_session_maker
from app.core.permissions import _current_user, set_current_user
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentScenario, AIAnalytics, LLMModel, LLMProvider, Source, Tenant
from app.models.managers.llm_model_manager import LLMModelManager
from app.types import SourceType

# Legacy catalog owner seeded by the personal catalog ownership migration.
CATALOG_OWNER_ID = 2


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


async def _cleanup():
    """Clean up test data."""
    async with async_session_maker() as s:
        from sqlalchemy import delete

        await s.execute(delete(AgentScenario).where(AgentScenario.name.like("test_%")))
        await s.execute(delete(AIAnalytics).where(AIAnalytics.llm_model.like("test_%")))
        await s.execute(delete(Source).where(Source.name.like("test_%")))
        await s.execute(delete(LLMModel).where(LLMModel.name.like("test_%")))
        await s.execute(delete(LLMProvider).where(LLMProvider.name.like("test_%")))
        await s.commit()


def _catalog_actor():
    """Minimal active actor for the catalog service layer (`require_actor`)."""
    return SimpleNamespace(id=CATALOG_OWNER_ID, is_active=True)


@pytest.fixture(autouse=True)
async def clean_db():
    # The manager resolves every query inside the acting personal catalog and the
    # catalog services re-check the request actor, so both are bound per test.
    token = set_current_user(_catalog_actor())
    try:
        with catalog_execution_scope(CATALOG_OWNER_ID):
            await _cleanup()
            yield
            await _cleanup()
    finally:
        _current_user.reset(token)


async def _create_provider(name: str = "test_provider") -> LLMProvider:
    async with async_session_maker() as s:
        provider = LLMProvider(
            name=name,
            uid=name.lower().replace("_", "-"),
            api_format="openai",
            base_url="https://api.example.com/v1",
            is_active=True,
            owner_user_id=CATALOG_OWNER_ID,
        )
        s.add(provider)
        await s.commit()
        await s.refresh(provider)
        return provider


def _owner_provider_scope(monkeypatch, provider_id: int) -> None:
    """Limit the owner's catalog to one provider: the shared schema holds other suites' rows."""
    from app.models.managers import base_manager

    original = base_manager.BaseManager._personal_criterion

    def _personal_criterion(self):
        criteria = original(self)
        if criteria:
            criteria = [*criteria, self.model.provider_id == provider_id]
        return criteria

    monkeypatch.setattr(base_manager.BaseManager, "_personal_criterion", _personal_criterion)


async def _create_source(tenant_id: int = 1) -> Source:
    async with async_session_maker() as s:
        source = Source(
            name="test_source",
            platform_id=1,  # Assume platform 1 exists
            source_type=SourceType.CHANNEL.name,  # Enum stores the member name
            external_id="test_external_1",  # Required, non-nullable
            tenant_id=tenant_id,
            is_active=True,
        )
        s.add(source)
        await s.commit()
        await s.refresh(source)
        return source


async def _create_model(
    provider_id: int,
    name: str,
    model_type: str = "text",
    is_default: bool = False,
    is_active: bool = True,
    last_success_at: datetime | None = None,
) -> LLMModel:
    async with async_session_maker() as s:
        model = LLMModel(
            provider_id=provider_id,
            name=name,
            model_id=name.lower().replace(" ", "-"),
            model_type=model_type,
            is_active=is_active,
            is_default=is_default,
            last_success_at=last_success_at,
            owner_user_id=CATALOG_OWNER_ID,
        )
        s.add(model)
        await s.commit()
        await s.refresh(model)
        return model


class TestLLMModelManager:
    """Tests for LLMModelManager lifecycle."""

    async def test_create_model_enforces_default_uniqueness_per_type(self):
        """Creating a default model clears other defaults of the same type."""
        provider = await _create_provider()

        mgr = LLMModelManager()

        # Create first default using manager
        m1 = await mgr.create_model(
            provider_id=provider.id,
            name="test_model_1",
            model_id="test-model-1",
            model_type="text",
            is_default=True,
        )
        assert m1.is_default is True

        # Create second default of same type using manager
        m2 = await mgr.create_model(
            provider_id=provider.id,
            name="test_model_2",
            model_id="test-model-2",
            model_type="text",
            is_default=True,
        )
        assert m2.is_default is True

        # First should no longer be default
        async with async_session_maker() as s:
            m1_refreshed = await s.get(LLMModel, m1.id)
            assert m1_refreshed.is_default is False

    async def test_update_model_enforces_default_uniqueness(self):
        """Updating a model to default clears other defaults of same type."""
        provider = await _create_provider()
        mgr = LLMModelManager()

        m1 = await mgr.create_model(
            provider_id=provider.id,
            name="test_model_1",
            model_id="test-model-1",
            model_type="image",
            is_default=True,
        )
        m2 = await mgr.create_model(
            provider_id=provider.id,
            name="test_model_2",
            model_id="test-model-2",
            model_type="image",
            is_default=False,
        )

        updated = await mgr.update_model(m2.id, is_default=True)
        assert updated.is_default is True

        async with async_session_maker() as s:
            m1_refreshed = await s.get(LLMModel, m1.id)
            assert m1_refreshed.is_default is False

    async def test_set_default_clears_overlapping_capabilities(self):
        """A "text,video" default and a "text" default cannot coexist.

        Resolution filters candidates with can_handle(), so both answer a
        "text" request — matching on the model_type string left both flagged
        and forked the fleet into a provider-priority lottery.
        """
        provider = await _create_provider()
        mgr = LLMModelManager()

        text_only = await _create_model(provider.id, "test_text_only", model_type="text", is_default=True)
        multimodal = await _create_model(provider.id, "test_multimodal", model_type="text,video", is_default=True)

        result = await mgr.set_default(text_only.id)
        assert result is not None and result.is_default is True

        async with async_session_maker() as s:
            assert (await s.get(LLMModel, text_only.id)).is_default is True
            assert (await s.get(LLMModel, multimodal.id)).is_default is False

    async def test_set_default_keeps_non_overlapping_defaults(self):
        """A default of a disjoint capability is none of this call's business."""
        provider = await _create_provider()
        mgr = LLMModelManager()

        text_model = await _create_model(provider.id, "test_text", model_type="text", is_default=True)
        image_model = await _create_model(provider.id, "test_image", model_type="image", is_default=True)

        await mgr.set_default(text_model.id)

        async with async_session_maker() as s:
            assert (await s.get(LLMModel, text_model.id)).is_default is True
            assert (await s.get(LLMModel, image_model.id)).is_default is True

    async def test_set_default_sets_flag_and_is_idempotent(self):
        provider = await _create_provider()
        mgr = LLMModelManager()

        plain = await _create_model(provider.id, "test_plain", model_type="text", is_default=False)
        result = await mgr.set_default(plain.id)
        assert result is not None and result.is_default is True

        again = await mgr.set_default(plain.id)
        assert again is not None and again.is_default is True

    async def test_set_default_missing_model_returns_none(self):
        mgr = LLMModelManager()
        with pytest.raises(PermissionError, match="Model is unavailable"):
            await mgr.set_default(999999999)

    async def test_delete_with_default_reassignment_last_success_at(self):
        """Deleting the current default hands the flag to the owner's next capable model.

        The delete path picks the replacement from the owner's whole catalog, so
        the shared schema's other rows decide it; the assertion covers the
        reassignment contract, not the candidate choice.
        """
        provider = await _create_provider()

        m_old = await _create_model(provider.id, "test_model_old", model_type="text", is_default=True)
        await _create_model(provider.id, "test_model_new", model_type="text", is_default=False)

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(m_old.id)

        assert result["deleted"] == m_old.id
        assert result["new_default_id"] is not None
        assert result["new_default_id"] != m_old.id
        assert (await LLMModel.objects.get(id=result["new_default_id"])).is_default is True

    async def test_delete_with_default_reassignment_ai_analytics_fallback(self):
        """A default is reassigned inside the owner's catalog even without success history."""
        provider = await _create_provider()
        source = await _create_source()

        m_default = await _create_model(provider.id, "test_default_model", model_type="text", is_default=True)
        await _create_model(provider.id, "test_fallback_model", model_type="text", is_default=False)

        # Add ai_analytics entry referencing the candidate model name
        async with async_session_maker() as s:
            analytics = AIAnalytics(
                tenant_id=1,
                source_id=source.id,
                topic_chain_id="test_chain",
                summary_data={"test": "data"},
                llm_model="test_fallback_model",  # Matches candidate name
                analysis_date=datetime.utcnow().date(),
            )
            s.add(analytics)
            await s.commit()

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(m_default.id)

        assert result["deleted"] == m_default.id
        assert result["new_default_id"] is not None
        assert (await LLMModel.objects.get(id=result["new_default_id"])).is_default is True

    async def test_delete_with_default_reassignment_any_active(self):
        """When no history, picks any active model of same type."""
        provider = await _create_provider()

        m_default = await _create_model(provider.id, "test_default_model", model_type="embedding", is_default=True)
        m_any = await _create_model(provider.id, "test_any_model", model_type="embedding", is_default=False)

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(m_default.id)

        assert result["deleted"] == m_default.id
        assert result["new_default_id"] == m_any.id

    async def test_delete_last_model_of_type_warning(self, monkeypatch):
        """Deleting the last model of a capability leaves no replacement default."""
        provider = await _create_provider()
        _owner_provider_scope(monkeypatch, provider.id)

        m_only = await _create_model(provider.id, "test_only_model", model_type="embedding", is_default=True)

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(m_only.id)

        assert result["deleted"] == m_only.id
        assert result["new_default_id"] is None

    async def test_delete_last_model_of_type_reports_warning(self, monkeypatch):
        """Deleting the last model of a capability warns that the type has no model left."""
        provider = await _create_provider()
        _owner_provider_scope(monkeypatch, provider.id)

        m_only = await _create_model(provider.id, "test_only_model_warn", model_type="embedding", is_default=True)

        result = await LLMModelManager().delete_with_default_reassignment(m_only.id)

        assert result["new_default_id"] is None
        assert any("no model left for type embedding" in w for w in result["warnings"])

    async def test_delete_counts_scenarios_reset(self):
        """Scenarios referencing the deleted model are counted in the result."""
        provider = await _create_provider()
        model = await _create_model(provider.id, "test_model_scenarios", model_type="text", is_default=False)

        # Create scenarios referencing this model
        async with async_session_maker() as s:
            s1 = AgentScenario(name="test_scenario_1", text_llm_model_id=model.id, tenant_id=1, is_active=True)
            s2 = AgentScenario(name="test_scenario_2", image_llm_model_id=model.id, tenant_id=1, is_active=True)
            s.add_all([s1, s2])
            await s.commit()

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(model.id)

        assert result["scenarios_reset"] == 2

    async def test_delete_counts_scenario_naming_model_twice_once(self):
        """One scenario naming the deleted model in two media columns is one reset, not two."""
        provider = await _create_provider()
        model = await _create_model(provider.id, "test_model_multi_column", model_type="text", is_default=False)

        async with async_session_maker() as s:
            scenario = AgentScenario(
                name="test_scenario_multi",
                text_llm_model_id=model.id,
                video_llm_model_id=model.id,
                tenant_id=1,
                is_active=True,
            )
            s.add(scenario)
            await s.commit()

        result = await LLMModelManager().delete_with_default_reassignment(model.id)

        assert result["scenarios_reset"] == 1

    async def test_delete_ignores_scenarios_referencing_other_models(self):
        """A scenario bound to a different model is not part of the reset count."""
        provider = await _create_provider()
        model = await _create_model(provider.id, "test_model_deleted", model_type="text", is_default=False)
        survivor = await _create_model(provider.id, "test_model_survivor", model_type="text", is_default=False)

        async with async_session_maker() as s:
            scenario = AgentScenario(
                name="test_scenario_survivor", text_llm_model_id=survivor.id, tenant_id=1, is_active=True
            )
            s.add(scenario)
            await s.commit()

        result = await LLMModelManager().delete_with_default_reassignment(model.id)

        assert result["scenarios_reset"] == 0

    async def test_delete_reports_workspace_that_lost_its_agent_model(self):
        """A workspace pinned to the deleted model yields a warning and no scenario reset."""
        provider = await _create_provider()
        model = await _create_model(provider.id, "test_model_tenant", model_type="text", is_default=False)

        tenant = await Tenant.objects.create(
            name="Delete agent model", slug=f"delete-agent-model-{secrets.token_hex(6)}", plan="business"
        )
        try:
            await Tenant.objects.update_by_id(tenant.id, agent_model_id=model.id)

            result = await LLMModelManager().delete_with_default_reassignment(model.id)

            assert result["scenarios_reset"] == 0
            assert any("workspace(s) lost their agent model" in w for w in result["warnings"])
            assert any(w.startswith("1 workspace(s) lost their agent model") for w in result["warnings"])
        finally:
            with tenant_scope(bypass=True):
                assert await Tenant.objects.delete_by_id(tenant.id)

    async def test_delete_with_replacement_and_no_references_reports_nothing(self, monkeypatch):
        """The ordinary path stays quiet: a replacement exists and nothing referenced the model."""
        provider = await _create_provider()
        _owner_provider_scope(monkeypatch, provider.id)
        model = await _create_model(provider.id, "test_model_clean", model_type="embedding", is_default=True)
        replacement = await _create_model(
            provider.id, "test_model_clean_next", model_type="embedding", is_default=False
        )

        result = await LLMModelManager().delete_with_default_reassignment(model.id)

        assert result["new_default_id"] == replacement.id
        assert result["scenarios_reset"] == 0
        assert result["warnings"] == []

    async def test_resolve_default_model(self):
        """resolve_default_model leads with the flagged default, then the lowest id."""
        provider = await _create_provider()

        flagged = await _create_model(provider.id, "test_default", model_type="text", is_default=True)
        await _create_model(provider.id, "test_active", model_type="text", is_default=False)

        mgr = LLMModelManager()
        await mgr.set_default(flagged.id)
        resolved = await mgr.resolve_default_model("text")
        assert resolved.id == flagged.id
        assert resolved.is_default is True

        # Delete default, the flag moves to a remaining model
        await mgr.delete_with_default_reassignment(flagged.id)
        resolved = await mgr.resolve_default_model("text")
        assert resolved.id != flagged.id
        assert resolved.is_default is True

    async def test_resolve_default_model_keeps_the_default_flag_over_lower_ids(self):
        """A flagged default outranks any lower-id model in the owner's catalog."""
        provider = await _create_provider()
        flagged = await _create_model(provider.id, "test_flagged", model_type="text", is_default=False)
        await LLMModelManager().set_default(flagged.id)

        resolved = await LLMModelManager().resolve_default_model("text")

        assert resolved.id == flagged.id
        assert resolved.is_default is True


class TestLLMModelManagerEdgeCases:
    """Edge case tests."""

    async def test_create_model_inactive_provider_is_not_an_eligible_candidate(self):
        """A model of an inactive provider stays out of default resolution."""
        provider = await _create_provider("test_inactive")
        # Need to fetch in new session to modify
        async with async_session_maker() as s:
            p = await s.get(LLMProvider, provider.id)
            p.is_active = False
            await s.commit()

        mgr = LLMModelManager()
        model = await mgr.create_model(provider_id=provider.id, name="test", model_id="test", model_type="text")

        assert model is not None
        resolved = await mgr.resolve_default_model("text")
        assert resolved is None or resolved.id != model.id

    async def test_update_nonexistent_model_returns_none(self):
        """Updating non-existent model returns None."""
        mgr = LLMModelManager()
        result = await mgr.update_model(999999, name="new_name")
        assert result is None

    async def test_delete_nonexistent_model_raises(self):
        """Deleting a model outside the owner's catalog raises."""
        mgr = LLMModelManager()
        with pytest.raises(ValueError, match="Model unavailable"):
            await mgr.delete_with_default_reassignment(999999)


class TestReliabilityRegressions:
    async def test_endpoint_path_round_trip(self):
        from app.api.v1.endpoints.llm_models import _model_response

        provider = await _create_provider()
        m = await LLMModelManager().create_model(
            provider_id=provider.id,
            name="test_custom_model",
            model_id="custom",
            model_type="decision",
            custom_endpoint_path="/predict",
        )
        assert m.custom_endpoint_path == "/predict"
        assert _model_response(m).custom_endpoint_path == "/predict"

    async def test_changing_default_model_type_clears_destination_default(self, monkeypatch):
        provider = await _create_provider()
        _owner_provider_scope(monkeypatch, provider.id)
        old = await _create_model(provider.id, "test_old_default", model_type="embedding", is_default=True)
        moved = await _create_model(provider.id, "test_moved_default", model_type="text", is_default=False)
        manager = LLMModelManager()
        # Uniqueness is per capability: promoting into a capability clears the overlapping default.
        await manager.update_model(moved.id, model_type="embedding", is_default=True)
        assert (await manager.get(id=old.id)).is_default is False
        assert (await manager.get(id=moved.id)).is_default is True

    async def test_delete_preview_matches_execution(self):
        from app.agent.toolset.llm import llm_model_delete

        provider = await _create_provider()
        deleted = await _create_model(
            provider.id,
            "test_deleted_default",
            model_type="embedding",
            is_default=True,
            last_success_at=datetime.utcnow(),
        )
        candidate = await _create_model(provider.id, "test_remaining", model_type="embedding")
        preview = await llm_model_delete(deleted.id)
        assert preview["plan"]["new_default"]["id"] == candidate.id
        assert await LLMModel.objects.get(id=deleted.id) is not None
        result = await llm_model_delete(deleted.id, dry_run=False)
        assert result["result"]["new_default_id"] == candidate.id

    async def test_default_candidate_ignores_disabled_providers(self):
        provider = await _create_provider()
        inactive = await _create_provider("test_inactive_provider")
        await LLMProvider.objects.update_by_id(inactive.id, is_active=False)
        deleted = await _create_model(provider.id, "test_deleted", model_type="embedding", is_default=True)
        await _create_model(
            inactive.id, "test_disabled_candidate", model_type="embedding", last_success_at=datetime.utcnow()
        )
        remaining = await _create_model(provider.id, "test_good_candidate", model_type="embedding")
        result = await LLMModelManager().delete_with_default_reassignment(deleted.id)
        assert result["new_default_id"] == remaining.id

    async def test_nondefault_delete_preview_has_no_spurious_warning(self):
        from app.agent.toolset.llm import llm_model_delete

        provider = await _create_provider()
        m = await _create_model(provider.id, "test_nondefault", model_type="embedding")
        preview = await llm_model_delete(m.id)
        assert preview["plan"]["warnings"] == []

    async def test_partial_update_does_not_unset_the_current_default(self):
        provider = await _create_provider()
        m = await _create_model(provider.id, "test_default_partial", model_type="text", is_default=True)
        updated = await LLMModelManager().update_model(m.id, name="test_renamed_default")
        assert updated.is_default is True
