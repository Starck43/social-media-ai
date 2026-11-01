"""VK collection adapter: normalised pages over the existing VKClient.

The adapter reuses the client's token/layer rules and normalisation. It never
writes rows, advances `last_checked` or calls a model. Stage C wires it to the
collector; stage B exercises it with mocked client responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.core.config import settings
from app.services.collection.adapters.base import CollectionProvider
from app.services.collection.pages import (
    Candidate,
    CityRef,
    CollectionProviderError,
    Item,
    Page,
    Profile,
    ProviderCapabilities,
    decode_offset_cursor,
    encode_offset_cursor,
)
from app.services.social.credentials import AuthorizationRequired
from app.services.social.rate_limit import SourceRateLimited
from app.services.social.vk_client import VKClient
from app.types import SourceType
from app.utils.date_parsing import to_unix_timestamp, universal_date_parser

VK_CAPABILITIES = ProviderCapabilities(
    city_lookup=True,
    people_search=True,
    community_search=True,
    post_search=True,
    wall=True,
    comments=True,
    likes=True,
    reposts=True,
    profiles=True,
)

_SEARCH_CEILING = 1000
_PAGE_CEILING = 100


class VKCollectionAdapter(CollectionProvider):
    """Typed collection primitives for VK."""

    platform = "vk"
    capabilities = VK_CAPABILITIES

    def __init__(self, client: VKClient, *, owner_user_id: int | None = None) -> None:
        self.client = client
        self.owner_user_id = owner_user_id

    async def resolve_city(self, name: str) -> CityRef | None:
        try:
            cities = await self.client.get_cities(name, owner_user_id=self.owner_user_id, count=5)
        except Exception as error:  # noqa: BLE001 - translated to a bounded provider error
            self._raise_translated(error)
        if not cities:
            return None
        first = cities[0]
        return CityRef(
            id=_as_int(first.get("id")),
            title=str(first.get("title") or name),
            region=first.get("region"),
            country=first.get("country"),
            raw=dict(first),
        )

    async def search_people(
        self,
        query: str,
        city: CityRef | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        try:
            rows = await self.client.search_users(
                query,
                owner_user_id=self.owner_user_id,
                city_id=city.id if city is not None else None,
                offset=offset,
                count=count,
            )
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        candidates = _valid_candidates(self._person(row) for row in rows)
        return self._page(
            candidates,
            raw_count=len(rows),
            offset=offset,
            page_size=count,
            limit=limit,
            total=None,
            search=False,
            warnings=(),
        )

    async def search_communities(
        self,
        query: str,
        city: CityRef | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        try:
            rows = await self.client.search_groups(
                query,
                owner_user_id=self.owner_user_id,
                city_id=city.id if city is not None else None,
                offset=offset,
                count=count,
            )
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        candidates = _valid_candidates(self._community(row) for row in rows)
        return self._page(
            candidates,
            raw_count=len(rows),
            offset=offset,
            page_size=count,
            limit=limit,
            total=None,
            search=False,
            warnings=(),
        )

    async def search_videos(
        self,
        query: str,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        """`video.search`: typed video results (P8-T46).

        Mocked contract while Q-5 owns live method/profile evidence; a video
        result is a candidate, never a Source. The `video.search` ceiling is
        offset-bounded like the other VK search methods.
        """
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        params: dict[str, Any] = {"q": query, "count": count, "offset": offset, "extended": 1}
        try:
            data = await self.client._search_response(
                "video.search",
                params,
                self.owner_user_id,
                require_user=False,
            )
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        raw_items = data.get("items") if isinstance(data, Mapping) else None
        rows = [row for row in (raw_items or []) if isinstance(row, Mapping)]
        candidates = _valid_candidates(self._video(row) for row in rows)
        total = _as_int(data.get("count")) if isinstance(data, Mapping) else None
        warnings: list[str] = []
        if offset + len(rows) >= _SEARCH_CEILING:
            warnings.append("vk_search_offset_ceiling")
        return self._page(
            candidates,
            raw_count=len(rows),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=True,
            warnings=tuple(warnings),
        )

    async def search_apps(
        self,
        query: str,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        """`apps.getCatalog` with the official `q` catalog search string (P8-T47).

        Mocked contract while Q-5 owns live method/profile evidence; an app or
        game result is a candidate, never a Source. Official quota note: the
        method allows at most 60 requests per minute per IP or id; the shared
        page/search ceilings keep paging bounded below that.
        """
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        params: dict[str, Any] = {
            "q": query,
            "count": count,
            "offset": offset,
            "extended": 1,
            "platform": "web",
        }
        try:
            data = await self.client._search_response(
                "apps.getCatalog",
                params,
                self.owner_user_id,
                require_user=False,
            )
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        raw_items = data.get("items") if isinstance(data, Mapping) else None
        rows = [row for row in (raw_items or []) if isinstance(row, Mapping)]
        candidates = _valid_candidates(self._app(row) for row in rows)
        total = _as_int(data.get("count")) if isinstance(data, Mapping) else None
        warnings: list[str] = []
        if offset + len(rows) >= _SEARCH_CEILING:
            warnings.append("vk_search_offset_ceiling")
        return self._page(
            candidates,
            raw_count=len(rows),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=True,
            warnings=tuple(warnings),
        )

    async def search_market_items(
        self,
        query: str,
        owner_id: int,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        """`market.search` scoped to the selected owner/community (P8-T48).

        Mocked contract while Q-5 owns live method/profile evidence; a market
        item is a candidate, never a Source unless the user explicitly adds a
        supported target. ``owner_id`` is required and negative for communities,
        exactly as VK scopes market catalogues.
        """
        if not isinstance(owner_id, int) or isinstance(owner_id, bool):
            raise CollectionProviderError("vk_owner_invalid", "market search requires a numeric owner id")
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        params: dict[str, Any] = {
            "owner_id": owner_id,
            "q": query,
            "count": count,
            "offset": offset,
            "extended": 1,
        }
        try:
            data = await self.client._search_response(
                "market.search",
                params,
                self.owner_user_id,
                require_user=False,
            )
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        raw_items = data.get("items") if isinstance(data, Mapping) else None
        rows = [row for row in (raw_items or []) if isinstance(row, Mapping)]
        candidates = _valid_candidates(self._market_item(row) for row in rows)
        total = _as_int(data.get("count")) if isinstance(data, Mapping) else None
        warnings: list[str] = []
        if offset + len(rows) >= _SEARCH_CEILING:
            warnings.append("vk_search_offset_ceiling")
        return self._page(
            candidates,
            raw_count=len(rows),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=True,
            warnings=tuple(warnings),
        )

    async def search_audios(
        self,
        query: str,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        """`audio.search` mocked contract (P8-T49).

        Not bound in the search-category registry: music keeps its separate
        permission gate (`deferred`, `separate_permission_evidence_required`)
        because the official `audio` access right is granted only per request
        via VK support. This operation exists so the contract is testable; it
        must not be dispatched until that evidence is recorded, and no audio
        scope may be requested for post research. An audio result is never a
        Source.
        """
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        params: dict[str, Any] = {"q": query, "count": count, "offset": offset}
        try:
            data = await self.client._search_response(
                "audio.search",
                params,
                self.owner_user_id,
                require_user=False,
            )
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        raw_items = data.get("items") if isinstance(data, Mapping) else None
        rows = [row for row in (raw_items or []) if isinstance(row, Mapping)]
        candidates = _valid_candidates(self._audio(row) for row in rows)
        total = _as_int(data.get("count")) if isinstance(data, Mapping) else None
        warnings: list[str] = []
        if offset + len(rows) >= _SEARCH_CEILING:
            warnings.append("vk_search_offset_ceiling")
        return self._page(
            candidates,
            raw_count=len(rows),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=True,
            warnings=tuple(warnings),
        )

    async def search_posts(
        self,
        query: str,
        since: Any,
        until: Any,
        limit: int,
        cursor: str | None,
        owner_ids: Sequence[str] | None = None,
    ) -> Page[Item]:
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        params: dict[str, Any] = {
            "q": query,
            "count": count,
            "offset": offset,
            "extended": 1,
            "fields": "screen_name,is_closed,type,members_count",
        }
        start_time = _timestamp(since)
        end_time = _timestamp(until)
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time

        previous_profiles = getattr(self.client, "_profile_names", None)
        previous_groups = getattr(self.client, "_group_names", None)
        previous_filter = getattr(self.client, "_owner_ids_filter", None)
        try:
            self.client._profile_names = {}
            self.client._group_names = {}
            self.client._owner_ids_filter = {str(value) for value in owner_ids} if owner_ids else None
            try:
                data = await self.client._search_response(
                    "newsfeed.search",
                    params,
                    self.owner_user_id,
                    require_user=False,
                )
            except Exception as error:  # noqa: BLE001
                self._raise_translated(error)
            raw_items = self.client._extract_items_from_response({"response": data})
            normalized = self.client._normalize_response({"response": data}, SourceType.SEARCH)
            normalized = self._filter_window(normalized, since, until)
            items = [self._item(row) for row in normalized]
        finally:
            self.client._profile_names = previous_profiles
            self.client._group_names = previous_groups
            self.client._owner_ids_filter = previous_filter

        total = _as_int(data.get("total_count")) or _as_int(data.get("count"))
        warnings: list[str] = []
        if offset + len(raw_items) >= _SEARCH_CEILING:
            warnings.append("vk_search_offset_ceiling")
        return self._page(
            items,
            raw_count=len(raw_items),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=True,
            warnings=tuple(warnings),
        )

    async def fetch_wall(
        self,
        source: Any,
        since: Any,
        until: Any,
        limit: int,
        cursor: str | None,
    ) -> Page[Item]:
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        token = await self._ensure_token()
        source_type = _source_type(source)
        owner_id = _owner_id(self.client, source, source_type)
        params: dict[str, Any] = {
            "access_token": token,
            "v": _api_version(self.client),
            "owner_id": owner_id,
            "count": count,
            "offset": offset,
            "extended": 1,
            "filter": "all",
        }
        start_time = _timestamp(since)
        end_time = _timestamp(until)
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time

        previous_profiles = getattr(self.client, "_profile_names", None)
        previous_groups = getattr(self.client, "_group_names", None)
        try:
            self.client._profile_names = {}
            self.client._group_names = {}
            try:
                response = await self.client._fetch_page("wall.get", params)
            except Exception as error:  # noqa: BLE001
                self._raise_translated(error)
            raw_items = self.client._extract_items_from_response(response)
            normalized = self.client._normalize_response(response, source_type)
            normalized = self._filter_window(normalized, since, until)
            items = [self._item(row) for row in normalized]
        finally:
            self.client._profile_names = previous_profiles
            self.client._group_names = previous_groups

        data = response.get("response", {}) if isinstance(response, dict) else {}
        total = _as_int(data.get("count"))
        return self._page(
            items,
            raw_count=len(raw_items),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=False,
            warnings=(),
        )

    async def fetch_comments(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Item]:
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        token = await self._ensure_token()
        owner_id = _post_owner_id(post)
        post_id = _post_id(post)
        post_external_id = str(post.get("external_id") or f"{owner_id}_{post_id}")
        params = {
            "access_token": token,
            "v": _api_version(self.client),
            "owner_id": owner_id,
            "post_id": post_id,
            "count": count,
            "offset": offset,
            "extended": 1,
            "fields": "screen_name,city,verified,followers_count",
        }
        try:
            response = await self.client._fetch_page("wall.getComments", params)
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        data = response.get("response", {}) if isinstance(response, dict) else {}
        raw_items = data.get("items") or []
        profiles = {
            _as_int(profile.get("id")): profile
            for profile in (data.get("profiles") or [])
            if isinstance(profile, dict) and profile.get("id") is not None
        }
        items = [
            self._comment(
                item, owner_id=owner_id, post_id=post_id, post_external_id=post_external_id, profiles=profiles
            )
            for item in raw_items
            if isinstance(item, dict)
        ]
        total = _as_int(data.get("count"))
        return self._page(
            items,
            raw_count=len(raw_items),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=False,
            warnings=(),
        )

    async def fetch_likes(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        token = await self._ensure_token()
        owner_id = _post_owner_id(post)
        post_id = _post_id(post)
        params = {
            "access_token": token,
            "v": _api_version(self.client),
            "type": "post",
            "owner_id": owner_id,
            "item_id": post_id,
            "count": count,
            "offset": offset,
            "extended": 1,
            "fields": "screen_name,city,verified,followers_count",
        }
        try:
            response = await self.client._fetch_page("likes.getList", params)
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        data = response.get("response", {}) if isinstance(response, dict) else {}
        raw_items = data.get("items") or []
        candidates = _valid_candidates(
            self._person(row if isinstance(row, Mapping) else {"id": row})
            for row in raw_items
            if isinstance(row, Mapping) or isinstance(row, (int, str))
        )
        total = _as_int(data.get("count"))
        return self._page(
            candidates,
            raw_count=len(raw_items),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=False,
            warnings=(),
        )

    async def fetch_reposts(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        offset = decode_offset_cursor(cursor)
        count = _page_size(limit)
        token = await self._ensure_token()
        owner_id = _post_owner_id(post)
        post_id = _post_id(post)
        params = {
            "access_token": token,
            "v": _api_version(self.client),
            "owner_id": owner_id,
            "post_id": post_id,
            "count": count,
            "offset": offset,
            "extended": 1,
            "fields": "screen_name,city,verified,followers_count",
        }
        try:
            response = await self.client._fetch_page("wall.getReposts", params)
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        data = response.get("response", {}) if isinstance(response, dict) else {}
        raw_items = data.get("items") or []
        candidates = []
        for row in raw_items:
            if isinstance(row, Mapping):
                profile = row.get("user") if isinstance(row.get("user"), Mapping) else row
                candidate = self._person(profile)
            elif isinstance(row, (int, str)):
                candidate = self._person({"id": row})
            else:
                continue
            if candidate["external_id"]:
                candidates.append(candidate)
        total = _as_int(data.get("count"))
        return self._page(
            candidates,
            raw_count=len(raw_items),
            offset=offset,
            page_size=count,
            limit=limit,
            total=total,
            search=False,
            warnings=(),
        )

    async def fetch_profiles(self, ids: Sequence[str]) -> list[Profile]:
        token = await self._ensure_token()
        users = [str(value).lstrip("-") for value in ids if str(value).lstrip("-").isdigit() and int(value) > 0]
        groups = [str(abs(int(value))) for value in ids if str(value).lstrip("-").isdigit() and int(value) < 0]
        profiles: list[Profile] = []
        if users:
            response = await self.client._fetch_page(
                "users.get",
                {
                    "access_token": token,
                    "v": _api_version(self.client),
                    "user_ids": ",".join(users),
                    "fields": "screen_name,city,verified,followers_count,occupation",
                },
            )
            profiles.extend(self._profile(row) for row in _response_items(response))
        if groups:
            response = await self.client._fetch_page(
                "groups.getById",
                {
                    "access_token": token,
                    "v": _api_version(self.client),
                    "group_ids": ",".join(groups),
                    "fields": "description,members_count,activity,verified",
                },
            )
            profiles.extend(self._community(row) for row in _response_items(response))
        return profiles

    async def _ensure_token(self) -> str:
        try:
            token = self.client._token()
            if token:
                return token
            token = await self.client._search_token(self.owner_user_id, require_user=False)
        except Exception as error:  # noqa: BLE001
            self._raise_translated(error)
        self.client._access_token = token
        return token

    def _filter_window(self, rows: list[dict[str, Any]], since: Any, until: Any) -> list[dict[str, Any]]:
        # `wall.get` ignores start_time/end_time, so the window is enforced
        # client-side exactly as the existing collector does. The API params are
        # still sent for methods that do honour them.
        if since:
            rows = self.client._filter_items_by_date(rows, since)
        if until:
            rows = self.client._filter_items_before_date(rows, until)
        return rows

    def _page(
        self,
        items: list[Any],
        *,
        raw_count: int,
        offset: int,
        page_size: int,
        limit: int,
        total: int | None,
        search: bool,
        warnings: tuple[str, ...],
    ) -> Page[Any]:
        limit_reached = raw_count >= limit
        next_offset = offset + raw_count
        exhausted = raw_count < page_size
        if total is not None:
            exhausted = exhausted or next_offset >= total
        if search:
            exhausted = exhausted or next_offset >= _SEARCH_CEILING
        next_cursor = None if limit_reached or exhausted else encode_offset_cursor(next_offset)
        return Page(
            items=tuple(items),
            next_cursor=next_cursor,
            limit_reached=limit_reached,
            total_available=total,
            warnings=warnings,
        )

    @staticmethod
    def _item(row: Mapping[str, Any]) -> Item:
        published = row.get("date")
        if isinstance(published, datetime) and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        item: dict[str, Any] = {
            "platform": "vk",
            "content_type": "post",
            "external_id": row.get("external_id") or row.get("id"),
            "text": row.get("text") or "",
            "published_at": published,
            "author": row.get("author"),
            "metrics": {
                "reactions": row.get("reactions"),
                "comments": row.get("comments"),
                "shares": row.get("shares"),
                "views": row.get("views"),
                "metric_availability": row.get("metric_availability"),
            },
            "permalink": row.get("permalink"),
            "owner_id": row.get("owner_id"),
            "post_id": row.get("id"),
            "source_type": row.get("source_type"),
        }
        if "attachments" in row and isinstance(row.get("attachments"), list):
            item["attachments"] = [dict(att) for att in row["attachments"] if isinstance(att, dict)]
        for extra_key in ("has_attachments", "attachment_types", "media_type"):
            if extra_key in row:
                item[extra_key] = row[extra_key]
        return item

    @staticmethod
    def _comment(
        row: Mapping[str, Any],
        *,
        owner_id: int,
        post_id: int,
        post_external_id: str,
        profiles: Mapping[int, Mapping[str, Any]],
    ) -> Item:
        comment_id = row.get("id")
        author_id = _as_int(row.get("from_id"))
        profile = profiles.get(author_id) if author_id is not None else None
        author = None
        if author_id is not None:
            author = {"platform": "vk", "external_id": str(author_id), "id": author_id}
            name = _profile_name(profile) if profile is not None else None
            if name:
                author["name"] = name
        published = _datetime(row.get("date"))
        comment_item: dict[str, Any] = {
            "platform": "vk",
            "content_type": "comment",
            "external_id": f"{post_external_id}:{comment_id}",
            "parent_external_id": post_external_id,
            "text": row.get("text") or "",
            "published_at": published,
            "author": author,
            "metrics": {
                "reactions": (row.get("likes") or {}).get("count") if isinstance(row.get("likes"), dict) else None,
            },
            "permalink": f"https://vk.com/wall{owner_id}_{post_id}?reply={comment_id}",
            "owner_id": owner_id,
            "post_id": post_id,
            "comment_id": comment_id,
        }
        if "attachments" in row and isinstance(row.get("attachments"), list):
            comment_item["attachments"] = VKClient._normalize_direct_attachments(row["attachments"])
        return comment_item

    @staticmethod
    def _person(row: Mapping[str, Any]) -> Candidate:
        external_id = row.get("external_id") or row.get("id") or row.get("user_id")
        username = row.get("screen_name")
        return {
            "platform": "vk",
            "kind": "person",
            "external_id": str(external_id) if external_id is not None else None,
            "name": row.get("name") or _profile_name(row),
            "url": (
                f"https://vk.com/{username}"
                if username
                else (f"https://vk.com/id{external_id}" if external_id else None)
            ),
            "city": row.get("city"),
            "metrics": {
                "followers_count": row.get("followers_count"),
                "verified": row.get("verified"),
            },
            "evidence": {},
        }

    @staticmethod
    def _video(row: Mapping[str, Any]) -> Candidate:
        video_id = row.get("id")
        owner_id = _as_int(row.get("owner_id"))
        external_id = f"{owner_id}_{video_id}" if owner_id is not None and video_id is not None else None
        return {
            "platform": "vk",
            "kind": "video",
            "external_id": external_id,
            "name": row.get("title"),
            "url": f"https://vk.com/video{external_id}" if external_id else None,
            "city": None,
            "metrics": {
                "views": row.get("views"),
                "duration": row.get("duration"),
            },
            "evidence": {
                "owner_id": owner_id,
                "video_id": video_id,
            },
        }

    @staticmethod
    def _app(row: Mapping[str, Any]) -> Candidate:
        app_id = row.get("id")
        screen_name = row.get("screen_name")
        return {
            "platform": "vk",
            "kind": "app",
            "external_id": str(app_id) if app_id is not None else None,
            "name": row.get("title"),
            "url": (
                f"https://vk.com/{screen_name}"
                if screen_name
                else (f"https://vk.com/app{app_id}" if app_id is not None else None)
            ),
            "city": None,
            "metrics": {
                "members_count": row.get("members_count"),
                "app_type": row.get("type"),
            },
            "evidence": {
                "app_id": app_id,
            },
        }

    @staticmethod
    def _market_item(row: Mapping[str, Any]) -> Candidate:
        item_id = row.get("id")
        owner_id = _as_int(row.get("owner_id"))
        external_id = f"{owner_id}_{item_id}" if owner_id is not None and item_id is not None else None
        price = row.get("price") if isinstance(row.get("price"), Mapping) else {}
        amount = price.get("amount")
        try:
            price_value = int(amount) / 100 if amount is not None else None
        except (TypeError, ValueError):
            price_value = None
        currency = price.get("currency") if isinstance(price.get("currency"), Mapping) else {}
        return {
            "platform": "vk",
            "kind": "market_item",
            "external_id": external_id,
            "name": row.get("title"),
            "url": f"https://vk.com/market{owner_id}?w=product{external_id}" if external_id else None,
            "city": None,
            "metrics": {
                "price": price_value,
                "currency": currency.get("name"),
                "availability": row.get("availability"),
            },
            "evidence": {
                "owner_id": owner_id,
                "market_item_id": item_id,
            },
        }

    @staticmethod
    def _audio(row: Mapping[str, Any]) -> Candidate:
        audio_id = row.get("id")
        owner_id = _as_int(row.get("owner_id"))
        external_id = f"{owner_id}_{audio_id}" if owner_id is not None and audio_id is not None else None
        artist = row.get("artist")
        title = row.get("title")
        name = f"{artist} — {title}" if artist and title else (title or artist)
        return {
            "platform": "vk",
            "kind": "audio",
            "external_id": external_id,
            "name": name,
            "url": f"https://vk.com/audio{external_id}" if external_id else None,
            "city": None,
            "metrics": {
                "duration": row.get("duration"),
            },
            "evidence": {
                "owner_id": owner_id,
                "audio_id": audio_id,
            },
        }

    @staticmethod
    def _community(row: Mapping[str, Any]) -> Candidate:
        external_id = row.get("external_id") or row.get("id")
        # VK community owner ids are negative. `groups.search` already returns
        # the negative form; `groups.getById` returns a positive id.
        if external_id is not None and str(external_id).lstrip("-").isdigit() and int(external_id) > 0:
            external_id = f"-{external_id}"
        return {
            "platform": "vk",
            "kind": "community",
            "external_id": str(external_id) if external_id is not None else None,
            "name": row.get("name"),
            "url": f"https://vk.com/{row.get('screen_name')}" if row.get("screen_name") else None,
            "city": row.get("city"),
            "metrics": {
                "members_count": row.get("members_count"),
                "is_closed": row.get("is_closed"),
            },
            "evidence": {},
        }

    @staticmethod
    def _profile(row: Mapping[str, Any]) -> Profile:
        return {
            "platform": "vk",
            "kind": "person",
            "external_id": str(row.get("id")) if row.get("id") is not None else None,
            "name": _profile_name(row),
            "screen_name": row.get("screen_name"),
            "city": (row.get("city") or {}).get("title") if isinstance(row.get("city"), dict) else row.get("city"),
            "followers_count": row.get("followers_count"),
            "verified": row.get("verified"),
        }

    @staticmethod
    def _raise_translated(error: Exception) -> None:
        if isinstance(error, CollectionProviderError):
            raise error
        text = str(error)
        if "1051" in text:
            raise CollectionProviderError(
                "credentials",
                "VK search is unavailable for the configured application profile (1051)",
            ) from error
        if isinstance(error, AuthorizationRequired):
            raise CollectionProviderError("credentials", str(error)) from error
        if isinstance(error, SourceRateLimited):
            raise CollectionProviderError("rate_limited", "VK rate limit did not clear", retryable=True) from error
        raise error


def _page_size(limit: int) -> int:
    if limit <= 0:
        raise ValueError("collection_limit_invalid")
    return min(int(limit), _PAGE_CEILING)


def _api_version(client: VKClient) -> str:
    return (getattr(client.platform, "params", None) or {}).get("api_version") or settings.VK_API_VERSION


def _source_type(source: Any) -> SourceType:
    value = getattr(source, "source_type", SourceType.GROUP)
    if isinstance(value, SourceType):
        return value
    found = SourceType.get_by_value(str(value).lower())
    return found or SourceType.GROUP


def _owner_id(client: VKClient, source: Any, source_type: SourceType) -> int:
    raw = client._parse_owner_id(str(getattr(source, "external_id", "")), source_type)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise CollectionProviderError("vk_owner_invalid", "VK source has no numeric owner id") from exc


def _post_owner_id(post: Mapping[str, Any]) -> int:
    value = post.get("owner_id")
    if value is None and post.get("external_id") and "_" in str(post["external_id"]):
        value = str(post["external_id"]).split("_", 1)[0]
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CollectionProviderError("vk_post_owner_invalid", "post has no VK owner id") from exc


def _post_id(post: Mapping[str, Any]) -> int:
    value = post.get("post_id") or post.get("id")
    if value is None and post.get("external_id") and "_" in str(post["external_id"]):
        value = str(post["external_id"]).split("_", 1)[1]
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CollectionProviderError("vk_post_id_invalid", "post has no VK post id") from exc


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> int | None:
    if value is None or value == "":
        return None
    parsed = universal_date_parser(value)
    if parsed is None:
        return None
    return to_unix_timestamp(parsed)


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def _valid_candidates(rows: Iterable[Candidate]) -> list[Candidate]:
    return [row for row in rows if row.get("external_id")]


def _profile_name(row: Mapping[str, Any] | None) -> str | None:
    if not isinstance(row, Mapping):
        return None
    name = " ".join(str(part) for part in (row.get("first_name"), row.get("last_name")) if part)
    return name or row.get("name")


def _response_items(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = response.get("response") if isinstance(response, Mapping) else None
    if isinstance(data, Mapping):
        items = data.get("items")
    else:
        items = data
    if isinstance(items, list):
        return [item for item in items if isinstance(item, Mapping)]
    return []
