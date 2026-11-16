"""VK OAuth address policy, without network or credential values."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import settings
from app.services.social import vk_oauth
from app.services.social.platform_config import initial_platform_params

__source_only__ = True


@pytest.mark.parametrize(
    "value",
    [
        "http://id.vk.ru/oauth2/auth",
        "https://id.vk.ru.evil.invalid/token",
        "https://evil.invalid@id.vk.ru/token",
        "https://id.vk.ru:8443/token",
        "https://id.vk.ru/token?extra=value",
        "https://id.vk.ru/token#fragment",
        "https://id.vk.ru/token\n",
        "https://id.vk.ru\\@evil.invalid/token",
        "file:///tmp/token",
    ],
)
async def test_untrusted_token_endpoint_never_constructs_http_client(monkeypatch, value):
    config = initial_platform_params("vk", {"oauth_token_url": value})
    monkeypatch.setattr(vk_oauth, "load_platform_config", AsyncMock(return_value=config))
    monkeypatch.setattr(settings, "VK_OAUTH_ALLOWED_HOSTS", "id.vk.ru")
    client = Mock(side_effect=AssertionError("no HTTP client may be created"))
    monkeypatch.setattr(vk_oauth.httpx, "AsyncClient", client)
    with pytest.raises(vk_oauth.OAuthPreflightError) as exc:
        await vk_oauth._post_form({"refresh_token": "FAKE_SECRET"})
    assert value not in str(exc.value) and "FAKE_SECRET" not in str(exc.value)
    client.assert_not_called()


async def test_custom_endpoint_requires_explicit_host_trust_and_never_follows_redirects(monkeypatch):
    config = initial_platform_params("vk", {"oauth_token_url": "https://trusted.proxy.invalid/token"})
    monkeypatch.setattr(vk_oauth, "load_platform_config", AsyncMock(return_value=config))
    monkeypatch.setattr(settings, "VK_OAUTH_ALLOWED_HOSTS", "id.vk.ru")
    factory = Mock()
    monkeypatch.setattr(vk_oauth.httpx, "AsyncClient", factory)
    with pytest.raises(vk_oauth.OAuthPreflightError):
        await vk_oauth._post_form({"grant_type": "synthetic"})
    factory.assert_not_called()
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})
    post = AsyncMock(return_value=response)

    class Client:
        async def __aenter__(self):
            return SimpleNamespace(post=post)

        async def __aexit__(self, *args):
            pass

    factory.side_effect = lambda **kwargs: Client()
    monkeypatch.setattr(settings, "VK_OAUTH_ALLOWED_HOSTS", "id.vk.ru, trusted.proxy.invalid")
    assert await vk_oauth._post_form({"grant_type": "synthetic"}) == {"ok": True}
    assert factory.call_args.kwargs["follow_redirects"] is False
    assert post.await_args.args[0] == "https://trusted.proxy.invalid/token"


@pytest.mark.parametrize(
    "environment,url,allowed",
    [
        ("development", "http://localhost/api/v1/social/callback", True),
        ("development", "http://127.0.0.1:8000/callback", True),
        ("production", "http://localhost/callback", False),
        ("development", "http://localhost.evil.invalid/callback", False),
        ("production", "https://app.example.invalid/callback", True),
        ("development", "https://user:password@app.example.invalid/callback", False),
    ],
)
def test_callback_requires_https_except_exact_local_development(monkeypatch, environment, url, allowed):
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    config = initial_platform_params("vk", {"redirect_uri": url})
    if allowed:
        assert vk_oauth._oauth_url(config, "redirect_uri", callback=True) == url
    else:
        with pytest.raises(vk_oauth.OAuthPreflightError):
            vk_oauth._oauth_url(config, "redirect_uri", callback=True)


async def test_untrusted_authorize_host_does_not_store_pending_state(monkeypatch):
    config = initial_platform_params("vk", {"oauth_base_url": "https://evil.invalid"})
    monkeypatch.setattr(vk_oauth, "load_platform_config", AsyncMock(return_value=config))
    monkeypatch.setattr(settings, "VK_OAUTH_ALLOWED_HOSTS", "id.vk.ru")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    pending = AsyncMock()
    monkeypatch.setattr(vk_oauth, "_store_pending", pending)
    with pytest.raises(vk_oauth.OAuthPreflightError):
        await vk_oauth.build_authorize_url(1, user_id=2, app_id="fake")
    pending.assert_not_awaited()


async def test_invalid_catalog_is_classified_before_http(monkeypatch):
    monkeypatch.setattr(vk_oauth, "load_platform_config", AsyncMock(side_effect=ValueError("FAKE_SECRET")))
    client = Mock()
    monkeypatch.setattr(vk_oauth.httpx, "AsyncClient", client)
    with pytest.raises(vk_oauth.OAuthPreflightError) as exc:
        await vk_oauth._post_form({"refresh_token": "FAKE_SECRET"})
    assert "FAKE_SECRET" not in str(exc.value)
    client.assert_not_called()


async def test_redirect_response_never_forwards_oauth_payload(monkeypatch):
    import httpx

    config = initial_platform_params("vk")
    monkeypatch.setattr(vk_oauth, "load_platform_config", AsyncMock(return_value=config))
    monkeypatch.setattr(settings, "VK_OAUTH_ALLOWED_HOSTS", "id.vk.ru")
    requests = []

    def handler(request):
        requests.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://evil.invalid/receive"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        vk_oauth.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await vk_oauth._post_form({"refresh_token": "FAKE_SECRET"})
    assert requests == ["id.vk.ru"]
