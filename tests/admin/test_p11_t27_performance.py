"""P11-T27 S6: per-request row criteria cache, view classification cache, table registry cache, and N+1-free membership grants."""

from __future__ import annotations

__source_only__ = True

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select

from app.admin import session as admin_session
from app.admin import table_registry as table_registry_module
from app.admin.scope import AdminScopeMiddleware, _workspace_view_by_identity
from app.admin.session import _scope_reads, row_criteria_scope, row_criterion
from app.admin.table_registry import build_table_registry
from app.core.access_catalog import workspace_grants
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Base, LLMProvider, Source
from app.types import ActionType


def _actor(user_id: int = 11, *, superuser: bool = False, source_actions=(ActionType.VIEW,)):
    grants = {"source": set(source_actions), "llmprovider": {ActionType.VIEW}}
    return SimpleNamespace(
        id=user_id,
        is_active=True,
        is_superuser=superuser,
        grants=grants,
        role=SimpleNamespace(id=1, permissions=()),
        has_admin_access=lambda: True,
        model_permissions=lambda model_name: set(grants.get(model_name.lower(), ())),
        has_perm_for=lambda model_name, action: action in grants.get(model_name.lower(), ()),
    )


def test_row_criterion_and_scope_reads_reuse_per_request_cache(monkeypatch):
    """Repeated SELECTs in one request reuse computed row criteria instead of rescanning every mapper."""
    actor = _actor()
    member_grants = frozenset({("source", ActionType.VIEW)})
    calls = []
    real_compute = admin_session._compute_row_criterion

    def counting_compute(model):
        calls.append(model)
        return real_compute(model)

    monkeypatch.setattr(admin_session, "_compute_row_criterion", counting_compute)

    state_one = SimpleNamespace(is_select=True, statement=select(Source))
    state_two = SimpleNamespace(is_select=True, statement=select(LLMProvider))

    with (
        row_criteria_scope(),
        tenant_scope(5),
        permission_scope(actor, member_grants=member_grants),
    ):
        _scope_reads(state_one)
        first_pass_count = len(calls)
        assert first_pass_count == len(Base.registry.mappers)

        # Second SELECT in the same request scope must not recompute any mapper criterion.
        _scope_reads(state_two)
        assert len(calls) == first_pass_count

        # Direct `row_criterion` lookups in `before_flush` also hit the same per-request cache.
        assert row_criterion(Source) is not None
        assert row_criterion(LLMProvider) is not None
        assert len(calls) == first_pass_count


def test_row_criterion_cache_invalidates_when_actor_or_tenant_changes(monkeypatch):
    """Switching active workspace or actor must never reuse another scope's predicate."""
    actor = _actor(user_id=21)
    other = _actor(user_id=22)
    grants = frozenset({("source", ActionType.VIEW)})

    with row_criteria_scope():
        with tenant_scope(10), permission_scope(actor, member_grants=grants):
            crit_ws_10 = str(row_criterion(Source).compile(compile_kwargs={"literal_binds": True}))
            crit_user_21 = str(row_criterion(LLMProvider).compile(compile_kwargs={"literal_binds": True}))
        with tenant_scope(20), permission_scope(actor, member_grants=grants):
            crit_ws_20 = str(row_criterion(Source).compile(compile_kwargs={"literal_binds": True}))
        with tenant_scope(10), permission_scope(other, member_grants=grants):
            crit_user_22 = str(row_criterion(LLMProvider).compile(compile_kwargs={"literal_binds": True}))

    assert "10" in crit_ws_10 and "20" in crit_ws_20 and crit_ws_10 != crit_ws_20
    assert "21" in crit_user_21 and "22" in crit_user_22 and crit_user_21 != crit_user_22


