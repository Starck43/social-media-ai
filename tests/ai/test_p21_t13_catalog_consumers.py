"""Dormant scoped consumers deny secrets/HTTP; unbound compatibility stays local."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.models import LLMModel, LLMProvider
from app.services.ai import catalog_reads, llm_client, llm_metadata, model_probe, personal_catalog
from app.services.ai.dispatch_seam import CODE_ADMISSION_UNAVAILABLE, DispatchContext, DispatchDenial

__source_only__ = True


@dataclass(frozen=True)
class Metadata:
    id: int = 10
    catalog_id: int = 7
    provider_id: int = 11
    name: str = "Managed text"
    model_id: str = "managed-text"
    provider_name: str = "Deployment"
    funding_class: str = "platform"
    capabilities: tuple = ("text",)
    is_default: bool = True
    priority: int = 42


@pytest.fixture
def read_scope(monkeypatch):
    from app.services.ai import catalog_grants

    metadata = Metadata()
    model = LLMModel(
        id=10,
        catalog_id=7,
        provider_id=11,
        owner_user_id=None,
        name=metadata.name,
        model_id=metadata.model_id,
        model_type="text",
        is_active=True,
        is_default=False,
        max_tokens=4096,
    )
    model.provider = LLMProvider(
        id=11, catalog_id=7, owner_user_id=None, name="Deployment", uid="deployment", is_active=True
    )
    statements = []

    class Resolver:
        def __init__(self, session, **kwargs):
            pass

        async def eligible_metadata(self, context, capabilities):
            assert context.plan_holder_user_id == 1
            return (metadata,) if set(capabilities) <= set(metadata.capabilities) else ()

    class Session:
        async def scalars(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: [model])

    monkeypatch.setattr(catalog_grants, "CatalogGrantResolver", Resolver)
    context = DispatchContext(
        actor_user_id=2,
        tenant_id=3,
        plan_holder_user_id=1,
        funding_class="platform",
        workspace_plan=SimpleNamespace(policy=SimpleNamespace(resource_limits={"model_types": ("text",)})),
    )
    return Session(), context, metadata, model, statements


@pytest.mark.asyncio
async def test_scoped_default_uses_grant_not_model_flag(read_scope, monkeypatch):
    session, context, metadata, model, statements = read_scope
    monkeypatch.setattr(llm_client.settings, "AGENT_MODEL", "")
    with catalog_reads.catalog_read_scope(session, context) as scope:
        assert await llm_client.resolve_model() is model
        assert llm_client.default_model_sort_key(model) == (False, 42, 10)
        assert llm_client._eligible_text_model(model, set()) is True
        assert await llm_client._allowed_model_types() == {"text"}
        assert scope.eligible_row(model, capabilities={"image"}) is False
    assert catalog_reads.current_catalog_reads() is None
    sql = str(statements[0])
    assert "catalog_id" in sql and "llm_catalogs.kind" in sql
    assert "encrypted_api_key" not in sql and "managed_credential_ref" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [99, True, -1])
async def test_scoped_explicit_selection_denies_without_loading_foreign_rows(read_scope, identity):
    session, context, metadata, model, statements = read_scope
    with catalog_reads.catalog_read_scope(session, context) as scope, pytest.raises(DispatchDenial):
        await scope.models(capabilities={"text"}, model_id=identity)
    assert statements == []


@pytest.mark.asyncio
async def test_ambiguous_env_name_never_falls_back(read_scope, monkeypatch):
    session, context, metadata, model, statements = read_scope
    monkeypatch.setattr(llm_client.settings, "AGENT_MODEL", "foreign-name")
    with catalog_reads.catalog_read_scope(session, context), pytest.raises(DispatchDenial):
        await llm_client.resolve_model()
    assert statements == []


def test_scoped_legacy_client_denies_before_decrypt_or_http(read_scope):
    session, context, metadata, model, statements = read_scope
    with catalog_reads.catalog_read_scope(session, context), pytest.raises(DispatchDenial) as denied:
        llm_client.OpenAIClient(model)
    assert denied.value.code == CODE_ADMISSION_UNAVAILABLE
    assert statements == []


@pytest.mark.asyncio
async def test_scoped_options_are_host_labeled_and_invitee_cannot_override_context(read_scope, monkeypatch):
    session, context, metadata, model, statements = read_scope
    monkeypatch.setattr(personal_catalog, "require_actor", lambda user_id: None)
    with catalog_reads.catalog_read_scope(session, context):
        options = await personal_catalog.chat_model_options(user_id=2, tenant_id=3)
        assert options == [
            {"id": 10, "label": "[platform] Deployment/managed-text", "is_default": True, "is_effective_default": True}
        ]
        assert await personal_catalog.validate_chat_model(user_id=2, tenant_id=3, model_id=None) is None
        with pytest.raises(PermissionError):
            await personal_catalog.chat_model_options(user_id=1, tenant_id=3)


def test_nested_read_scope_restores_parent(read_scope):
    session, context, metadata, model, statements = read_scope
    with catalog_reads.catalog_read_scope(session, context) as parent:
        with catalog_reads.catalog_read_scope(session, context) as child:
            assert catalog_reads.current_catalog_reads() is child
        assert catalog_reads.current_catalog_reads() is parent
    assert catalog_reads.current_catalog_reads() is None


def test_unbound_default_key_preserves_personal_order():
    assert catalog_reads.current_catalog_reads() is None
    assert llm_client.default_model_sort_key(SimpleNamespace(id=6, is_default=True)) == (False, 6)
    assert llm_client.default_model_sort_key(SimpleNamespace(id=2, is_default=False)) == (True, 2)


@pytest.mark.asyncio
async def test_explicit_personal_payload_cannot_exceed_model_output_ceiling(monkeypatch):
    from app.services.ai import personal_dispatch

    async def validate(client):
        return None

    monkeypatch.setattr(personal_dispatch, "validate_personal_dispatch", validate)
    monkeypatch.setattr(llm_client.settings, "PERSONAL_BILLING_ENABLED", False)
    client = SimpleNamespace(model=SimpleNamespace(max_tokens=4096, max_output_tokens=512))
    payload = {"max_tokens": 8192}
    assert await llm_client._personal_admission(client, payload, purpose="chat", stage="chat") == (None, None)
    assert payload["max_tokens"] == 512


@pytest.mark.asyncio
async def test_metadata_consumers_use_the_same_safe_scoped_projection(read_scope):
    session, context, metadata, model, statements = read_scope
    with catalog_reads.catalog_read_scope(session, context):
        rows = await llm_metadata.list_active_models()
        assert await llm_metadata.get_model_info("managed-text") == rows[0]
        assert await llm_metadata.get_provider_models(11) == rows
        assert await llm_metadata.get_provider_models(99) == []
        assert await llm_metadata.get_model_info("foreign") is None
    assert rows[0]["funding_class"] == "platform"
    assert not {"encrypted_api_key", "managed_credential_ref", "auth_header", "base_url"}.intersection(rows[0])
    assert statements == []


@pytest.mark.asyncio
async def test_scoped_analyzer_pin_is_numeric_and_denial_never_enters_legacy_fallback(read_scope):
    import __future__

    import ast
    from enum import Enum
    from pathlib import Path

    class MediaType(Enum):
        TEXT = "text"

    source = Path("app/services/ai/analyzer.py").read_text()
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_get_llm_model"
    )
    namespace = {"MediaType": MediaType, "get_enum_value": lambda value: value.value}
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            "analyzer-selection",
            "exec",
            flags=__future__.annotations.compiler_flag,
        ),
        namespace,
    )
    session, context, metadata, model, statements = read_scope
    with catalog_reads.catalog_read_scope(session, context):
        selected = await namespace["_get_llm_model"](None, SimpleNamespace(text_llm_model_id=10), "text")
        assert selected is model
        with pytest.raises(DispatchDenial):
            await namespace["_get_llm_model"](None, SimpleNamespace(text_llm_model_id=99), "text")
    assert len(statements) == 1


@pytest.mark.asyncio
async def test_scoped_probe_does_not_touch_key_or_provider(read_scope):
    session, context, metadata, model, statements = read_scope

    class Provider:
        def get_api_key(self):
            pytest.fail("A read-only catalog probe attempted credential access")

    probe_model = SimpleNamespace(provider=Provider())
    with catalog_reads.catalog_read_scope(session, context):
        assert model_probe.probe_status(probe_model)["ok"] is False
        assert model_probe.probe_short(probe_model)["tokens_spent"] == 0
        result = await model_probe.probe_connectivity(probe_model)
        assert result["ok"] is False and result["tokens_spent"] == 0
    assert statements == []


def test_probe_explicit_output_is_restricted_by_hard_ceiling():
    model = SimpleNamespace(max_tokens=4096, max_output_tokens=4)
    assert model_probe.request_parameters(model)[1] == 4
    assert model_probe.request_parameters(model, "A custom prompt")[1] == 4


@pytest.mark.asyncio
async def test_scoped_revocation_clears_prior_metadata_approval(read_scope):
    session, context, metadata, model, statements = read_scope
    with catalog_reads.catalog_read_scope(session, context) as scope:
        await scope.models(capabilities={"text"})
        assert scope.eligible_row(model, capabilities={"text"}) is True

        async def revoked(context, capabilities):
            return ()

        scope.resolver.eligible_metadata = revoked
        assert await scope.models(capabilities={"text"}) == []
        assert scope.eligible_row(model, capabilities={"text"}) is False
    assert len(statements) == 1
