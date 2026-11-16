"""No-DB tests for the VK search path: params, pagination stop, normalisation.

Owner command: `.venv/bin/python -m pytest tests/test_vk_search.py -q --noconftest -o addopts=`

A SEARCH source is a phrase, not a wall: `newsfeed.search` is called with
`q`/`extended`, the date window is the same as for `wall.get`, authors and
permalinks come out of normalisation, and `params["owner_ids"]` narrows the
wall client-side (the method has no owner parameter).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.social.vk_client import VKClient
from app.types import SourceType
from app.utils.date_parsing import to_unix_timestamp, universal_date_parser


class _Platform:
    params = {"api_base_url": "https://api.vk.com/method", "api_version": "5.199"}
    platform_type = "vk"


def _source(**overrides):
    defaults = dict(
        id=1,
        name="search",
        external_id="12345",
        params={},
        last_checked=None,
        source_type=SourceType.SEARCH,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _client():
    client = VKClient(_Platform())
    client._access_token = "TEST"
    return client


class TestBuildParamsForNewsfeedSearch:
    def test_q_comes_from_params_query(self):
        source = _source(params={"query": "дизайн интерьера"})
        params = _client()._build_params(source, "newsfeed.search")
        assert params["q"] == "дизайн интерьера"

    def test_q_falls_back_to_external_id(self):
        source = _source(external_id="77777")
        params = _client()._build_params(source, "newsfeed.search")
        assert params["q"] == "77777"

    def test_extended_and_search_fields_are_sent(self):
        params = _client()._build_params(_source(), "newsfeed.search")
        assert params["extended"] == 1
        assert params["fields"] == "screen_name,is_closed,type,members_count"

    def test_count_offset_come_from_collection_params(self):
        source = _source(params={"collection": {"count": 25, "offset": 3}})
        params = _client()._build_params(source, "newsfeed.search")
        assert params["count"] == 25
        assert params["offset"] == 3

    def test_date_window_from_force_refresh_cli_dates(self):
        source = _source(
            params={"force_refresh": True, "cli_dates": {"start_date": "2026-01-01", "end_date": "2026-01-31"}}
        )
        params = _client()._build_params(source, "newsfeed.search")
        assert params["start_time"] == to_unix_timestamp(universal_date_parser("2026-01-01"))
        assert params["end_time"] == to_unix_timestamp(universal_date_parser("2026-01-31"))

    def test_incremental_window_uses_last_checked(self):
        source = _source(last_checked=datetime(2026, 2, 1, tzinfo=timezone.utc))
        params = _client()._build_params(source, "newsfeed.search")
        assert params["start_time"] == to_unix_timestamp(universal_date_parser("2026-02-01"))

    def test_api_version_is_attached(self):
        params = _client()._build_params(_source(), "newsfeed.search")
        assert params["v"] == "5.199"
        assert params["access_token"] == "TEST"

    def test_search_phrase_as_external_id_builds_params(self):
        """A SEARCH source without `params.query` carries its phrase as the id.

        The method's `q` accepts it, so the params must build; `int()` on a
        phrase in the leftover user-info block is a bug, not a contract.
        """
        source = _source(external_id="дизайн интерьера")
        params = _client()._build_params(source, "newsfeed.search")
        assert params["q"] == "дизайн интерьера"


class TestShouldStopPagination:
    def _response(self, **data):
        return {"response": {"items": [{"id": i} for i in range(50)], "count": 50, **data}}

    def test_empty_page_stops(self):
        assert _client()._should_stop_pagination({"response": {"items": []}}, [], 50, 0) is True

    def test_total_count_wins_over_count(self):
        # count says the wall has 9000 posts, total_count says 150: pagination
        # must stop at 150, not keep going to 9000.
        response = self._response(count=9000, total_count=150)
        assert _client()._should_stop_pagination(response, [{"id": 1}] * 50, 50, 150) is True
        assert _client()._should_stop_pagination(response, [{"id": 1}] * 50, 50, 149) is False

    def test_count_is_the_wall_fallback(self):
        response = self._response(count=120)
        assert _client()._should_stop_pagination(response, [{"id": 1}] * 50, 50, 120) is True
        assert _client()._should_stop_pagination(response, [{"id": 1}] * 50, 50, 119) is False

    def test_vk_serves_only_the_first_thousand(self):
        response = self._response(count=999999, total_count=999999)
        assert _client()._should_stop_pagination(response, [{"id": 1}] * 50, 50, 999) is False
        assert _client()._should_stop_pagination(response, [{"id": 1}] * 50, 50, 1000) is True


class TestExtractItemsFromResponse:
    def test_profiles_and_groups_are_kept_for_author_names(self):
        client = _client()
        response = {
            "response": {
                "items": [{"id": 1}],
                "profiles": [{"id": 42, "first_name": "Анна", "last_name": "Иванова"}],
                "groups": [{"id": 7, "name": "Клуб дизайнеров"}],
            }
        }
        assert client._extract_items_from_response(response) == [{"id": 1}]
        assert client._profile_names == {42: "Анна Иванова"}
        assert client._group_names == {-7: "Клуб дизайнеров"}


class TestNormalizeResponse:
    def _raw(self, items):
        return {"response": {"items": items}}

    def _item(self, owner_id=-7, from_id=None, post_id=11, text="привет"):
        return {
            "id": post_id,
            "owner_id": owner_id,
            "from_id": from_id,
            "date": 1700000000,
            "text": text,
            "likes": {"count": 1},
            "comments": {"count": 2},
            "reposts": {"count": 0},
            "views": {"count": 3},
        }

    def test_author_comes_from_from_id_with_profile_name(self):
        client = _client()
        client._profile_names = {42: "Анна Иванова"}
        (item,) = client._normalize_response(self._raw([self._item(from_id=42)]), SourceType.SEARCH)
        assert item["author"] == {"platform": "vk", "external_id": "42", "id": 42, "name": "Анна Иванова"}

    def test_community_author_resolves_group_negative_id(self):
        client = _client()
        client._group_names = {-7: "Клуб дизайнеров"}
        (item,) = client._normalize_response(self._raw([self._item(from_id=-7)]), SourceType.SEARCH)
        assert item["author"]["external_id"] == "-7"
        assert item["author"]["name"] == "Клуб дизайнеров"

    def test_author_falls_back_to_owner_id(self):
        (item,) = _client()._normalize_response(self._raw([self._item(from_id=None)]), SourceType.SEARCH)
        assert item["author"] == {"platform": "vk", "external_id": "-7", "id": -7}

    def test_permalink_is_wall_style(self):
        (item,) = _client()._normalize_response(self._raw([self._item(owner_id=-7, post_id=11)]), SourceType.SEARCH)
        assert item["permalink"] == "https://vk.com/wall-7_11"

    def test_owner_ids_filter_drops_foreign_walls(self):
        client = _client()
        client._owner_ids_filter = {"-7"}
        items = client._normalize_response(
            self._raw([self._item(owner_id=-7, post_id=1), self._item(owner_id=-9, post_id=2)]),
            SourceType.SEARCH,
        )
        assert [i["owner_id"] for i in items] == [-7]

    def test_no_owner_filter_keeps_all_walls(self):
        items = _client()._normalize_response(
            self._raw([self._item(owner_id=-7, post_id=1), self._item(owner_id=-9, post_id=2)]),
            SourceType.SEARCH,
        )
        assert len(items) == 2