def test_workspace_page_classification_is_cached():
    """`_reads_workspace_data` caches identity classification across requests."""
    _workspace_view_by_identity.cache_clear()
    AdminScopeMiddleware._reads_workspace_data.cache_clear()

    assert AdminScopeMiddleware._reads_workspace_data("/admin/source/list") is True
    assert AdminScopeMiddleware._reads_workspace_data("/admin/llm-provider/list") is False
    assert AdminScopeMiddleware._reads_workspace_data("/admin/source/list") is True

    assert _workspace_view_by_identity.cache_info().currsize == 1
    info = AdminScopeMiddleware._reads_workspace_data.cache_info()
    assert info.hits >= 1


def test_build_table_registry_caches_by_metadata_signature(monkeypatch):
    """`build_table_registry` does not rescan columns when metadata has not changed."""
    metadata = MetaData()
    Table("alpha", metadata, Column("id", Integer, primary_key=True), Column("api_key", String(64)))

    calls = 0
    real_columns = table_registry_module._table_columns

    def counting_columns(table):
        nonlocal calls
        calls += 1
        return real_columns(table)

    monkeypatch.setattr(table_registry_module, "_table_columns", counting_columns)

    first = build_table_registry(metadata)
    assert calls == 1
    second = build_table_registry(metadata)
    assert second is first
    assert calls == 1

    # Adding a table changes the metadata signature and triggers a fresh build.
    Table("beta", metadata, Column("id", Integer, primary_key=True))
    third = build_table_registry(metadata)
    assert [item["table"] for item in third] == ["alpha", "beta"]
    assert calls == 3


@pytest.mark.asyncio
async def test_memberships_use_preloaded_roles_without_per_membership_queries(monkeypatch):
    """Eager-loaded `TenantUser.role` resolves grants in memory with zero `member_grants` queries."""
    rows = [
        SimpleNamespace(tenant_id=3, is_active=True, role_id=1, role=SimpleNamespace(codename="admin")),
        SimpleNamespace(tenant_id=7, is_active=True, role_id=2, role=SimpleNamespace(codename="viewer")),
        SimpleNamespace(tenant_id=9, is_active=True, role_id=3, role=SimpleNamespace(codename="manager")),
    ]
    monkeypatch.setattr(
        "app.models.managers.tenant_manager.TenantUserManager.web_memberships",
        AsyncMock(return_value=rows),
    )
    forbid_n_plus_one = AsyncMock(side_effect=AssertionError("per-membership member_grants must not run"))
    monkeypatch.setattr("app.models.managers.tenant_manager.TenantUserManager.member_grants", forbid_n_plus_one)

    resolved = await AdminScopeMiddleware._memberships(SimpleNamespace(id=15))
    assert resolved == [
        (3, workspace_grants("admin")),
        (7, workspace_grants("viewer")),
        (9, workspace_grants("manager")),
    ]
    forbid_n_plus_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_memberships_batch_load_unloaded_roles_in_a_single_query(monkeypatch):
    """When `role` is not preloaded on `TenantUser`, missing `role_id`s are fetched in one batch query."""
    from app.models.tenancy.workspace_role import WorkspaceRole

    rows = [
        SimpleNamespace(tenant_id=3, is_active=True, role_id=10, role=None),
        SimpleNamespace(tenant_id=7, is_active=True, role_id=20, role=None),
    ]
    monkeypatch.setattr(
        "app.models.managers.tenant_manager.TenantUserManager.web_memberships",
        AsyncMock(return_value=rows),
    )
    batch_filter = AsyncMock(
        return_value=[
            SimpleNamespace(id=10, codename="admin"),
            SimpleNamespace(id=20, codename="analyst"),
        ]
    )
    monkeypatch.setattr(
        WorkspaceRole,
        "objects",
        SimpleNamespace(
            filter=batch_filter,
            get=AsyncMock(side_effect=AssertionError("per-membership WorkspaceRole.objects.get must not run")),
        ),
    )

    resolved = await AdminScopeMiddleware._memberships(SimpleNamespace(id=15))
    assert resolved == [
        (3, workspace_grants("admin")),
        (7, workspace_grants("analyst")),
    ]
    assert batch_filter.await_count == 1
