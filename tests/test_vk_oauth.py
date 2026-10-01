"""VK OAuth 2.0 + PKCE tests: signed state, token exchange, auto-refresh.

Covers the pieces that do not need real VK: PKCE generation, the signed-state
round trip, the authorize URL, and the personal-vault write path with the HTTP
layer stubbed (`_post_form`). The auto-refresh path is exercised through
`resolve_token` so an expired L2 token transparently comes back refreshed.

The L2 token now lives in the *personal* vault (`user_credentials`, keyed by
`users.id`), so each test creates a web user + workspace membership to host it.
"""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.models import Role, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.services.social import vk_oauth
from app.services.social.vk_oauth import _verify, build_authorize_url, generate_pkce
from app.types.enums.user_types import UserRoleType


@pytest.fixture
def credentials_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
    return key


@pytest.fixture
def no_env_tokens(monkeypatch):
    for attribute in (
        "VK_SERVICE_KEY",
        "VK_APP_ID",
        "VK_CLIENT_ACCESS_KEY",
    ):
        monkeypatch.setattr(settings, attribute, None)


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


@pytest.fixture
async def bootstrap_tenant_id():
    tenant = await tenants.filter(slug=settings.DEFAULT_TENANT_SLUG).first()
    assert tenant is not None, "bootstrap workspace is missing — run `alembic upgrade head`"
    return tenant.id


@pytest.fixture
async def owner_user(bootstrap_tenant_id):
    """A web user who is an active member of the bootstrap workspace."""
    username = _uniq("vkuser")
    role = await Role.objects.get(codename=UserRoleType.VIEWER.name)
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="secret-password-1",
        role_id=role.id,
    )
    await TenantUserManager().add_web_member(tenant_id=bootstrap_tenant_id, user_id=user.id, role="owner")
    return user


@pytest.fixture
async def clean_vault(owner_user):
    from app.models.managers.user_credential_manager import user_credentials

    async def _wipe():
        for row in await user_credentials.filter(user_id=owner_user.id):
            await user_credentials.delete_by_id(row.id)

    await _wipe()
    yield
    await _wipe()


async def _store_user(user_id, platform, kind, secret, *, expires_at=None, meta=None):
    from app.models.managers.user_credential_manager import user_credentials

    row = await user_credentials.store(user_id=user_id, platform=platform, kind=kind, secret=secret)
    values = {}
    if expires_at is not None:
        values["expires_at"] = expires_at
    if meta is not None:
        values["meta"] = meta
    if values:
        row = await user_credentials.update_by_id(row.id, **values)
    return row


async def _user_token_row(user_id):
    from app.models.managers.user_credential_manager import user_credentials

    rows = await user_credentials.filter(user_id=user_id, platform="vk", kind="user_token")
    return rows[0] if rows else None


def test_generate_pkce_is_s256():
    verifier, challenge = generate_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_signed_state_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret")
    from app.services.social.vk_oauth import _sign

    state = _sign(42, 7, "abc")
    assert _verify(state) == (42, 7, "abc")


def test_signed_state_roundtrip_no_user(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret")
    from app.services.social.vk_oauth import _sign

    state = _sign(42, None, "abc")
    assert _verify(state) == (42, None, "abc")


def test_signed_state_rejects_tamper(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret")
    from app.services.social.vk_oauth import _sign

    state = _sign(42, 7, "abc")
    tenant, owner, verifier, _ = state.split(".", 3)
    tampered = f"{tenant}.{owner}.{verifier}.{'0' * 64}"
    assert _verify(tampered) is None


def test_authorize_url_requires_app_id(credentials_key, no_env_tokens, bootstrap_tenant_id, owner_user):
    with pytest.raises(RuntimeError):
        build_authorize_url(bootstrap_tenant_id, user_id=owner_user.id)


def test_authorize_url_embeds_state(credentials_key, no_env_tokens, bootstrap_tenant_id, owner_user, monkeypatch):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    url = build_authorize_url(bootstrap_tenant_id, user_id=owner_user.id)
    assert url.startswith("https://oauth.vk.com/authorize?")
    assert "client_id=123456" in url
    assert "code_challenge_method=S256" in url
    state = url.split("state=")[1].split("&")[0]
    tenant_id, user_id, _ = _verify(state)
    assert tenant_id == bootstrap_tenant_id
    assert user_id == owner_user.id


async def test_complete_authorization_stores_user_token(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user, monkeypatch
):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    monkeypatch.setattr(settings, "VK_CLIENT_ACCESS_KEY", "secret")

    async def fake_post(payload):
        assert payload["grant_type"] == "authorization_code"
        assert payload["client_id"] == "123456"
        assert payload["client_secret"] == "secret"
        assert "code_verifier" in payload
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 86400,
            "user_id": 999,
            "scope": "offline,wall",
        }

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    state = vk_oauth._sign(bootstrap_tenant_id, owner_user.id, "abc")
    data = await vk_oauth.complete_authorization("the-code", state)
    assert data["access_token"] == "new-access"

    row = await _user_token_row(owner_user.id)
    assert row is not None
    assert row.reveal() == "new-access"
    assert (row.meta or {}).get("refresh_token") == "new-refresh"
    assert row.expires_at is not None


async def test_complete_authorization_rejects_bad_state(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user
):
    with pytest.raises(ValueError):
        await vk_oauth.complete_authorization("code", "1.0.abc.0000000000000000")


async def test_refresh_user_token_updates_vault(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user, monkeypatch
):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    monkeypatch.setattr(settings, "VK_CLIENT_ACCESS_KEY", "secret")
    await _store_user(
        owner_user.id,
        "vk",
        "user_token",
        "stale-access",
        meta={"refresh_token": "old-refresh"},
    )

    async def fake_post(payload):
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "old-refresh"
        return {"access_token": "fresh-access", "refresh_token": "rotated-refresh", "expires_in": 3600}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    result = await vk_oauth.refresh_user_token(owner_user.id)
    assert result == "fresh-access"

    row = await _user_token_row(owner_user.id)
    assert row.reveal() == "fresh-access"
    assert (row.meta or {}).get("refresh_token") == "rotated-refresh"


async def test_resolve_token_auto_refreshes_expired_user_token(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user, monkeypatch
):
    from app.services.social.credentials import resolve_token

    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    monkeypatch.setattr(settings, "VK_CLIENT_ACCESS_KEY", "secret")
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await _store_user(
        owner_user.id,
        "vk",
        "user_token",
        "expired-access",
        expires_at=past,
        meta={"refresh_token": "old-refresh"},
    )

    async def fake_post(payload):
        return {"access_token": "fresh-access", "refresh_token": "new-refresh", "expires_in": 3600}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bootstrap_tenant_id):
        token = await resolve_token("vk", kinds=("user_token",), owner_user_id=owner_user.id)
    assert token == "fresh-access"
