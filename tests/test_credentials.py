"""Credential vault tests: resolution order, expiry, and no secret leakage.

The vault (`tenant_credentials`) is the primary source, env variables are the
legacy fallback, and a secret must never reach the logs. Everything runs against
the real database, like the tenancy and agent suites.
"""

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.social.credentials import CredentialMissing, credential_status, resolve_token


@pytest.fixture
def credentials_key(monkeypatch):
    """A throwaway Fernet key so tests never depend on the deployment's."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
    return key


@pytest.fixture
def no_env_tokens(monkeypatch):
    """Isolate from whatever the developer's .env has configured."""
    for attribute in ("VK_USER_ACCESS_TOKEN", "VK_SERVICE_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN", "MAX_BOT_TOKEN"):
        monkeypatch.setattr(settings, attribute, None)


@pytest.fixture
async def bootstrap_tenant_id():
    from app.models import Tenant

    tenant = await Tenant.objects.filter(slug=settings.DEFAULT_TENANT_SLUG).first()
    assert tenant is not None, "bootstrap workspace is missing — run `alembic upgrade head`"
    return tenant.id


@pytest.fixture
async def clean_vault(bootstrap_tenant_id):
    """Remove the workspace's credentials before and after each test."""
    from app.models.managers.tenant_manager import tenant_credentials

    async def _wipe():
        for row in await tenant_credentials.filter(tenant_id=bootstrap_tenant_id):
            await tenant_credentials.delete_by_id(row.id)

    await _wipe()
    yield
    await _wipe()


async def _store(tenant_id: int, platform: str, kind: str, secret: str, *, expires_at=None):
    """Store a credential through the manager, so encryption is exercised."""
    from app.models.managers.tenant_manager import tenant_credentials

    row = await tenant_credentials.store(tenant_id=tenant_id, platform=platform, kind=kind, secret=secret)
    if expires_at is not None:
        row = await tenant_credentials.update_by_id(row.id, expires_at=expires_at)
    return row


async def test_vault_wins_over_env(credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, monkeypatch):
    """A configured vault row always beats the legacy env variable."""
    monkeypatch.setattr(settings, "VK_SERVICE_ACCESS_TOKEN", "env-token")
    await _store(bootstrap_tenant_id, "vk", "service_token", "vault-token")

    assert await resolve_token("vk", tenant_id=bootstrap_tenant_id) == "vault-token"


async def test_user_token_is_preferred_over_service_token(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id
):
    """A VK user token sees more than a community token, so it wins."""
    await _store(bootstrap_tenant_id, "vk", "service_token", "service-token")
    await _store(bootstrap_tenant_id, "vk", "user_token", "user-token")

    assert await resolve_token("vk", tenant_id=bootstrap_tenant_id) == "user-token"


async def test_env_used_when_vault_is_empty(credentials_key, clean_vault, bootstrap_tenant_id, monkeypatch):
    """Legacy installs keep working with env-only credentials."""
    monkeypatch.setattr(settings, "VK_USER_ACCESS_TOKEN", None)
    monkeypatch.setattr(settings, "VK_SERVICE_ACCESS_TOKEN", "env-service")

    assert await resolve_token("vk", tenant_id=bootstrap_tenant_id) == "env-service"


async def test_service_token_used_when_only_kind_present(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id
):
    """Without a user token the service token is still usable."""
    await _store(bootstrap_tenant_id, "vk", "service_token", "service-token")

    assert await resolve_token("vk", tenant_id=bootstrap_tenant_id) == "service-token"


async def test_expired_credential_is_skipped(credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id):
    """An expired row must not be handed to the platform client."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await _store(bootstrap_tenant_id, "vk", "user_token", "stale-token", expires_at=past)

    assert await resolve_token("vk", tenant_id=bootstrap_tenant_id) is None


async def test_missing_credential_raises_when_required(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id
):
    with pytest.raises(CredentialMissing):
        await resolve_token("vk", tenant_id=bootstrap_tenant_id, required=True)


async def test_secret_never_logs_plaintext(credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, caplog):
    """Resolution is logged (platform/kind), the value never is."""
    await _store(bootstrap_tenant_id, "vk", "user_token", "super-secret-token")

    with caplog.at_level("DEBUG"):
        assert await resolve_token("vk", tenant_id=bootstrap_tenant_id) == "super-secret-token"

    assert "super-secret-token" not in caplog.text


async def test_credential_status_reports_source(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, monkeypatch
):
    """Diagnostics report where a secret comes from, without revealing it."""
    await _store(bootstrap_tenant_id, "vk", "user_token", "vault-vk")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "env-telegram")

    status = await credential_status(tenant_id=bootstrap_tenant_id)

    assert status["vk"] == "vault"
    assert status["telegram"] == "env"
    assert status["max"] == "missing"
