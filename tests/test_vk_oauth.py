"""VK ID (OAuth 2.1) tests: PKCE, pending state, token exchange, auto-refresh.

Covers the pieces that do not need real VK: state/verifier generation, the
S256 code challenge, the authorize URL + pending-row round trip, and the
personal-vault write path with the HTTP layer stubbed (`_post_form`). The
auto-refresh path is exercised through `resolve_token` so an expired L2 token
transparently comes back refreshed.

The L2 token and the ephemeral pending rows both live in `user_credentials`,
so each test creates a web user + workspace membership to host them.
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
from app.services.social.vk_oauth import (
    build_authorize_url,
    code_challenge,
    generate_code_verifier,
    generate_state,
)
from app.types.enums.user_types import UserRoleType

_PENDING_KIND = "oauth_pending"


@pytest.fixture
def credentials_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
    return key


@pytest.fixture
def no_env_tokens(monkeypatch):
    for attribute in ("VK_SERVICE_KEY", "VK_APP_ID", "VK_CLIENT_ACCESS_KEY"):
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
        email=f"{username}@example.com",
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


async def _pending_row(state: str):
    from app.models.managers.user_credential_manager import user_credentials

    rows = await user_credentials.filter(platform="vk", kind=_PENDING_KIND, label=state)
    return rows[0] if rows else None


def test_generate_state():
    state = generate_state()
    assert len(state) >= 32
    assert all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in state)
    assert "=" not in state


def test_generate_code_verifier():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in verifier)


def test_code_challenge_s256():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert code_challenge(verifier) == expected
    assert code_challenge(verifier) != code_challenge("other-verifier")


async def test_authorize_url_requires_app_id(credentials_key, no_env_tokens, bootstrap_tenant_id, owner_user):
    with pytest.raises(RuntimeError, match="app_id"):
        await build_authorize_url(bootstrap_tenant_id, user_id=owner_user.id)


async def test_authorize_url_requires_user_id(credentials_key, no_env_tokens, bootstrap_tenant_id, monkeypatch):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    with pytest.raises(RuntimeError, match="user_id"):
        await build_authorize_url(bootstrap_tenant_id, user_id=None)


async def test_authorize_url_embeds_state(credentials_key, no_env_tokens, bootstrap_tenant_id, owner_user, monkeypatch):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    url = await build_authorize_url(bootstrap_tenant_id, user_id=owner_user.id)
    assert url.startswith("https://id.vk.ru/authorize?")
    assert "client_id=123456" in url
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "redirect_uri=" in url
    state = url.split("state=")[1].split("&")[0]
    assert state


async def test_complete_authorization_stores_user_token(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user, monkeypatch
):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    monkeypatch.setattr(settings, "VK_SERVICE_KEY", "srv-token")

    state, verifier = None, None
    original = vk_oauth._store_pending

    async def spy_store_pending(**kwargs):
        nonlocal state, verifier
        state = kwargs["state"]
        verifier = kwargs["verifier"]
        await original(**kwargs)

    monkeypatch.setattr(vk_oauth, "_store_pending", spy_store_pending)
    await build_authorize_url(bootstrap_tenant_id, user_id=owner_user.id)

    async def fake_post(payload):
        assert payload["grant_type"] == "authorization_code"
        assert payload["client_id"] == "123456"
        assert payload["code_verifier"] == verifier
        assert payload["service_token"] == "srv-token"
        assert payload["state"] == state
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "user_id": 999,
            "scope": "vkid.personal_info",
        }

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    data = await vk_oauth.complete_authorization("the-code", state, device_id="dev-1")
    assert data["access_token"] == "new-access"

    row = await _user_token_row(owner_user.id)
    assert row is not None
    assert row.reveal() == "new-access"
    assert (row.meta or {}).get("refresh_token") == "new-refresh"
    assert (row.meta or {}).get("device_id") == "dev-1"
    assert row.expires_at is not None


async def test_complete_authorization_rejects_bad_state(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user
):
    with pytest.raises(ValueError):
        await vk_oauth.complete_authorization("code", "some-unknown-state")


async def test_complete_authorization_consumes_state_once(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user, monkeypatch
):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")

    async def fake_post(payload):
        return {"access_token": "t", "refresh_token": "r", "expires_in": 3600, "user_id": 1}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    state = None
    original = vk_oauth._store_pending

    async def spy_store_pending(**kwargs):
        nonlocal state
        state = kwargs["state"]
        await original(**kwargs)

    monkeypatch.setattr(vk_oauth, "_store_pending", spy_store_pending)
    await build_authorize_url(bootstrap_tenant_id, user_id=owner_user.id)

    await vk_oauth.complete_authorization("code-1", state)
    with pytest.raises(ValueError):
        await vk_oauth.complete_authorization("code-2", state)


async def test_refresh_user_token_updates_vault(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user, monkeypatch
):
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
    monkeypatch.setattr(settings, "VK_SERVICE_KEY", "srv-token")
    await _store_user(
        owner_user.id,
        "vk",
        "user_token",
        "stale-access",
        meta={"refresh_token": "old-refresh", "device_id": "dev-1"},
    )

    async def fake_post(payload):
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "old-refresh"
        assert payload["device_id"] == "dev-1"
        assert payload["service_token"] == "srv-token"
        return {"access_token": "fresh-access", "refresh_token": "rotated-refresh", "expires_in": 3600}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    result = await vk_oauth.refresh_user_token(owner_user.id)
    assert result == "fresh-access"

    row = await _user_token_row(owner_user.id)
    assert row.reveal() == "fresh-access"
    assert (row.meta or {}).get("refresh_token") == "rotated-refresh"
    assert (row.meta or {}).get("device_id") == "dev-1"


async def test_resolve_token_auto_refreshes_expired_user_token(
    credentials_key, no_env_tokens, clean_vault, bootstrap_tenant_id, owner_user, monkeypatch
):
    from app.services.social.credentials import resolve_token

    monkeypatch.setattr(settings, "VK_APP_ID", "123456")
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
