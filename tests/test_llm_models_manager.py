"""Tests for LLMModelManager lifecycle methods."""
import pytest
from datetime import datetime, timedelta

from app.core.database import async_session_maker
from app.models import LLMModel, LLMProvider, AgentScenario, AIAnalytics, Source
from app.models.managers.llm_model_manager import LLMModelManager
from app.types import SourceType


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


@pytest.fixture(autouse=True)
async def clean_db():
    await _cleanup()
    yield
    await _cleanup()


async def _create_provider(name: str = "test_provider") -> LLMProvider:
    async with async_session_maker() as s:
        provider = LLMProvider(
            name=name,
            api_format="openai",
            base_url="https://api.example.com/v1",
            is_active=True,
            is_default=False,
        )
        s.add(provider)
        await s.commit()
        await s.refresh(provider)
        return provider


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

    async def test_delete_with_default_reassignment_last_success_at(self):
        """Deleted default → candidate with most recent last_success_at becomes new default."""
        provider = await _create_provider()

        # Create models with different last_success_at
        now = datetime.utcnow()
        m_old = await _create_model(provider.id, "test_model_old", model_type="text", is_default=True, last_success_at=now - timedelta(days=10))
        m_new = await _create_model(provider.id, "test_model_new", model_type="text", is_default=False, last_success_at=now - timedelta(days=1))

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(m_old.id)

        assert result["deleted"] == m_old.id
        assert result["new_default_id"] == m_new.id
        assert result["new_default_name"] == "test_model_new"

    async def test_delete_with_default_reassignment_ai_analytics_fallback(self):
        """When no last_success_at, falls back to ai_analytics.llm_model name match."""
        provider = await _create_provider()
        source = await _create_source()

        m_default = await _create_model(provider.id, "test_default_model", model_type="text", is_default=True)
        m_candidate = await _create_model(provider.id, "test_fallback_model", model_type="text", is_default=False)

        # Add ai_analytics entry referencing the fallback model name
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
        assert result["new_default_id"] == m_candidate.id

    async def test_delete_with_default_reassignment_any_active(self):
        """When no history, picks any active model of same type."""
        provider = await _create_provider()

        m_default = await _create_model(provider.id, "test_default_model", model_type="embedding", is_default=True)
        m_any = await _create_model(provider.id, "test_any_model", model_type="embedding", is_default=False)

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(m_default.id)

        assert result["deleted"] == m_default.id
        assert result["new_default_id"] == m_any.id

    async def test_delete_last_model_of_type_warning(self):
        """Deleting last model of a type returns warning."""
        provider = await _create_provider()

        m_only = await _create_model(provider.id, "test_only_model", model_type="embedding", is_default=True)

        mgr = LLMModelManager()
        result = await mgr.delete_with_default_reassignment(m_only.id)

        assert result["deleted"] == m_only.id
        assert result["new_default_id"] is None
        assert any("no model left for type embedding" in w for w in result["warnings"])

    async def test_delete_counts_scenarios_reset(self):
        """Scenarios referencing the model are counted in result."""
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

    async def test_resolve_default_model(self):
        """resolve_default_model returns is_default first, then first active."""
        provider = await _create_provider()

        m1 = await _create_model(provider.id, "test_default", model_type="text", is_default=True)
        m2 = await _create_model(provider.id, "test_active", model_type="text", is_default=False)

        mgr = LLMModelManager()
        resolved = await mgr.resolve_default_model("text")
        assert resolved.id == m1.id

        # Delete default, should fall back to first active
        await mgr.delete_with_default_reassignment(m1.id)
        resolved = await mgr.resolve_default_model("text")
        assert resolved.id == m2.id


class TestLLMModelManagerEdgeCases:
    """Edge case tests."""

    async def test_create_model_inactive_provider_raises(self):
        """Creating model for inactive provider should fail."""
        provider = await _create_provider("test_inactive")
        # Need to fetch in new session to modify
        async with async_session_maker() as s:
            p = await s.get(LLMProvider, provider.id)
            p.is_active = False
            await s.commit()

        mgr = LLMModelManager()
        with pytest.raises(ValueError, match="not active"):
            await mgr.create_model(provider_id=provider.id, name="test", model_id="test", model_type="text")

    async def test_update_nonexistent_model_returns_none(self):
        """Updating non-existent model returns None."""
        mgr = LLMModelManager()
        result = await mgr.update_model(999999, name="new_name")
        assert result is None

    async def test_delete_nonexistent_model_raises(self):
        """Deleting non-existent model raises ValueError."""
        mgr = LLMModelManager()
        with pytest.raises(ValueError, match="not found"):
            await mgr.delete_with_default_reassignment(999999)