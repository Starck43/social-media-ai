"""Tests for VK screen_name -> numeric ID resolution during collection.

A VK source can store either a numeric ID or a short name (screen_name) in
`external_id` (docs/TENANCY.md, admin "Внешний ID источника"). The VK API only
accepts numeric `owner_id`, so `VKClient` resolves a screen_name via
`utils.resolveScreenName` once per run and uses the numeric result when
building request params. These tests pin that behaviour.
"""

import uuid

import httpx
import pytest

from app.models import Platform, Source
from app.services.social.vk_client import VKClient
from app.types import SourceType


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self):
        self.calls = []
        self.script = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, **kwargs):
        self.calls.append((url, params))
        if not self.script:
            raise AssertionError("no scripted response left for vk screen_name resolution")
        return FakeResponse(self.script.pop(0))


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type="vk",
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def user_source(platform):
    s = await Source.objects.create(
        platform_id=platform.id,
        name="natalia",
        source_type=SourceType.USER.name,
        external_id="russkikh_natalia",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


def _patch_httpx(monkeypatch, fake_client):
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake_client)


async def test_resolve_user_screen_name(monkeypatch, platform):
    fake = FakeClient()
    fake.script = [{"response": {"object_id": 123456, "type": "user"}}]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    resolved = await client._resolve_external_id("russkikh_natalia")

    assert resolved == "123456"
    url, params = fake.calls[0]
    assert url.endswith("utils.resolveScreenName")
    assert params["screen_name"] == "russkikh_natalia"
    assert params["access_token"] == "test-token"


async def test_resolve_group_screen_name_gets_minus_prefix(monkeypatch, platform):
    fake = FakeClient()
    fake.script = [{"response": {"object_id": 999, "type": "group"}}]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    resolved = await client._resolve_external_id("some_club")

    assert resolved == "-999"


async def test_resolve_leaves_numeric_untouched(monkeypatch, platform):
    fake = FakeClient()
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"

    assert await client._resolve_external_id("12345") == "12345"
    assert await client._resolve_external_id("-54321") == "-54321"
    assert fake.calls == []


async def test_build_params_uses_resolved_owner_id(monkeypatch, platform, user_source):
    fake = FakeClient()
    fake.script = [{"response": {"object_id": 123456, "type": "user"}}]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    client._resolved_external_id = await client._resolve_external_id(user_source.external_id)

    params = client._build_params(user_source, "wall.get")
    assert params["owner_id"] == "123456"
