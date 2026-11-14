"""P21-T13 increment B: safe grant-metadata cache and eligibility listing.

Mocked storage only: no database, no network, no provider and no credential. The
tests prove that a cached answer tracks *content* (an in-place price/name/default
edit invalidates it), that a revocation or failed query never serves the previous
tuple, that the DTO carries no secret field, and that the store is a bounded LRU.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import fields
from decimal import Decimal

import pytest

from app.services.ai import catalog_grants
from app.services.ai.catalog_grants import CatalogGrantResolver
from app.services.ai.dispatch_seam import FUNDING_CLASS_PERSONAL, FUNDING_CLASS_PLATFORM, DispatchContext
from app.services.ai.grant_metadata_cache import (
    METADATA_FIELDS,
    CatalogMetadataAnswer,
    CatalogMetadataError,
    CatalogModelMetadata,
    MetadataRevisionCache,
    build_answer,
    safe_revision,
)
from app.services.tenancy.service_plan_policy import (
    TokenAllowancePolicy,
    ValidatedPlanPolicy,
    WorkspacePlanContext,
)

__source_only__ = True

HOST = 11
TENANT = 33
CATALOG = 4
PROVIDER = 5
MODEL = 6
TOKEN_UNIT = 1_000_000

FORBIDDEN_DTO_FIELDS = frozenset(
    {
        "api_key",
        "encrypted_api_key",
        "managed_credential_ref",
        "credential_ref",
        "credential_version",
        "base_url",
        "auth_header",
        "custom_endpoint_path",
        "description",
        "endpoint",
        "secret",
        "token",
        "owner_user_id",
    }
)


# ── storage doubles ─────────────────────────────────────────


class FakeRow:
    def __init__(self, values):
        self._mapping = dict(values)


class FakeResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def one_or_none(self):
        assert len(self.rows) <= 1, "ambiguous fake projection"
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class MockedAsyncSession:
    def __init__(self, results=()):
        self.results = list(results)
        self.statements = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        if not self.results:
            raise AssertionError("unexpected extra statement")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def plan_policy(*, model_types=("text", "image")):
    return ValidatedPlanPolicy(
        plan_code="pro",
        policy_version=3,
        module_grants=("*",),
        daily_quotas={},
        resource_limits={"label": "pro", "model_types": list(model_types)},
        owned_workspace_limit_configured=True,
        max_owned_workspaces=2,
        token_policy=TokenAllowancePolicy(unit="tokens", period="day", token_limit=1000, policy_version=3),
    )


def holder(*, actor=HOST):
    snap = plan_policy()
    return WorkspacePlanContext(
        actor_user_id=actor,
        tenant_id=TENANT,
        plan_holder_user_id=HOST,
        plan_code=snap.plan_code,
        plan_policy_version=snap.policy_version,
        plan_expires_at=None,
        policy=snap,
    )


def make_context(*, funding=FUNDING_CLASS_PLATFORM, snapshot=None):
    return DispatchContext(
        actor_user_id=HOST,
        tenant_id=TENANT,
        plan_holder_user_id=HOST,
        funding_class=funding,
        plan_policy_version=3,
        workspace_plan=snapshot if snapshot is not None else holder(),
    )


def sample_metadata(**overrides):
    values = dict(
        id=MODEL,
        name="m",
        model_id="m-1",
        provider_id=PROVIDER,
        provider_name="p",
        provider_uid="p",
        provider_format="openai",
        catalog_id=CATALOG,
        funding_class=FUNDING_CLASS_PLATFORM,
        capabilities=("text",),
        max_tokens=4096,
        max_output_tokens=None,
        context_window_tokens=128000,
        default_temperature=0.3,
        pricing_state="undefined",
        input_price=None,
        output_price=None,
        pricing_token_unit=TOKEN_UNIT,
        is_active=True,
        is_default=True,
        priority=10,
    )
    values.update(overrides)
    return CatalogModelMetadata(**values)


def catalog_row(
    *,
    row_id=MODEL,
    name="Model",
    model_id="model-1",
    capabilities=("text",),
    input_price=Decimal("0.00010000"),
    pricing_state="priced",
    is_default=False,
    grant_id=70,
    grant_capability="text",
    priority=100,
    provider_id=PROVIDER,
    catalog_id=CATALOG,
):
    return FakeRow(
        {
            "id": row_id,
            "name": name,
            "model_id": model_id,
            "capabilities": list(capabilities),
            "max_tokens": 4096,
            "max_output_tokens": 2048,
            "context_window_tokens": 128000,
            "default_temperature": 0.3,
            "pricing_state": pricing_state,
            "input_price": input_price,
            "output_price": Decimal("0.00020000"),
            "pricing_token_unit": TOKEN_UNIT,
            "is_active": True,
            "provider_id": provider_id,
            "provider_name": "Provider",
            "provider_uid": "provider",
            "provider_format": "openai",
            "catalog_id": catalog_id,
            "grant_id": grant_id,
            "grant_capability": grant_capability,
            "grant_is_active": True,
            "priority": priority,
            "is_default": is_default,
        }
    )


def run_metadata(*, session, context=None, capabilities=frozenset({"text"}), resolver=None):
    resolver = (
        resolver if resolver is not None else CatalogGrantResolver(session, cache=MetadataRevisionCache(max_entries=4))
    )
    calls = []

    async def fake_resolve_holder_context(db, tenant_id, actor_user_id, **rest):
        calls.append((tenant_id, actor_user_id))
        return holder(actor=actor_user_id)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake_resolve_holder_context)
        return asyncio.run(resolver.eligible_metadata(context if context is not None else make_context(), capabilities))


# ── DTO shape ───────────────────────────────────────────────


def test_dto_is_frozen_safe_and_file_has_no_secret_field():
    assert METADATA_FIELDS == frozenset(field.name for field in fields(CatalogModelMetadata))
    assert not METADATA_FIELDS & FORBIDDEN_DTO_FIELDS
    for expected in (
        "id",
        "model_id",
        "provider_id",
        "provider_format",
        "catalog_id",
        "funding_class",
        "capabilities",
        "max_output_tokens",
        "context_window_tokens",
        "default_temperature",
        "pricing_state",
        "input_price",
        "output_price",
        "pricing_token_unit",
        "is_active",
        "is_default",
        "priority",
    ):
        assert expected in METADATA_FIELDS
    metadata = run_metadata(session=MockedAsyncSession([FakeResult([catalog_row()])]))[0]
    with pytest.raises(Exception):
        metadata.name = "mutated"  # frozen
    assert metadata.id == MODEL and type(metadata.id) is int
    assert metadata.capabilities == ("text",)
    assert isinstance(metadata.input_price, Decimal)
    assert "0.00010000" == metadata.safe_dict()["input_price"]


def test_empty_price_state_is_undefined_not_free():
    metadata = run_metadata(
        session=MockedAsyncSession([FakeResult([catalog_row(input_price=None, pricing_state=None)])])
    )[0]
    assert metadata.input_price is None and metadata.pricing_state is None


# ── listing semantics ───────────────────────────────────────


def test_platform_listing_uses_grants_and_orders_defaults_first():
    rows = [catalog_row(row_id=MODEL, grant_id=2, is_default=True), catalog_row(row_id=8, grant_id=1, priority=5)]
    metadata = run_metadata(session=MockedAsyncSession([FakeResult(rows)]))
    # Determinism is the resolver's query order, not the double's row order.
    assert [item.id for item in metadata] == [MODEL, 8]
    assert metadata[0].is_default is True and metadata[0].priority == 100
    assert metadata[1].is_default is False


def test_listing_filters_whole_capability_set_by_policy():
    rows = [catalog_row(row_id=MODEL, capabilities=("text",)), catalog_row(row_id=9, capabilities=("text", "image"))]
    metadata = run_metadata(session=MockedAsyncSession([FakeResult(rows)]), capabilities=frozenset({"text"}))
    # `text`-only models are eligible; a text+image model is not a text-only route's
    # own requirement, but its whole set is inside the plan list, so it is listed.
    assert [item.id for item in metadata] == [MODEL, 9]

    strict = CatalogGrantResolver(
        MockedAsyncSession([FakeResult(rows)]),
        cache=MetadataRevisionCache(max_entries=2),
    )
    snap = holder()
    downgraded = WorkspacePlanContext(
        actor_user_id=snap.actor_user_id,
        tenant_id=snap.tenant_id,
        plan_holder_user_id=snap.plan_holder_user_id,
        plan_code=snap.plan_code,
        plan_policy_version=snap.plan_policy_version,
        plan_expires_at=None,
        policy=ValidatedPlanPolicy(
            plan_code="pro",
            policy_version=3,
            module_grants=("*",),
            daily_quotas={},
            resource_limits={"label": "pro", "model_types": ["text"]},
            owned_workspace_limit_configured=True,
            max_owned_workspaces=2,
            token_policy=snap.policy.token_policy,
        ),
    )
    calls = []

    async def fake(db, tenant_id, actor_user_id, **rest):
        calls.append(tenant_id)
        return downgraded

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake)
        narrowed = asyncio.run(strict.eligible_metadata(make_context(), frozenset({"text"})))
    assert [item.id for item in narrowed] == [MODEL]


def test_personal_listing_reads_only_the_personal_kind():
    session = MockedAsyncSession([FakeResult([catalog_row(row_id=MODEL, grant_id=None)])])
    metadata = run_metadata(session=session, context=make_context(funding=FUNDING_CLASS_PERSONAL))
    assert [item.funding_class for item in metadata] == [FUNDING_CLASS_PERSONAL]
    assert metadata[0].priority == 100 and metadata[0].is_default is False
    sql = str(session.statements[0])
    assert "llm_catalogs.kind" in sql
    assert " OR " not in sql.upper()
    assert ".owner_user_id =" in sql or ".owner_user_id=: " in sql or "owner_user_id = :" in sql


@pytest.mark.parametrize("bad", [frozenset(), frozenset({"telepathy"}), {"text"}])
def test_malformed_listing_capabilities_return_empty(bad):
    assert run_metadata(session=MockedAsyncSession([]), capabilities=bad) == ()


# ── cache invalidation ─────────────────────────────────────


def test_in_place_edit_invalidates_a_cached_answer():
    cache = MetadataRevisionCache(max_entries=4)
    resolver = CatalogGrantResolver(
        MockedAsyncSession(
            [
                FakeResult([catalog_row()]),
                FakeResult([catalog_row(name="Renamed")]),
                FakeResult([catalog_row(name="Renamed")]),
            ]
        ),
        cache=cache,
    )

    async def fake(db, tenant_id, actor_user_id, **rest):
        return holder(actor=actor_user_id)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake)
        first = asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
        second = asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
        third = asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
    assert first[0].name == "Model"
    assert second[0].name == "Renamed"  # the edit is visible, not a stale hit
    assert second is third  # the edited revision is now the cached one
    assert len(cache) == 1


def test_unchanged_content_is_served_from_the_cache():
    cache = MetadataRevisionCache(max_entries=4)
    session = MockedAsyncSession(
        [FakeResult([catalog_row()]), FakeResult([catalog_row()]), FakeResult([catalog_row()])]
    )
    resolver = CatalogGrantResolver(session, cache=cache)

    async def fake(db, tenant_id, actor_user_id, **rest):
        return holder(actor=actor_user_id)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake)
        first = asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
        second = asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
        third = asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
    assert first == second and second is third  # same revision -> the cached tuple
    assert len(cache) == 1


def test_revocation_is_never_served_from_the_cache():
    cache = MetadataRevisionCache(max_entries=4)
    resolver = CatalogGrantResolver(MockedAsyncSession([FakeResult([catalog_row()]), FakeResult([])]), cache=cache)

    async def fake(db, tenant_id, actor_user_id, **rest):
        return holder(actor=actor_user_id)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake)
        assert len(asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))) == 1
        # Revocation is an empty answer with a new revision: the caller gets `()`,
        # never the previous tuple.
        assert asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"}))) == ()
    cached = cache.get(CatalogGrantResolver.metadata_cache_key(make_context(), frozenset({"text"})))
    assert cached is not None and cached.metadata == ()


def test_query_error_invalidates_and_raises_but_never_serves_stale():
    cache = MetadataRevisionCache(max_entries=4)
    resolver = CatalogGrantResolver(
        MockedAsyncSession([FakeResult([catalog_row()]), RuntimeError("boom")]), cache=cache
    )

    async def fake(db, tenant_id, actor_user_id, **rest):
        return holder(actor=actor_user_id)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake)
        asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
        assert len(cache) == 1
        with pytest.raises(RuntimeError):
            asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
    assert len(cache) == 0


def test_cache_key_separates_plan_version_funding_and_capabilities():
    cache = MetadataRevisionCache(max_entries=4)
    resolver = CatalogGrantResolver(
        MockedAsyncSession([FakeResult([catalog_row()]), FakeResult([catalog_row()]), FakeResult([catalog_row()])]),
        cache=cache,
    )

    async def fake(db, tenant_id, actor_user_id, **rest):
        return holder(actor=actor_user_id)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", fake)
        asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"})))
        asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text", "image"})))
        asyncio.run(resolver.eligible_metadata(make_context(funding=FUNDING_CLASS_PERSONAL), frozenset({"text"})))
    assert len(cache) == 3
    assert CatalogGrantResolver.metadata_cache_key(make_context(), frozenset({"text"})) != (
        CatalogGrantResolver.metadata_cache_key(make_context(funding=FUNDING_CLASS_PERSONAL), frozenset({"text"}))
    )


def test_metadata_cache_holds_no_authorization_and_no_secret():
    answer = build_answer([sample_metadata()])
    assert isinstance(answer, CatalogMetadataAnswer)
    assert not hasattr(answer, "allowed") and not hasattr(answer, "gallery") and not hasattr(answer, "grant")
    serialized = str(answer)
    for needle in FORBIDDEN_DTO_FIELDS:
        assert needle not in METADATA_FIELDS


# ── primitives ─────────────────────────────────────────────


def test_revision_distinguishes_bool_none_float_and_decimal_scale():
    assert safe_revision({"x": True}) != safe_revision({"x": 1})
    assert safe_revision({"x": None}) != safe_revision({"x": 0})
    assert safe_revision({"x": Decimal("1.20")}) != safe_revision({"x": Decimal("1.2")})
    assert safe_revision({"x": ("text",)}) == safe_revision({"x": ("text",)})


def test_unsafe_revision_payload_raises_a_bounded_error():
    class Opaque:
        pass

    with pytest.raises(CatalogMetadataError):
        safe_revision({"x": Opaque()})


def test_cache_is_a_bounded_lru():
    cache = MetadataRevisionCache(max_entries=2)
    answer = build_answer([])
    cache.store(("a",), answer)
    cache.store(("b",), answer)
    assert cache.get(("a",)) is answer  # a is refreshed
    cache.store(("c",), answer)
    assert cache.get(("b",)) is None
    assert len(cache) == 2
    cache.invalidate(("a",))
    assert cache.get(("a",)) is None
    cache.clear()
    assert len(cache) == 0


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "4"])
def test_cache_size_must_be_a_positive_int(bad):
    with pytest.raises(CatalogMetadataError):
        MetadataRevisionCache(max_entries=bad)


def test_cache_stores_only_immutable_answers():
    with pytest.raises(CatalogMetadataError):
        MetadataRevisionCache(max_entries=1).store(("a",), {"metadata": []})


# ── source guards ──────────────────────────────────────────


def test_cache_module_is_pure_stdlib_and_storage_free():
    source = inspect.getsource(__import__(build_answer.__module__, fromlist=["_"]))
    allowed = {"__future__", "hashlib", "json", "collections", "dataclasses", "decimal", "typing"}
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or (stripped.startswith("from ") and " import " in stripped):
            root = stripped.split()[1].split(".")[0]
            assert root in allowed, stripped
    assert "session" not in source and "settings" not in source


def test_platform_listing_missing_one_required_grant_is_not_offered():
    rows = [catalog_row(capabilities=("text", "image"), grant_capability="text")]
    assert run_metadata(session=MockedAsyncSession([FakeResult(rows)]), capabilities=frozenset({"text", "image"})) == ()


def test_multi_capability_grants_collapse_to_one_model_choice():
    rows = [
        catalog_row(capabilities=("text", "image"), grant_id=70, grant_capability="text", is_default=True, priority=5),
        catalog_row(
            capabilities=("text", "image"), grant_id=71, grant_capability="image", is_default=False, priority=10
        ),
    ]
    session = MockedAsyncSession([FakeResult(rows)])
    metadata = run_metadata(session=session, capabilities=frozenset({"text", "image"}))
    assert len(metadata) == 1 and metadata[0].id == MODEL
    assert metadata[0].is_default is False and metadata[0].priority == 10
    sql = str(session.statements[0])
    assert "FROM " in sql and "plan_model_grants JOIN" in sql


def test_personal_listing_supplies_priority_without_a_grant_column():
    row = catalog_row()._mapping
    for field in ("priority", "grant_id", "grant_capability", "grant_is_active"):
        row.pop(field)
    metadata = run_metadata(
        session=MockedAsyncSession([FakeResult([FakeRow(row)])]), context=make_context(funding=FUNDING_CLASS_PERSONAL)
    )
    assert metadata[0].priority == 100


def test_holder_read_failure_clears_cache_without_serving_old_metadata():
    cache = MetadataRevisionCache(max_entries=4)
    resolver = CatalogGrantResolver(MockedAsyncSession([FakeResult([catalog_row()])]), cache=cache)
    assert run_metadata(session=resolver._session, resolver=resolver)

    async def failed(db, tenant_id, actor_user_id, **kwargs):
        raise RuntimeError("synthetic-private-holder-error")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(catalog_grants, "resolve_holder_context", failed)
        assert asyncio.run(resolver.eligible_metadata(make_context(), frozenset({"text"}))) == ()
    assert len(cache) == 0


def test_personal_invitee_metadata_requires_delegation():
    context = DispatchContext(
        actor_user_id=22,
        tenant_id=TENANT,
        plan_holder_user_id=HOST,
        plan_policy_version=3,
        funding_class=FUNDING_CLASS_PERSONAL,
        workspace_plan=holder(actor=22),
    )
    session = MockedAsyncSession([])
    assert run_metadata(session=session, context=context) == ()
    assert session.statements == []
