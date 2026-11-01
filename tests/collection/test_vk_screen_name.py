"""Tests for VK screen_name -> numeric ID resolution during collection.

A VK source can store either a numeric ID or a short name (screen_name) in
`external_id` (docs/TENANCY.md, admin "Внешний ID источника"). The VK API only
accepts numeric `owner_id`, so `VKClient` resolves a screen_name once per run:
`users.get` for a person, then `groups.getById` for a community, and finally
`utils.resolveScreenName` as a last resort (it answers 1051 for this app
profile type). These tests pin that behaviour.
"""

import uuid

import httpx
import pytest

from app.models import Platform, Source
from app.services.social.vk_client import VKClient
from app.types import SourceType
from tests.shared.platform_catalog import shared_platform


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
    p = await shared_platform("vk")
    yield p


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
    # users.get answers empty (not scripted as a person), groups.getById finds
    # no community, then resolveScreenName answers.
    fake.script = [
        {"response": []},
        {"response": {}},
        {"response": {"object_id": 123456, "type": "user"}},
    ]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    resolved = await client._resolve_external_id("russkikh_natalia")

    assert resolved == "123456"
    url, params = fake.calls[-1]
    assert url.endswith("utils.resolveScreenName")
    assert params["screen_name"] == "russkikh_natalia"
    assert params["access_token"] == "test-token"


async def test_resolve_group_screen_name_gets_minus_prefix(monkeypatch, platform):
    fake = FakeClient()
    # users.get empty → groups.getById resolves the community (its group id).
    fake.script = [
        {"response": []},
        {"response": {"groups": [{"id": 999, "type": "group"}]}},
    ]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    resolved = await client._resolve_external_id("some_club")

    assert resolved == "-999"
    assert fake.calls[-1][0].endswith("groups.getById")


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
    fake.script = [{"response": [{"id": 123456}]}]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    client._resolved_external_id = await client._resolve_external_id(user_source.external_id)

    params = client._build_params(user_source, "wall.get")
    assert params["owner_id"] == "123456"


class _UserThenScreenName(FakeClient):
    """`users.get` answers first; the script holds its answer, then resolveScreenName's."""


async def test_user_nick_resolves_even_when_resolve_screen_name_is_refused(monkeypatch, platform):
    """A service key gets error 1051 from resolveScreenName but users.get still maps a nick.

    VK answers «Method is not available for this profile type» for
    utils.resolveScreenName while `users.get(user_ids=<nick>)` returns the same
    person's id. Collection must use that id, not fall back to the raw nick.
    """
    fake = FakeClient()
    fake.script = [
        {"response": [{"id": 19149862, "first_name": "Anna"}]},
        {"error": {"error_code": 1051, "error_msg": "Method is not available for this profile type"}},
    ]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    resolved = await client._resolve_external_id("annastrizhovadesign")

    assert resolved == "19149862"
    assert fake.calls[0][0].endswith("users.get")
    assert fake.calls[0][1]["user_ids"] == "annastrizhovadesign"


async def test_community_nick_falls_back_to_resolve_screen_name(monkeypatch, platform):
    """An empty users.get answer means "not a person"; groups.getById is next."""
    fake = FakeClient()
    fake.script = [
        {"response": []},
        {"response": {"groups": [{"id": 777, "type": "group"}]}},
    ]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    assert await client._resolve_external_id("some_club") == "-777"


async def test_community_nick_falls_back_to_resolve_screen_name_when_group_probe_fails(monkeypatch, platform):
    """If groups.getById cannot answer, utils.resolveScreenName is the last resort."""
    fake = FakeClient()
    fake.script = [
        {"response": []},
        {"error": {"error_code": 100, "error_msg": "One of the parameters specified was missing or invalid"}},
        {"response": {"object_id": 888, "type": "group"}},
    ]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    assert await client._resolve_external_id("some_club") == "-888"


async def test_a_failed_user_probe_does_not_invent_an_id(monkeypatch, platform):
    fake = FakeClient()
    fake.script = [
        {"error": {"error_code": 5, "error_msg": "User authorization failed"}},
        {"error": {"error_code": 100, "error_msg": "group_id not defined"}},
        {"error": {"error_code": 1051, "error_msg": "Method is not available for this profile type"}},
    ]
    _patch_httpx(monkeypatch, fake)

    client = VKClient(platform=platform)
    client._access_token = "test-token"
    assert await client._resolve_external_id("unknown_nick") == "unknown_nick"
