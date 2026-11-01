"""Token-free search client doubles and fail-closed external-I/O tripwires."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.collection.adapters.vk import VKCollectionAdapter


@pytest.fixture(autouse=True)
def no_external_search_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Search contract tests must not access network, credentials or storage")

    import sqlalchemy.ext.asyncio

    from app.services.social import credentials, vk_client

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(sqlalchemy.ext.asyncio.AsyncEngine, "connect", forbidden)
    monkeypatch.setattr(sqlalchemy.ext.asyncio.AsyncSession, "execute", forbidden)
    monkeypatch.setattr(credentials, "resolve_token", forbidden)
    monkeypatch.setattr(vk_client, "resolve_token", forbidden)
    monkeypatch.setattr(vk_client.VKClient, "_search_token", forbidden)
    monkeypatch.setattr(vk_client.VKClient, "_make_request", forbidden)


def search_adapter(response=None, *, error=None):
    """The adapter sees only a mock transport; no VKClient or token is created."""
    client = SimpleNamespace(_search_response=AsyncMock(return_value=response, side_effect=error))
    return VKCollectionAdapter(client, owner_user_id=42), client._search_response


async def run_search(adapter, category, *, limit=10, cursor=None, owner_id=-7):
    operation = {
        "video": adapter.search_videos,
        "apps": adapter.search_apps,
        "market": adapter.search_market_items,
        "music": adapter.search_audios,
    }[category]
    if category == "market":
        return await operation("design", owner_id, limit, cursor)
    return await operation("design", limit, cursor)


ROWS = {
    "video": {"id": 11, "owner_id": -7, "title": "Demo", "views": 12, "duration": 30},
    "apps": {"id": 11, "title": "Demo", "type": "game", "members_count": 12},
    "market": {
        "id": 11,
        "owner_id": -7,
        "title": "Demo",
        "price": {"amount": "12345", "currency": {"name": "RUB"}},
        "availability": 0,
    },
    "music": {"id": 11, "owner_id": -7, "artist": "Artist", "title": "Demo", "duration": 30},
}
METHODS = {"video": "video.search", "apps": "apps.getCatalog", "market": "market.search", "music": "audio.search"}


def quota_adapter(responses):
    """Execute the real bounded BaseClient read/retry loop, but never token code."""
    from app.services.social.vk_client import VKClient

    client = VKClient(SimpleNamespace(platform_type="vk", params={}))
    transport = AsyncMock(side_effect=responses)
    client._make_request = transport

    async def search_response(method, params, owner_user_id, *, require_user):
        return (await client._fetch_page(method, params)).get("response")

    adapter = VKCollectionAdapter(SimpleNamespace(_search_response=search_response), owner_user_id=42)
    return adapter, transport
