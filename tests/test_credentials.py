"""Credential resolution tests: personal vault first, env fallback, no leakage.

Personal secrets live in `user_credentials` keyed by `users.id`; application /
infrastructure secrets (VK app_id, client_secret, service_token, bot tokens)
come from the environment. A secret must never reach the logs. Everything runs
against the real database, like the tenancy and agent suites.
"""

import secrets
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
    for attribute in (
        "VK_SERVICE_KEY",
        "VK_APP_ID",
        "VK_CLIENT_ACCESS_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION",
        "MAX_BOT_TOKEN",
    ):
        monkeypatch.setattr(settings, attribute, None)


@pytest.fixture
async def owner_user():
    """A web user that hosts personal credentials (`user_credentials.user_id`)."""
    from app.models import Role, User
    from app.types import UserRoleType

    role = await Role.objects.get(codename=UserRoleType.VIEWER.name)
    username = f"creduser{secrets.token_hex(4)}"
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
    )
    yield user
    await User.objects.delete_by_id(user.id)


@pytest.fixture
async def clean_vault(owner_user):
    """Remove the user's personal credentials before and after each test."""
    from app.models.managers.user_credential_manager import user_credentials

    async def _wipe():
        for row in await user_credentials.filter(user_id=owner_user.id):
            await user_credentials.delete_by_id(row.id)

    await _wipe()
    yield
    await _wipe()


async def _store(user_id: int, platform: str, kind: str, secret: str, *, expires_at=None):
    """Store a personal credential through the manager, so encryption is exercised."""
    from app.models.managers.user_credential_manager import user_credentials

    row = await user_credentials.store(user_id=user_id, platform=platform, kind=kind, secret=secret)
    if expires_at is not None:
        row = await user_credentials.update_by_id(row.id, expires_at=expires_at)
    return row


async def test_user_token_wins_over_env(credentials_key, no_env_tokens, clean_vault, owner_user, monkeypatch):
    """A personal L2 token beats the env L1 token."""
    monkeypatch.setattr(settings, "VK_SERVICE_KEY", "env-token")
    await _store(owner_user.id, "vk", "user_token", "vault-token")

    assert await resolve_token("vk", owner_user_id=owner_user.id) == "vault-token"


async def test_env_used_when_no_personal_token(credentials_key, no_env_tokens, clean_vault, owner_user, monkeypatch):
    """Env-only installs keep working; this user has no personal token."""
    monkeypatch.setattr(settings, "VK_SERVICE_KEY", "env-service")

    assert await resolve_token("vk", owner_user_id=owner_user.id) == "env-service"


async def test_personal_token_requires_owner(credentials_key, no_env_tokens, clean_vault, owner_user):
    """Without an owner id the personal vault is not consulted at all."""
    await _store(owner_user.id, "vk", "user_token", "vault-token")

    assert await resolve_token("vk") is None


async def test_expired_token_is_skipped(credentials_key, no_env_tokens, clean_vault, owner_user):
    """An expired L2 token with no refresh path must not be handed out."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await _store(owner_user.id, "vk", "user_token", "stale-token", expires_at=past)

    assert await resolve_token("vk", owner_user_id=owner_user.id) is None


async def test_missing_credential_raises_when_required(credentials_key, no_env_tokens, clean_vault, owner_user):
    with pytest.raises(CredentialMissing):
        await resolve_token("vk", owner_user_id=owner_user.id, required=True)


async def test_secret_never_logs_plaintext(credentials_key, no_env_tokens, clean_vault, owner_user, caplog):
    """Resolution is logged (platform/kind), the value never is."""
    await _store(owner_user.id, "vk", "user_token", "super-secret-token")

    with caplog.at_level("DEBUG"):
        assert await resolve_token("vk", owner_user_id=owner_user.id) == "super-secret-token"

    assert "super-secret-token" not in caplog.text


async def test_bot_token_resolves_from_env_only(credentials_key, no_env_tokens, clean_vault, owner_user, monkeypatch):
    """Bot tokens are infrastructure config: env, regardless of the owner id."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "env-bot")

    assert await resolve_token("telegram", owner_user_id=owner_user.id) == "env-bot"
    assert await resolve_token("telegram") == "env-bot"


async def test_credential_status_reports_source(credentials_key, no_env_tokens, clean_vault, owner_user, monkeypatch):
    """Diagnostics report where a secret comes from, without revealing it."""
    await _store(owner_user.id, "vk", "user_token", "vault-vk")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "env-telegram")

    status = await credential_status(owner_user_id=owner_user.id)

    assert status["vk"] == "user-vault"
    assert status["telegram"] == "env"
    assert status["max"] == "missing"
