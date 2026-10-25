"""Provider protocol shared by VK and Telegram collection adapters."""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from app.services.collection.pages import (
    Candidate,
    CityRef,
    Item,
    Page,
    Profile,
    ProviderCapabilities,
)


@runtime_checkable
class CollectionProvider(Protocol):
    """One platform's typed collection primitives.

    A provider normalises platform payloads and reports bounds. It never
    decides task selection, persists rows, advances cursors or calls a model.
    """

    platform: str
    capabilities: ProviderCapabilities

    async def resolve_city(self, name: str) -> CityRef | None: ...

    async def search_people(
        self,
        query: str,
        city: CityRef | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]: ...

    async def search_communities(
        self,
        query: str,
        city: CityRef | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]: ...

    async def search_posts(
        self,
        query: str,
        since: Any,
        until: Any,
        limit: int,
        cursor: str | None,
        owner_ids: Sequence[str] | None = None,
    ) -> Page[Item]: ...

    async def fetch_wall(
        self,
        source: Any,
        since: Any,
        until: Any,
        limit: int,
        cursor: str | None,
    ) -> Page[Item]: ...

    async def fetch_comments(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Item]: ...

    async def fetch_likes(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]: ...

    async def fetch_reposts(
        self,
        post: Item,
        limit: int,
        cursor: str | None,
    ) -> Page[Candidate]: ...

    async def fetch_profiles(self, ids: Sequence[str]) -> list[Profile]: ...
