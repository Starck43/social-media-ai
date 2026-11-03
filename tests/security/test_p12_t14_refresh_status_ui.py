"""VK status is honest before/after a synthetic wrong-key refresh attempt."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models.managers.user_credential_manager import user_credentials
from app.services.notifications.inbox import PersonalInbox
from app.services.social import source_access, vk_connections, vk_oauth
from app.services.social.connections import connection_status
from app.utils.crypto import encrypt_secret
from tests.collection.test_web_sources import _client, _csrf, _drop, _login, _register


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
        source = SimpleNamespace(
            id=1,
            tenant_id=tenant_id,
            owner_user_id=user.id,
            params={"mode": "pull"},
            platform=SimpleNamespace(platform_type="vk"),
        )
        with tenant_scope(tenant_id), permission_scope(user):
            reports = await source_access.preflight([source])
            assert len(reports) == 1 and reports[0].operator_source_ids == (1,)
            assert "администратору" in reports[0].summary() and "авторизуйтесь" not in reports[0].summary()
            notices = await source_access.authorisation_reports([source], required_source_ids={1})
            assert len(notices) == 1 and notices[0].operator_count == 1
            assert await source_access.notify_authorisation_required(notices) == 1
            entries = await PersonalInbox(user).entries()
            event = next(e for e in entries if e.event.dedup_key == notices[0].dedup_key)
            assert event.destination == "/app/settings?tab=connections"
            assert "администратору" in event.event.message
            assert "FAKE_REFRESH" not in event.event.message
            event_id = event.event.id
        url = f"/app/notifications/open/{event_id}"
        token = await _csrf(client, url)
        opened = await client.post(url, data={"_csrf": token}, follow_redirects=False)
        assert opened.status_code == 303
        assert opened.headers["location"] == "/app/settings?tab=connections"
    finally:
        await client.aclose()
        await _drop(user, tenant_id)
