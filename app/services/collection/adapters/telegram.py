"""Telegram collection adapter: channel pull plus explicit unsupported lanes.

Telegram has no global post/people/city search in the bot/MTProto contract used
by this project. The adapter therefore reports unsupported operations instead
of returning an empty page. Existing channel pull is reused through the current
Telegram client; comments/likes/reposts stay unsupported until a later stage.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.services.collection.adapters.base import CollectionProvider
from app.services.collection.pages import (
    Candidate,
    CityRef,
    Item,
    Page,
    Profile,
    ProviderCapabilities,
)
from app.services.social.tg_client import TelegramClient

TELEGRAM_CAPABILITIES = ProviderCapabilities(
    city_lookup=False,
    people_search=False,
    community_search=False,
    post_search=False,
    wall=True,
    comments=False,
    likes=False,
    reposts=False,
    profiles=False,
)


class TelegramCollectionAdapter(CollectionProvider):
    """Channel pull and honest capability gaps for Telegram."""

    platform = "telegram"
    capabilities = TELEGRAM_CAPABILITIES

    def __init__(self, client: TelegramClient) -> None:
        self.client = client

    async def resolve_city(self, name: str) -> CityRef | None:
        return None

    async def search_people(
        self,
        query: str,
        city: CityRef | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        return Page.unsupported("telegram_people_search_unsupported")

    async def search_communities(
        self,
        query: str,
        city: CityRef | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        return Page.unsupported("telegram_community_search_unsupported")

    async def search_posts(
        self,
        query: str,
        since: Any,
        until: Any,
        limit: int,
        cursor: str | None,
        owner_ids: Sequence[str] | None = None,
    ) -> Page[Item]:
        return Page.unsupported("telegram_post_search_unsupported")

    async def fetch_wall(
        self,
        source: Any,
        since: Any,
        until: Any,
        limit: int,
        cursor: str | None,
    ) -> Page[Item]:
        items = await self.client.collect_data(source, "posts")
        rows = [self._item(item) for item in items if isinstance(item, dict)]
        bounded = rows[: max(1, int(limit))]
        next_cursor = None
        if len(rows) > len(bounded) and bounded:
            next_cursor = str(bounded[-1].get("external_id") or "")
        return Page(
            items=tuple(bounded),
            next_cursor=next_cursor,
            limit_reached=len(rows) >= limit,
        )

    async def fetch_comments(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Item]:
        return Page.unsupported("telegram_comments_unsupported")

    async def fetch_likes(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        return Page.unsupported("telegram_likes_unsupported")

    async def fetch_reposts(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]:
        return Page.unsupported("telegram_reposts_unsupported")

    async def fetch_profiles(self, ids: Sequence[str]) -> list[Profile]:
        return []

    @staticmethod
    def _item(item: dict[str, Any]) -> Item:
        return {
            **item,
            "platform": item.get("platform") or "telegram",
            "content_type": item.get("content_type") or "post",
        }
