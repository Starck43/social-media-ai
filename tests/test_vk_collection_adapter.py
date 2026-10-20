"""VK collection adapter: page envelopes and exact API parameters (mocked)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.collection.adapters.vk import VKCollectionAdapter
from app.services.collection.pages import CollectionProviderError
from app.services.social.vk_client import VKClient
from app.types import SourceType


class _Platform:
    params = {"api_version": "5.199"}
    platform_type = "vk"


def _client():
    client = VKClient(_Platform())
    client._access_token = "TEST"
    return client


def _item(post_id=11, owner_id=-7, text="пост", date=None):
    if date is None:
        date = int(datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp())
    return {
        "id": post_id,
        "owner_id": owner_id,
        "from_id": owner_id,
        "date": date,
        "text": text,
        "likes": {"count": 1},
        "comments": {"count": 2},
        "reposts": {"count": 3},
        "views": {"count": 4},
    }


def _source(external_id="-7"):
    return SimpleNamespace(external_id=external_id, source_type=SourceType.GROUP, params={})


@pytest.mark.asyncio
async def test_resolve_city_wraps_the_existing_city_lookup():
    client = _client()
    client.get_cities = AsyncMock(return_value=[{"id": 1, "title": "Киров", "region": "Кировская область"}])
    city = await VKCollectionAdapter(client).resolve_city("Киров")

    assert city.id == 1
    assert city.title == "Киров"
    client.get_cities.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_people_passes_city_offset_and_reports_next_page():
    client = _client()
    client.search_users = AsyncMock(return_value=[{"id": 5, "name": "Анна", "city": "Киров"}])
    adapter = VKCollectionAdapter(client)

    page = await adapter.search_people("дизайнер", city=SimpleNamespace(id=1), limit=2, cursor=None)

    assert page.items[0]["external_id"] == "5"
    assert page.items[0]["kind"] == "person"
    assert page.limit_reached is False
    assert page.next_cursor is None
    kwargs = client.search_users.await_args.kwargs
    assert kwargs["city_id"] == 1
    assert kwargs["offset"] == 0
    assert kwargs["count"] == 2


@pytest.mark.asyncio
async def test_search_communities_uses_the_group_search_method():
    client = _client()
    client.search_groups = AsyncMock(return_value=[{"external_id": "-7", "name": "Клуб"}])
    page = await VKCollectionAdapter(client).search_communities("дизайн", None, 1, "0")

    assert page.items[0]["kind"] == "community"
    assert page.limit_reached is True
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_search_posts_builds_newsfeed_params_and_applies_owner_filter():
    client = _client()
    client._search_response = AsyncMock(
        return_value={
            "items": [_item(owner_id=-7), _item(post_id=12, owner_id=-9)],
            "profiles": [],
            "groups": [{"id": 7, "name": "Клуб дизайнеров"}],
            "total_count": 20,
        }
    )
    page = await VKCollectionAdapter(client).search_posts(
        "Древиз",
        since="2026-10-01",
        until=None,
        limit=5,
        cursor=None,
        owner_ids=["-7"],
    )

    assert [item["external_id"] for item in page.items] == ["-7_11"]
    assert page.items[0]["content_type"] == "post"
    assert page.items[0]["platform"] == "vk"
    assert page.total_available == 20
    assert page.limit_reached is False
    params = client._search_response.await_args.args[1]
    assert params["q"] == "Древиз"
    assert params["count"] == 5
    assert params["offset"] == 0
    assert "start_time" in params
    assert client._owner_ids_filter is None


@pytest.mark.asyncio
async def test_search_posts_maps_1051_to_a_bounded_credentials_error():
    client = _client()
    client._search_response = AsyncMock(side_effect=RuntimeError("Ошибка API (1051): Method is not available"))
    with pytest.raises(CollectionProviderError) as caught:
        await VKCollectionAdapter(client).search_posts("Древиз", None, None, 10, None)

    assert caught.value.code == "credentials"
    assert "1051" in caught.value.message


@pytest.mark.asyncio
async def test_fetch_wall_uses_wall_get_and_normalises_a_post():
    client = _client()
    client._fetch_page = AsyncMock(return_value={"response": {"items": [_item()], "count": 10}})
    page = await VKCollectionAdapter(client).fetch_wall(_source(), "2026-10-01", None, 50, None)

    assert len(page.items) == 1
    assert page.items[0]["external_id"] == "-7_11"
    assert page.total_available == 10
    method, params = client._fetch_page.await_args.args
    assert method == "wall.get"
    assert params["owner_id"] == -7
    assert params["count"] == 50
    assert "start_time" in params


@pytest.mark.asyncio
async def test_fetch_wall_applies_the_window_client_side():
    client = _client()
    old = _item(date=int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()))
    client._fetch_page = AsyncMock(return_value={"response": {"items": [old], "count": 1}})

    page = await VKCollectionAdapter(client).fetch_wall(_source(), "2026-10-01", None, 50, None)

    assert page.items == ()


@pytest.mark.asyncio
async def test_fetch_comments_uses_parent_identity_and_composite_id():
    client = _client()
    client._fetch_page = AsyncMock(
        return_value={
            "response": {
                "items": [{"id": 99, "from_id": 5, "date": 1700000001, "text": "италия", "likes": {"count": 1}}],
                "profiles": [{"id": 5, "first_name": "Анна", "last_name": "Иванова"}],
                "count": 1,
            }
        }
    )
    post = {"external_id": "-7_11", "owner_id": -7, "post_id": 11}
    page = await VKCollectionAdapter(client).fetch_comments(post, 20, None)

    assert page.items[0]["content_type"] == "comment"
    assert page.items[0]["external_id"] == "-7_11:99"
    assert page.items[0]["parent_external_id"] == "-7_11"
    assert page.items[0]["author"]["name"] == "Анна Иванова"
    method, params = client._fetch_page.await_args.args
    assert method == "wall.getComments"
    assert params["owner_id"] == -7
    assert params["post_id"] == 11


@pytest.mark.asyncio
async def test_fetch_likes_and_reposts_return_candidates_not_text_items():
    client = _client()
    client._fetch_page = AsyncMock(
        side_effect=[
            {"response": {"items": [{"id": 5, "name": "Анна"}], "count": 1}},
            {"response": {"items": [{"user": {"id": 6, "name": "Иван"}}], "count": 1}},
        ]
    )
    adapter = VKCollectionAdapter(client)
    post = {"external_id": "-7_11", "owner_id": -7, "post_id": 11}

    likes = await adapter.fetch_likes(post, 10, None)
    reposts = await adapter.fetch_reposts(post, 10, None)

    assert likes.items[0]["kind"] == "person"
    assert likes.items[0]["external_id"] == "5"
    assert reposts.items[0]["external_id"] == "6"
    assert "content_type" not in likes.items[0]


@pytest.mark.asyncio
async def test_fetch_profiles_splits_users_and_groups():
    client = _client()
    client._fetch_page = AsyncMock(
        side_effect=[
            {"response": [{"id": 5, "first_name": "Анна", "city": {"title": "Киров"}}]},
            {"response": [{"id": 7, "name": "Клуб"}]},
        ]
    )
    profiles = await VKCollectionAdapter(client).fetch_profiles(["5", "-7"])

    assert profiles[0]["kind"] == "person"
    assert profiles[0]["city"] == "Киров"
    assert profiles[1]["kind"] == "community"
    assert profiles[1]["external_id"] == "-7"
    assert client._fetch_page.await_count == 2
