"""Canonical JSON configuration contracts, doubled I/O boundaries only."""

from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa

from app.models import Platform
from app.services.social.platform_config import (
    PlatformConfigurationError,
    initial_platform_params,
    load_platform_config,
    require_platform_param,
)
from app.types import PlatformType

__source_only__ = True


def test_creation_defaults_are_materialized_not_shared_or_runtime_fallbacks():
    first = Platform(name="vk", platform_type=PlatformType.VK, base_url="https://vk.com/")
    second = Platform(name="other", platform_type=PlatformType.VK, base_url="https://vk.com")
    assert first.params["api_version"] == "5.199"
    first.params["api_version"] = "custom"
    assert second.params["api_version"] == "5.199"
    assert first.base_url == "https://vk.com"
    assert {"user_id", "tenant_id"}.isdisjoint(Platform.__table__.columns.keys())
    assert any(c.name == "uq_platforms_type" for c in Platform.__table__.constraints)


def test_existing_config_and_arbitrary_nested_keys_survive_creation():
    source = {"api_version": "custom", "options": {"nested": [1]}}
    params = initial_platform_params("vk", source)
    params["options"]["nested"].append(2)
    assert source["options"]["nested"] == [1]
    assert params["api_version"] == "custom"


def test_params_are_mutable_json_and_wrong_shapes_are_rejected():
    platform = Platform(platform_type=PlatformType.VK)
    # Simulate a loaded clean ORM instance without any DB connection.
    sa.orm.attributes.set_committed_value(platform, "params", platform.params)
    platform.params["api_version"] = "changed"
    assert sa.inspect(platform).attrs.params.history.has_changes()
    with pytest.raises(ValueError, match="platform_params_must_be_object"):
        platform.params = []


@pytest.mark.parametrize(
    "params",
    [
        {},
        None,
        {"api_base_url": ""},
        {"api_base_url": "https://" + "name:secret" + "@example.invalid"},
        {"api_base_url": "file:///tmp/local"},
        {"api_base_url": "https://example.invalid?token=x"},
    ],
)
def test_incomplete_or_unsafe_configuration_has_no_environment_fallback(params):
    with pytest.raises(PlatformConfigurationError):
        require_platform_param(NS(params=params, is_active=True), "api_base_url")


async def test_global_lookup_uses_type_not_user_and_sees_edits(monkeypatch):
    row = NS(params={"api_base_url": "https://custom.invalid/v1/", "api_version": "one"}, is_active=True)
    query = AsyncMock(return_value=[row])
    monkeypatch.setattr(Platform.objects, "get_by_type", query)
    assert (await load_platform_config("vk"))["api_version"] == "one"
    row.params["api_version"] = "two"
    assert (await load_platform_config("vk"))["api_version"] == "two"
    query.assert_awaited_with(PlatformType.VK, is_active=None)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [NS(), NS()],
        [NS(params={"api_base_url": "https://example.invalid"}, is_active=False)],
        [NS(params=None, is_active=True)],
    ],
)
async def test_missing_duplicate_and_disabled_catalog_fail_closed(monkeypatch, rows):
    monkeypatch.setattr(Platform.objects, "get_by_type", AsyncMock(return_value=rows))
    with pytest.raises(PlatformConfigurationError):
        await load_platform_config("vk")


async def test_vk_request_params_come_from_shared_json_not_settings(monkeypatch):
    from app.services.social.vk_client import VKClient
    from app.types import SourceType

    platform = NS(params={"api_base_url": "https://custom.invalid", "api_version": "custom"}, is_active=True)
    client = VKClient(platform)
    monkeypatch.setattr(client, "_token", lambda: "synthetic-token")
    source = NS(external_id="-1", source_type=SourceType.GROUP, params={}, last_checked=None)
    assert client._build_params(source, "wall.get")["v"] == "custom"
    platform.params["api_version"] = "updated"
    assert client._build_params(source, "wall.get")["v"] == "updated"


async def test_telegram_channel_uses_shared_endpoint_and_blocks_missing_config(monkeypatch):
    from app.channels import telegram

    monkeypatch.setattr(
        telegram, "load_platform_config", AsyncMock(return_value={"api_base_url": "https://tg.custom.invalid/"})
    )
    channel = telegram.TelegramChannel(token="synthetic")
    assert await channel._api("getMe") == "https://tg.custom.invalid/botsynthetic/getMe"
    monkeypatch.setattr(
        telegram, "load_platform_config", AsyncMock(side_effect=PlatformConfigurationError("platform_inactive"))
    )
    assert (await channel.send_part("1", "hello"))["outcome"] == "blocked"


async def test_capability_endpoint_guard_rechecks_shared_json_before_io(monkeypatch):
    from app.channels import telegram

    channel = telegram.TelegramChannel(token="synthetic", expected_api_base="https://api.telegram.org")
    monkeypatch.setattr(
        telegram, "load_platform_config", AsyncMock(return_value={"api_base_url": "https://changed.invalid"})
    )

    def forbidden_http(**kwargs):
        raise AssertionError("Configuration change must block before HTTP")

    monkeypatch.setattr(telegram.httpx, "AsyncClient", forbidden_http)
    assert (await channel.send_part("1", "hello"))["outcome"] == "blocked"


async def test_oauth_uses_shared_authorize_token_and_callback_urls(monkeypatch):
    from app.core.config import settings
    from app.services.social import vk_oauth

    config = initial_platform_params(
        "vk",
        {
            "oauth_base_url": "https://auth.shared.invalid",
            "oauth_token_url": "https://auth.shared.invalid/token",
            "redirect_uri": "https://callback.shared.invalid/social",
        },
    )
    monkeypatch.setattr(vk_oauth, "load_platform_config", AsyncMock(return_value=config))
    monkeypatch.setattr(settings, "VK_APP_ID", "synthetic")
    pending = AsyncMock()
    monkeypatch.setattr(vk_oauth, "_store_pending", pending)
    url = await vk_oauth.build_authorize_url(1, user_id=1)
    assert url.startswith("https://auth.shared.invalid/authorize?")
    assert "https://callback.shared.invalid/social" in url
    assert "ignored-env" not in url
    with pytest.raises(ValueError, match="callback must match"):
        await vk_oauth.build_authorize_url(1, user_id=1, redirect_uri="https://override.invalid")
    pending.assert_awaited_once()
    response = NS(raise_for_status=lambda: None, json=lambda: {"ok": True})
    post = AsyncMock(return_value=response)

    class Client:
        async def __aenter__(self):
            return NS(post=post)

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(vk_oauth.httpx, "AsyncClient", lambda **kwargs: Client())
    assert await vk_oauth._post_form({"grant_type": "synthetic"}) == {"ok": True}
    assert post.await_args.args[0] == "https://auth.shared.invalid/token"
