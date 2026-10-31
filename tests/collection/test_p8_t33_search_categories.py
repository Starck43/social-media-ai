"""P8-T33: VK search-result category registry and people contract (mocked).

No live VK call, database or browser session: the people category executes
through the real adapter over a mocked client, and every other category is
checked only for its declared status, reason and bounded failure behaviour.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.collection.adapters.vk import VKCollectionAdapter
from app.services.collection.pages import CollectionProviderError
from app.services.collection.search_categories import (
    CATEGORY_CHANNELS,
    CATEGORY_CLIPS,
    CATEGORY_PEOPLE,
    EXECUTABLE_STATUSES,
    STATUS_ENABLED,
    STATUS_MOCKED,
    SearchCategoryError,
    category_catalog,
    get_category,
    resolve_categories,
    search_category,
    vk_search_categories,
)
from app.services.social.vk_client import VKClient


class _Platform:
    params = {"api_version": "5.199"}
    platform_type = "vk"


def _client():
    client = VKClient(_Platform())
    client._access_token = "TEST"
    return client


def _person_row(user_id=5, name="Анна"):
    return {"id": user_id, "name": name, "screen_name": f"id{user_id}", "city": "Киров"}


def test_people_category_is_registered_with_the_official_users_search_method():
    people = get_category(CATEGORY_PEOPLE)

    assert people.official_method == "users.search"
    assert people.result_kind == "person"
    assert people.adapter_operation == "search_people"
    assert people.status == STATUS_MOCKED
    assert people.executable is True
    assert people.evidence_ref == "docs/tasks/q-5.md"


def test_no_category_is_live_enabled_while_q5_owns_profile_evidence():
    categories = vk_search_categories()

    assert categories
    assert all(category.status != STATUS_ENABLED for category in categories)
    assert all(category.status in EXECUTABLE_STATUSES or category.reason for category in categories)


def test_every_requested_category_keeps_an_explicit_status_and_reason():
    by_id = {category.category_id: category for category in vk_search_categories()}

    for category_id in ("video", "apps", "market", "music"):
        assert by_id[category_id].status == "deferred"
        assert by_id[category_id].reason
        assert by_id[category_id].executable is False
    assert by_id[CATEGORY_CLIPS].status == "unsupported"
    assert by_id[CATEGORY_CLIPS].reason == "no_promised_public_search_method"
    assert by_id[CATEGORY_CHANNELS].reason == "partially_represented_by_communities"


def test_catalog_is_json_safe_and_shared_across_surfaces():
    catalog = category_catalog()

    json.dumps(catalog, ensure_ascii=False)
    by_id = {entry["id"]: entry for entry in catalog}
    assert by_id[CATEGORY_PEOPLE]["status"] == STATUS_MOCKED
    assert by_id[CATEGORY_PEOPLE]["official_method"] == "users.search"
    assert by_id["clips"]["executable"] is False
    assert all(entry["reason"] for entry in catalog)


def test_resolve_all_aggregates_only_executable_categories():
    selection = resolve_categories("all")

    assert [category.category_id for category in selection.selected] == [CATEGORY_PEOPLE]
    assert selection.unavailable == ()


def test_resolve_marks_deferred_and_unsupported_categories_with_reasons():
    selection = resolve_categories([CATEGORY_PEOPLE, "video", CATEGORY_CLIPS])

    assert [category.category_id for category in selection.selected] == [CATEGORY_PEOPLE]
    assert [entry["id"] for entry in selection.unavailable] == ["video", CATEGORY_CLIPS]
    assert all(entry["reason"] for entry in selection.unavailable)
    json.dumps(selection.as_dict(), ensure_ascii=False)


def test_resolve_unknown_category_fails_closed():
    with pytest.raises(SearchCategoryError) as caught:
        resolve_categories(["unknown-category"])

    assert caught.value.code == "search_category_unknown"


@pytest.mark.asyncio
async def test_people_search_returns_typed_people_results_with_provenance():
    client = _client()
    client.search_users = AsyncMock(return_value=[_person_row()])
    adapter = VKCollectionAdapter(client)

    page = await search_category(adapter, CATEGORY_PEOPLE, "дизайнер", limit=5)

    assert len(page.items) == 1
    person = page.items[0]
    assert person["platform"] == "vk"
    assert person["kind"] == "person"
    assert person["external_id"] == "5"
    assert person["url"] == "https://vk.com/id5"
    # A people result is a typed candidate, never a Source: no source identity.
    assert "source_id" not in person
    assert "source_type" not in person
    assert person["evidence"]["search_category"] == CATEGORY_PEOPLE
    assert person["evidence"]["official_method"] == "users.search"
    kwargs = client.search_users.await_args.kwargs
    assert kwargs["offset"] == 0
    assert kwargs["count"] == 5


@pytest.mark.asyncio
async def test_people_search_preserves_bounded_paging_and_city_scope():
    client = _client()
    client.search_users = AsyncMock(return_value=[_person_row(5), _person_row(6, "Иван")])
    adapter = VKCollectionAdapter(client)
    city = SimpleNamespace(id=1, title="Киров")

    page = await search_category(adapter, CATEGORY_PEOPLE, "дизайнер", limit=2, cursor="0", city=city)

    assert page.limit_reached is True
    assert page.next_cursor is None
    kwargs = client.search_users.await_args.kwargs
    assert kwargs["city_id"] == 1


@pytest.mark.asyncio
async def test_people_search_maps_1051_to_a_bounded_credentials_error():
    client = _client()
    client.search_users = AsyncMock(side_effect=RuntimeError("Ошибка API (1051): Method is not available"))
    adapter = VKCollectionAdapter(client)

    with pytest.raises(CollectionProviderError) as caught:
        await search_category(adapter, CATEGORY_PEOPLE, "дизайнер", limit=5)

    assert caught.value.code == "credentials"
    assert "1051" in caught.value.message


@pytest.mark.asyncio
async def test_deferred_category_fails_visibly_instead_of_an_empty_page():
    client = _client()
    adapter = VKCollectionAdapter(client)

    with pytest.raises(SearchCategoryError) as caught:
        await search_category(adapter, "video", "ремонт", limit=5)

    assert caught.value.code == "search_category_unavailable"
    assert "deferred" in caught.value.message


@pytest.mark.asyncio
async def test_provider_without_the_mapped_operation_fails_visibly():
    with pytest.raises(SearchCategoryError) as caught:
        await search_category(object(), CATEGORY_PEOPLE, "дизайнер", limit=5)

    assert caught.value.code == "search_category_operation_missing"
