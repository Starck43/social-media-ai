"""Authenticated ASGI/DOM check: VK setup errors must not expose secrets."""

from unittest.mock import AsyncMock

import pytest

from app.models.managers.tenant_manager import tenants
from app.services.social import vk_oauth
from app.services.social.platform_config import PlatformConfigurationError
from tests.web.test_web_ui import _client, _delete_user, _register


@pytest.mark.parametrize(
    "error", [RuntimeError("FAKE_PRIVATE_RUNTIME_VALUE"), PlatformConfigurationError("FAKE_PRIVATE_CONFIG_VALUE")]
)
async def test_authorize_failure_is_safe_and_points_to_current_config(monkeypatch, error):
    monkeypatch.setattr(vk_oauth, "build_authorize_url", AsyncMock(side_effect=error))
    async with await _client() as client:
        user, tenant_id = await _register(client, "OAuth guidance test")
        try:
            page = await client.get("/app/connections/vk/authorize")
            assert page.status_code == 200
            assert "Не удалось начать авторизацию VK" in page.text
            assert "VK_APP_ID" in page.text
            assert "общей конфигурации платформы" in page.text
            assert "VK_CLIENT_ACCESS_KEY" not in page.text
            assert "FAKE_PRIVATE_RUNTIME_VALUE" not in page.text
            assert "FAKE_PRIVATE_CONFIG_VALUE" not in page.text
        finally:
            await _delete_user(user.id)
            await tenants.delete_by_id(tenant_id)
