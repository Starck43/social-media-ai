"""The owner-only table registry: complete, read-only, and closed to admins.

`tests/admin/test_admin_shell_catalog.py` proves the same seam for the catalog
pages (real SQLAdmin routes, real views, inert storage); this file pins the
registry's two promises — it lists every table from the metadata, and only the
platform owner may reach it, no matter what grants the actor carries.
"""

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from sqladmin import Admin
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles
from test_admin_model_permission_policy import CATALOG, TYPES, user_with

from app.admin.registry import PAGE_SIZES
from app.admin.table_registry import build_table_registry, filter_tables, find_table, paginate_tables
from app.models import Base

ROOT = Path(__file__).resolve().parents[2]


# --- the registry's data model -------------------------------------------------


def synthetic_metadata():
    metadata = MetaData()
    Table(
        "sources",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(50), nullable=False),
        Column("tenant_id", Integer),
    )
    Table("agent_task_sources", metadata, Column("agent_task_id", Integer, primary_key=True))
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("hashed_password", String(255), nullable=False),
    )
    return metadata


def test_registry_lists_every_table_including_association_tables():
    tables = build_table_registry(synthetic_metadata())
    assert [item["table"] for item in tables] == ["agent_task_sources", "sources", "users"]
    # An association table has no model, but it is still a table the owner asked to see.
    assert find_table(tables, "agent_task_sources")["model"] is None
    assert find_table(tables, "sessions") is None


def test_registry_describes_columns_without_reading_rows():
    item = find_table(build_table_registry(synthetic_metadata()), "users")
    assert [column["name"] for column in item["columns"]] == ["id", "hashed_password"]
    assert item["columns"][0]["primary_key"] is True
    assert item["columns"][1]["nullable"] is False
    # Credentials are flagged so the owner sees what the table carries.
    assert item["columns"][1]["sensitive"] is True


def test_registry_labels_resource_keys_from_the_access_catalog():
    item = find_table(build_table_registry(Base.metadata), "sources")
    assert item["model"] == "source"
    assert item["resource_key"] == "collection.source"


def test_registry_covers_the_whole_model_metadata():
    """The surface is complete: every mapped table appears exactly once."""
    tables = build_table_registry(Base.metadata)
    expected = {table.name for table in Base.metadata.tables.values()}
    assert {item["table"] for item in tables} == expected
    assert len(tables) == len(Base.metadata.tables)


def test_registry_search_and_paging_clamp():
    tables = build_table_registry(synthetic_metadata())
    assert [item["table"] for item in filter_tables(tables, "USER")] == ["users"]
    assert [item["table"] for item in filter_tables(tables, "collection.source")] == ["sources"]
    page = paginate_tables(tables, 9, 2)
    assert (page["page"], page["pages"], page["total"]) == (2, 2, 3)
    assert [item["table"] for item in page["items"]] == ["users"]


# --- the console surface -------------------------------------------------------


@pytest.fixture(scope="module")
def registry_module():
    from app.admin.registry import TableRegistryAdmin

    return NS(TableRegistryAdmin=TableRegistryAdmin)


def console(registry_module, user):
    """Mount the real registry view on real SQLAdmin routes; no DB, no engine."""
    user.username = "verified-admin"
    user.role.label = CATALOG.role_label(user.role.codename)

    class Auth(AuthenticationBackend):
        async def authenticate(self, request):
            request.state.admin_user = user
            return user.has_admin_access()

    app = Starlette()
    app.mount("/static", StaticFiles(directory=str(ROOT / "app/static")), name="static")
    admin = Admin(
        app,
        session_maker=sessionmaker(),
        authentication_backend=Auth("synthetic-session-key"),
        templates_dir=str(ROOT / "app/templates"),
    )
    admin.templates.env.globals.update(
        csrf_token=lambda: "synthetic-csrf",
        settings=NS(),
        debug=False,
        related_controls=lambda *args, **kwargs: [],
        related_token=AsyncMock(return_value=None),
    )
    admin.add_view(registry_module.TableRegistryAdmin)
    admin.admin.mount("", app)
    # The registry mints its own links: the console router is mounted under
    # `base_url`, so `url_for('admin:view-...')` cannot resolve from the app.
    assert admin._views[0].url() == "/admin/registry"
    assert admin._views[0].url(table="users") == "/admin/registry?table=users"
    return NS(app=app, admin=admin, user=user)


