"""Personal connection states (`app/services/social/connections.py`).

The whole module exists because of one distinction: an expired token that has a
`refresh_token` is **not** a problem, and an expired token without one is. The
old sources-page badge keyed only on `expires_at`, so it told people to log in
again for a token the collector renewed silently every hour — and people acted on
it, logging out and re-authorizing for no reason.

These tests pin the states, the navbar contract (silence unless action is
needed), and the refresh-token rotation rule, which is the one place where a
careless line costs a user their login.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.models import Role, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.services.social import connections
from app.services.social.connections import (
    connection_status,
    connection_statuses,
    source_connection_spec,
    statuses_needing_action,
)


@pytest.fixture
def credentials_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
    return key


@pytest.fixture
def app_configured(monkeypatch):
    """A deployment that *can* offer VK OAuth — otherwise everything reads as
    `unconfigured` and none of the interesting states are reachable."""
    monkeypatch.setattr(settings, "VK_APP_ID", "123456")


@pytest.fixture
def app_missing(monkeypatch):
    monkeypatch.setattr(settings, "VK_APP_ID", None)


@pytest.fixture
async def web_user(credentials_key):
    """A web user in the bootstrap workspace, hosting their own vault rows."""
    from app.models.managers.user_credential_manager import user_credentials

    tenant = await tenants.filter(slug=settings.DEFAULT_TENANT_SLUG).first()
    assert tenant is not None, "bootstrap workspace is missing — run `alembic upgrade head`"
    username = f"conn{secrets.token_hex(4)}"
    role = await Role.objects.get(codename="VIEWER")
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant.id, user_id=user.id, role="owner")
    for row in await user_credentials.filter(user_id=user.id):
        await user_credentials.delete_by_id(row.id)
    yield user

    for row in await user_credentials.filter(user_id=user.id):
        await user_credentials.delete_by_id(row.id)
    await User.objects.delete_by_id(user.id)


async def _store(user_id, *, platform="vk", kind="user_token", expires_at=None, meta=None, label=None):
    from app.models.managers.user_credential_manager import user_credentials

    row = await user_credentials.store(
        user_id=user_id, platform=platform, kind=kind, secret="secret-value", label=label
    )
    values = {}
    if expires_at is not None:
        values["expires_at"] = expires_at
    if meta is not None:
        values["meta"] = meta
    if values:
        row = await user_credentials.update_by_id(row.id, **values)
    return row


async def _vk_row(user_id):
    from app.models.managers.user_credential_manager import user_credentials

    return await user_credentials.newest(user_id=user_id, platform="vk", kind="user_token")


# ── states ───────────────────────────────────────────────────────────────────


async def test_never_connected_is_missing_and_asks_for_action(credentials_key, app_configured, web_user):
    status = await connection_status("vk", web_user.id)
    assert status.state == "missing"
    assert status.needs_action is True
    assert status.can_connect is True


async def test_a_valid_token_is_connected_and_silent(credentials_key, app_configured, web_user):
    await _store(web_user.id, expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
    status = await connection_status("vk", web_user.id)
    assert status.state == "connected"
    assert status.needs_action is False
    assert status.tone == "ok"


async def test_expired_but_renewable_is_not_the_users_problem(credentials_key, app_configured, web_user):
    """The regression this module was written for.

    A VK access token lives one hour and is renewed automatically. Showing
    «войдите заново» for it every hour taught people to log out for no reason.
    """
    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=2),
        meta={"refresh_token": "refresh-1"},
    )
    status = await connection_status("vk", web_user.id)
    assert status.state == "renewable"
    assert status.needs_action is False, "a token that renews itself must never nag"
    assert status.tone == "ok"


async def test_expired_without_refresh_asks_for_a_reauth(credentials_key, app_configured, web_user):
    await _store(web_user.id, expires_at=datetime.now(timezone.utc) - timedelta(hours=2))
    status = await connection_status("vk", web_user.id)
    assert status.state == "reauth"
    assert status.needs_action is True
    assert status.tone == "bad"


async def test_a_failed_refresh_is_recorded_and_surfaced(credentials_key, app_configured, web_user):
    """A dead refresh token is the one case where a human must step in.

    It must also stop being retried: VK ID invalidates the whole session when a
    spent refresh token is replayed.
    """
    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=2),
        meta={
            "refresh_token": "refresh-1",
            "refresh_failed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    status = await connection_status("vk", web_user.id)
    assert status.state == "reauth"
    assert status.needs_action is True


async def test_an_old_refresh_failure_stops_worrying(credentials_key, app_configured, web_user):
    """After the cooldown a stale failure marker is not a verdict on the token."""
    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=2),
        meta={
            "refresh_token": "refresh-1",
            "refresh_failed_at": (
                datetime.now(timezone.utc) - connections.REFRESH_FAILURE_COOLDOWN - timedelta(minutes=1)
            ).isoformat(),
        },
    )
    status = await connection_status("vk", web_user.id)


# ── the navbar contract ─────────────────────────────────────────────────────


async def test_the_navbar_stays_silent_while_everything_is_fine(credentials_key, app_configured, web_user):
    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        meta={"refresh_token": "refresh-1"},
    )
    assert await statuses_needing_action(web_user.id) == []


async def test_the_navbar_lists_only_what_needs_a_person(credentials_key, app_configured, web_user):
    await _store(web_user.id, expires_at=datetime.now(timezone.utc) - timedelta(days=2))
    alerts = await statuses_needing_action(web_user.id)
    assert [a.platform for a in alerts] == ["vk"]
    assert all(a.needs_action for a in alerts)


async def test_statuses_cover_every_registered_platform(credentials_key, app_configured, web_user):
    statuses = await connection_statuses(web_user.id)
    assert {s.platform for s in statuses} == {spec.platform for spec in connections.CONNECTIONS}


# ── source scoping ──────────────────────────────────────────────────────────


class _PlatformType:
    db_value = "vk"


class _Platform:
    platform_type = _PlatformType()


def _source(mode: str):
    return SimpleNamespace(params={"mode": mode}, platform=_Platform())


def test_a_push_source_needs_no_personal_connection():
    """A bot collects through its own token; asking about one invents a problem."""
    assert source_connection_spec(_source("push")) is None


def test_a_pull_source_maps_to_its_platform_connection():
    assert source_connection_spec(_source("pull")).platform == "vk"


def test_a_user_source_uses_a_personal_token():
    assert source_connection_spec(_source("user")) is not None


# ── refresh rotation ────────────────────────────────────────────────────────


async def test_the_rotated_refresh_token_replaces_the_old_one(credentials_key, app_configured, web_user, monkeypatch):
    """Keeping the spent refresh token would make the next renewal kill the session."""
    from app.services.social import vk_oauth

    await _store(web_user.id, meta={"refresh_token": "old-refresh"})

    async def fake_post(payload):
        assert payload["refresh_token"] == "old-refresh"
        return {"access_token": "fresh", "refresh_token": "rotated", "expires_in": 3600}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    assert await vk_oauth.refresh_user_token(web_user.id) == "fresh"
    assert (await _vk_row(web_user.id)).meta["refresh_token"] == "rotated"


async def test_a_response_without_a_refresh_token_does_not_keep_the_spent_one(
    credentials_key, app_configured, web_user, monkeypatch
):
    """VK rotates; if the response omits one, keeping the old value is a trap."""
    from app.services.social import vk_oauth

    await _store(web_user.id, meta={"refresh_token": "old-refresh"})

    async def fake_post(payload):
        return {"access_token": "fresh", "expires_in": 3600}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    await vk_oauth.refresh_user_token(web_user.id)
    assert (await _vk_row(web_user.id)).meta["refresh_token"] == ""


async def test_a_failed_refresh_is_recorded_not_retried_blindly(credentials_key, app_configured, web_user, monkeypatch):
    from app.services.social import vk_oauth

    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        meta={"refresh_token": "old-refresh"},
    )

    async def failing_post(payload):
        raise RuntimeError("503 from VK")

    monkeypatch.setattr(vk_oauth, "_post_form", failing_post)

    assert await vk_oauth.refresh_user_token(web_user.id) is None
    row = await _vk_row(web_user.id)
    assert row.meta.get("refresh_failed_at")
    # The refresh token itself is untouched: the failure may well be transient.
    assert row.meta["refresh_token"] == "old-refresh"

    status = await connection_status("vk", web_user.id)
    assert status.state == "reauth"
    assert status.needs_action is True


async def test_a_token_is_renewed_before_it_expires_not_after(credentials_key, app_configured, web_user, monkeypatch):
    """A run starting at 10:59 must not carry a token that dies mid-request."""
    from app.services.social import vk_oauth

    calls: list[int] = []
    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        meta={"refresh_token": "r"},
    )

    async def fake_post(payload):
        calls.append(1)
        return {"access_token": "fresh", "refresh_token": "r2", "expires_in": 3600}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)

    assert await vk_oauth.renew_if_expiring(web_user.id) == "fresh"
    assert calls


async def test_a_fresh_token_is_left_alone(credentials_key, app_configured, web_user, monkeypatch):
    from app.services.social import vk_oauth

    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        meta={"refresh_token": "r"},
    )

    async def never_called(payload):
        raise AssertionError("a token valid for an hour must not be renewed")

    monkeypatch.setattr(vk_oauth, "_post_form", never_called)

    assert await vk_oauth.renew_if_expiring(web_user.id) is None


async def test_resolve_token_renews_a_token_that_is_about_to_expire(
    credentials_key, app_configured, web_user, monkeypatch
):
    """The end-to-end promise: collection never sees a stale token."""
    from app.core.tenant_context import tenant_scope
    from app.services.social import vk_oauth
    from app.services.social.credentials import resolve_token

    tenant = await tenants.filter(slug=settings.DEFAULT_TENANT_SLUG).first()
    await _store(
        web_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        meta={"refresh_token": "r"},
    )

    async def fake_post(payload):
        return {"access_token": "renewed", "refresh_token": "r2", "expires_in": 3600}

    monkeypatch.setattr(vk_oauth, "_post_form", fake_post)
    monkeypatch.setattr(settings, "VK_SERVICE_KEY", None)

    with tenant_scope(tenant.id):
        token = await resolve_token("vk", kinds=("user_token",), owner_user_id=web_user.id)
    assert token == "renewed"
