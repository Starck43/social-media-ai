"""VK status is honest before/after a synthetic wrong-key refresh attempt."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet

from app.core.config import settings
from app.models.managers.user_credential_manager import user_credentials
from app.services.social import vk_connections, vk_oauth
from app.services.social.connections import connection_status
from app.utils.crypto import encrypt_secret
from tests.collection.test_web_sources import _client, _drop, _login, _register


async def test_status_and_settings_page_with_wrong_key(monkeypatch):
    monkeypatch.setattr(settings, "VK_APP_ID", "synthetic-app")
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    client = await _client()
    user = tenant_id = None
    try:
        user, tenant_id = await _register(client, "t14status")
        await _login(client, user.username)
        row = await user_credentials.store(user_id=user.id, platform="vk", kind="user_token", secret="FAKE_ACCESS")
        await user_credentials.update_by_id(
            row.id,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            refresh_secret_encrypted=encrypt_secret("FAKE_REFRESH"),
            meta={"refresh_available": True},
        )
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
        mock_post = AsyncMock()
        monkeypatch.setattr(vk_oauth, "_post_form", mock_post)
        before = await connection_status("vk", user.id)
        assert before.state == "renewal_unverified" and not before.needs_action
        page = await client.get("/app/settings?tab=connections")
        assert page.status_code == 200 and "Продление не проверено" in page.text
        assert "FAKE_ACCESS" not in page.text and "FAKE_REFRESH" not in page.text
        assert await vk_oauth.refresh_user_token(user.id) is None
        mock_post.assert_not_awaited()
        after = await connection_status("vk", user.id)
        assert after.state == "reauth" and after.needs_action and not after.can_connect
        actual_row = await user_credentials.newest(user_id=user.id, platform="vk", kind="user_token")
        assert vk_connections._vault_state(actual_row) == ("credential_unavailable", False)
        page = await client.get("/app/settings?tab=connections")
        assert page.status_code == 200 and "Доступ к секрету недоступен" in page.text
        assert "Обратитесь к администратору" in page.text
        assert "FAKE_ACCESS" not in page.text and "FAKE_REFRESH" not in page.text
    finally:
        await client.aclose()
        await _drop(user, tenant_id)