async def get(ctx, url):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ctx.app), base_url="https://test.invalid") as client:
        return await client.get(url)


@pytest.mark.asyncio
async def test_owner_sees_the_registry_with_tables_and_search(registry_module):
    ctx = console(registry_module, user_with(role="superuser"))
    response = await get(ctx, "/admin/registry")
    assert response.status_code == 200, response.text
    body = response.text
    assert "data-table-registry" in body
    assert 'name="q"' in body
    # The first page is bounded by the default page size, not by a table count
    # that grows with the metadata: assert the bound, then reach a table that
    # falls past it through the search the owner actually uses.
    total = len(build_table_registry(Base.metadata))
    assert total > 0
    assert body.count("data-table-name=") == min(total, PAGE_SIZES[1])
    assert f"Всего {total} ·" in body

    found = await get(ctx, "/admin/registry?q=user_credentials")
    assert found.status_code == 200, found.text
    assert 'data-table-name="user_credentials"' in found.text


@pytest.mark.asyncio
async def test_registry_pages_past_the_default_size(registry_module):
    """A table beyond the first page is still reachable by paging."""
    ctx = console(registry_module, user_with(role="superuser"))
    tables = build_table_registry(Base.metadata)
    if len(tables) <= PAGE_SIZES[1]:
        pytest.skip("metadata fits on one page")
    last = tables[-1]["table"]
    response = await get(ctx, "/admin/registry?page=2")
    assert response.status_code == 200, response.text
    assert f'data-table-name="{last}"' in response.text


@pytest.mark.asyncio
async def test_owner_reads_one_table_structure(registry_module):
    response = await get(
        console(registry_module, user_with(role="superuser")), "/admin/registry?table=user_credentials"
    )
    assert response.status_code == 200, response.text
    assert "data-table-registry-detail" in response.text
    assert "secret_encrypted" in response.text
    assert "чувствительный" in response.text


@pytest.mark.asyncio
async def test_unknown_table_is_a_404_for_the_owner(registry_module):
    response = await get(console(registry_module, user_with(role="superuser")), "/admin/registry?table=no_such_table")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "operator"])
async def test_registry_is_403_for_every_platform_role_but_the_owner(registry_module, role):
    """Even a wildcard-carrying admin, even an operator with every grant."""
    actor = user_with(*TYPES.ActionType, role=role)
    ctx = console(registry_module, actor)
    assert (await get(ctx, "/admin/registry")).status_code == 403
    assert (await get(ctx, "/admin/registry?table=users")).status_code == 403


@pytest.mark.asyncio
async def test_registry_has_no_write_or_export_route(registry_module):
    ctx = console(registry_module, user_with(role="superuser"))
    for url in [
        "/admin/registry/export/csv",
        "/admin/registry/create",
        "/admin/registry/edit/1",
        "/admin/registry/delete",
    ]:
        assert (await get(ctx, url)).status_code in {404, 405}
    view = ctx.admin._views[0]
    assert not getattr(view, "is_model", True)
    # No model means no form, no session and no bulk action to invoke.
    assert not hasattr(view, "model")


@pytest.mark.asyncio
async def test_registry_is_hidden_from_the_menu_for_admins(registry_module):
    ctx = console(registry_module, user_with(role="admin"))
    assert (await get(ctx, "/admin/")).status_code == 200
    view = ctx.admin._views[0]
    assert view.is_visible(NS(state=NS(admin_user=ctx.user))) is False
    assert view.is_visible(NS(state=NS(admin_user=user_with(role="superuser")))) is True


@pytest.mark.asyncio
async def test_registry_page_size_selection_is_bounded(registry_module):
    response = await get(console(registry_module, user_with(role="superuser")), "/admin/registry?size=100000")
    assert response.status_code == 200
    # Anything outside the accepted sizes falls back to the console default.
    # Count the listed tables instead of pinning a literal: every new model would
    # otherwise break this behaviour check.
    total = len(build_table_registry(Base.metadata))
    default_size = 50
    assert total > default_size, "the registry must be larger than one default page"
    assert f"Всего {total}" in response.text
    assert f"страница 1 из {-(-total // default_size)}" in response.text
