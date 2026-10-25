"""Independent scoped pages over the predecessor's synthetic SQLAdmin harness."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader
from sqladmin.pagination import Pagination
from starlette.requests import Request

# Same-domain reusable harness: no app DB/engine, provider or root fixtures.
from test_p8_t30_related_inlines import (
    INLINE,
    LLMModel,
    LLMProvider,
    Platform,
    Source,
    User,
    actor,
    grant,
    harness,
    sections,
)

from app.types import ActionType

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = "inline-source-platform_id"


def query(h, value):
    h.request = Request({**h.request.scope, "query_string": value.encode()})


def populate(h):
    for index in range(100, 140):
        h.store.add(Source, id=index, tenant_id=10, platform_id=1, name=f"own-{index}")


@pytest.mark.asyncio
async def test_next_previous_pages_and_count_only_include_visible_rows(harness):
    h = harness
    populate(h)
    await grant(h, actor())
    first = (await sections(h))[0]
    assert isinstance(first["pagination"], Pagination)
    assert first["pagination"].count == 42
    assert first["pagination"].has_next and not first["pagination"].has_previous
    query(h, f"{SOURCE_ID}-page=2")
    second = (await sections(h))[0]
    assert len(second["rows"]) == 17
    assert not second["pagination"].has_next and second["pagination"].has_previous
    assert not ({r[0] for r in first["rows"]} & {r[0] for r in second["rows"]})
    assert {r[0] for r in first["rows"] + second["rows"]} == {"10", "20", *(str(i) for i in range(100, 140))}
    assert second["pagination"].previous_page.number == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "nan", "1e9", "<script>", "９", "9" * 5000])
async def test_malformed_page_input_fails_safe_without_large_int_or_offset(harness, value):
    query(harness, f"{SOURCE_ID}-page={value}")
    await grant(harness, actor())
    result = (await sections(harness))[0]
    assert result["pagination"].page == 1
    assert result["pagination"].count == 2
    assert harness.store.statements[-1]._offset_clause.value == 0


@pytest.mark.asyncio
async def test_large_positive_page_clamps_before_sql_offset(harness):
    populate(harness)
    query(harness, f"{SOURCE_ID}-page=999999999")
    await grant(harness, actor())
    result = (await sections(harness))[0]
    assert result["pagination"].page == 2
    assert harness.store.statements[-1]._offset_clause.value == 25


@pytest.mark.asyncio
@pytest.mark.parametrize("value,expected", [("10", 10), ("100", 100), ("500", 25), ("-10", 25), ("garbage", 25)])
async def test_page_size_whitelist_is_enforced(harness, value, expected):
    populate(harness)
    query(harness, f"{SOURCE_ID}-size={value}")
    await grant(harness, actor())
    result = (await sections(harness))[0]
    assert result["pagination"].page_size == expected
    assert len(result["rows"]) == min(42, expected)
    assert harness.store.statements[-1]._limit_clause.value == expected


@pytest.mark.asyncio
async def test_view_configuration_controls_columns_page_defaults_and_exclusion(harness):
    view = harness.views[Source]
    view.inline_columns = ["name", "name", "secret_encrypted", "params", "platform", "not_a_column"]
    view.inline_page_size = 1
    view.inline_page_size_options = (1, 2)
    await grant(harness, actor())
    result = (await sections(harness))[0]
    assert result["columns"] == ["Источник"]
    assert result["rows"] == [["source-10"]]
    assert result["pagination"].count == 2
    view.inline_exclude = True
    harness.store.statements.clear()
    assert await sections(harness) == []
    assert len(harness.store.statements) == 1  # no count/page against excluded target


@pytest.mark.asyncio
async def test_query_state_and_other_inline_pages_survive_all_navigation_links(harness):
    populate(harness)
    query(harness, f"page=7&pageSize=50&search=hello%26world&other-inline-page=3&{SOURCE_ID}-page=2")
    await grant(harness, actor())
    section = (await sections(harness))[0]
    links = [control.url for control in section["pagination"].page_controls] + [s["url"] for s in section["sizes"]]
    for link in links:
        split = urlsplit(link)
        params = parse_qs(split.query)
        assert split.path == "/admin/platform/details/1"
        assert split.fragment == SOURCE_ID
        assert params["page"] == ["7"] and params["pageSize"] == ["50"]
        assert params["search"] == ["hello&world"] and params["other-inline-page"] == ["3"]
    size10 = next(s for s in section["sizes"] if s["size"] == 10)
    assert parse_qs(urlsplit(size10["url"]).query)[f"{SOURCE_ID}-page"] == ["3"]
    assert section["pagination"].page_size == 25  # size links do not mutate the rendered pager


@pytest.mark.asyncio
async def test_two_fk_sections_page_independently(harness):
    h = harness
    for index in range(1, 31):
        h.store.add(LLMProvider, id=index, owner_user_id=1, name=f"provider-{index}")
        h.store.add(LLMModel, id=index, provider_id=index, owner_user_id=1, name=f"model-{index}")
    query(h, "inline-llm-provider-owner_user_id-page=2&inline-llm-model-owner_user_id-size=10")
    await grant(h, actor(rights={name: {ActionType.VIEW} for name in ("user", "llmprovider", "llmmodel")}))
    result = await sections(h, User, User(id=1))
    indexed = {s["id"]: s for s in result}
    providers = indexed["inline-llm-provider-owner_user_id"]
    models = indexed["inline-llm-model-owner_user_id"]
    assert providers["pagination"].page == 2 and len(providers["rows"]) == 5
    assert models["pagination"].page == 1 and len(models["rows"]) == 10
    assert all(s["pagination"].count == 30 for s in result)


@pytest.mark.asyncio
async def test_membership_revocation_recounts_and_clamps_current_page(harness):
    populate(harness)
    query(harness, f"{SOURCE_ID}-page=2")
    await grant(harness, actor())
    assert (await sections(harness))[0]["pagination"].page == 2
    harness.store.db.execute("UPDATE tenant_users SET is_active=0 WHERE tenant_id=10")
    result = (await sections(harness))[0]
    assert result["pagination"].count == 1 and result["pagination"].page == 1
    assert result["rows"] == [["20", "source-20"]]


@pytest.mark.asyncio
async def test_empty_page_has_zero_scoped_count_and_no_offset(harness):
    harness.store.db.execute("UPDATE tenant_users SET is_active=0")
    query(harness, f"{SOURCE_ID}-page=999")
    await grant(harness, actor())
    result = (await sections(harness))[0]
    assert result["pagination"].count == 0 and result["pagination"].page == 1
    assert result["rows"] == []


@pytest.mark.asyncio
async def test_pagination_markup_is_russian_accessible_and_read_only(harness):
    populate(harness)
    await grant(harness, actor())
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True, enable_async=True)
    env.globals["related_inlines"] = INLINE.related_inlines
    html = await env.get_template("sqladmin/related_inlines.html").render_async(
        model_view=harness.views[Platform],
        inline_object=Platform(id=1),
        request=harness.request,
    )
    assert 'class="pagination pagination-sm' in html and 'aria-current="page"' in html
    assert 'aria-disabled="true"' in html and 'aria-label="Следующая страница"' in html
    assert "Записи 1–25 из 42" in html and "По 25" in html
    assert "<form" not in html and 'type="submit"' not in html
    assert "/create" not in html and "/delete" not in html
    assert f'id="{SOURCE_ID}"' in html


def test_all_canonical_configured_columns_and_relation_headings_have_labels(monkeypatch):
    import socket

    from fastapi import FastAPI

    from app.admin.inlines import projection_columns, reverse_foreign_keys
    from app.admin.setup import setup_admin
    from app.admin.views import CollectedItemAdmin

    def refuse(*args, **kwargs):
        raise AssertionError("metadata only, no network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    admin = setup_admin(FastAPI())
    assert CollectedItemAdmin.inline_exclude is True
    for view in admin._views:
        if not getattr(view, "model", None) or view.inline_exclude:
            continue
        columns = projection_columns(view)
        assert 0 < len(columns) <= 6, view.identity
        for name in columns:
            assert name in view._column_labels, (view.identity, name)
        obj = view.model()
        for column in view.pk_columns:
            setattr(obj, column.key, 1)
        if hasattr(obj, "owner_user_id"):
            obj.owner_user_id = 1
        for target in admin._views:
            if not getattr(target, "model", None) or target.inline_exclude:
                continue
            for key, _ in reverse_foreign_keys(obj, target.model):
                for name in key.split(", "):
                    assert name in target._column_labels, (target.identity, name)


@pytest.mark.asyncio
async def test_joined_view_queries_do_not_inflate_count_or_repeat_rows(harness):
    from test_p8_t30_related_inlines import TenantUser

    h = harness
    h.store.add(TenantUser, id=999, tenant_id=10, user_id=2, role_id=1, channel="web", is_active=True)
    original_query = h.views[Source].list_query
    h.views[Source].list_query = lambda request: original_query(request).join(
        TenantUser, TenantUser.tenant_id == Source.tenant_id
    )
    await grant(h, actor())
    result = (await sections(h))[0]
    assert result["pagination"].count == 2
    assert result["rows"] == [["10", "source-10"], ["20", "source-20"]]


@pytest.mark.asyncio
async def test_compound_fk_count_and_pages_keep_both_columns(harness):
    h = harness
    h.store.add(LLMProvider, id=1, owner_user_id=1, name="own provider")
    h.store.add(LLMProvider, id=2, owner_user_id=2, name="foreign provider")
    h.store.add(LLMModel, id=1, provider_id=1, owner_user_id=1, name="visible")
    h.store.add(LLMModel, id=2, provider_id=1, owner_user_id=2, name="malformed foreign owner")
    await grant(h, actor(rights={"llmprovider": {ActionType.VIEW}, "llmmodel": {ActionType.VIEW}}))
    result = await sections(h, LLMProvider, LLMProvider(id=1, owner_user_id=1))
    assert result[0]["pagination"].count == 1
    assert result[0]["rows"] == [["1", "visible"]]
    assert result[0]["id"] == "inline-llm-model-provider_id-owner_user_id"
