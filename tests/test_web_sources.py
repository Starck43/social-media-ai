"""Sources v2: the collection mode and token owner are visible and editable.

Before this, `params["mode"]` (which layer fetches — API, bot updates, or a
personal L2 session) and `params["token_owner"]` (whose personal token collects)
existed in the data model and were honoured by `app/services/social/owner.py`,
but the web UI never showed them: a user could not see why a source was silent,
let alone fix it.

These tests pin what was actually broken — the values reach `params`, a foreign
`token_owner` is refused instead of silently stored, an unknown mode is named
rather than coerced — plus the detail page that answers "why is nothing
collected from it?".
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AIAnalytics, Job, Platform, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import UserRoleType
from app.web.sources import COLLECTION_MODES

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав для этого действия"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret-password-1",
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _login(client: AsyncClient, username: str, password: str = "secret-password-1") -> None:
    token = await _csrf(client, "/app/login")
    resp = await client.post("/app/login", data={"username": username, "password": password, "_csrf": token})
    assert resp.status_code == 200


async def _invitee(prefix: str, role_type: UserRoleType, tenant_id: int) -> User:
    """A real user with the given platform role, added as a web member."""
    role = await Role.objects.get(codename=role_type.name)
    username = _name(prefix)
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
        is_superuser=False,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role="member")
    return user


async def _platform_value() -> str:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.platform_type.db_value


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


async def _make_source(client: AsyncClient, name: str, mode: str = "pull", owner_id: int | None = None) -> int:
    """Create a source through the real add form and return its id."""
    external_id = secrets.token_hex(6)
    token = await _csrf(client, "/app/sources")
    resp = await client.post(
        "/app/sources",
        data={
            "name": name,
            "platform": await _platform_value(),
            "external_id": external_id,
            "source_type": "user",
            "monitored_users": "",
            "mode": mode,
            "token_owner": str(owner_id or ""),
            "_csrf": token,
        },
    )
    assert resp.status_code == 200, "the add form refused a valid source"
    with tenant_scope(bypass=True):
        source = await Source.objects.filter(external_id=external_id).first()
    assert source is not None
    return source.id


async def _drop_source(source_id: int | None) -> None:
    if source_id is not None:
        with tenant_scope(bypass=True):
            await Source.objects.delete(id=source_id)


@pytest.mark.tenancy
async def test_the_source_page_shows_what_collect_pulled_from_it():
    """The per-source collect history must be visible on the source's own page.

    Analytics show what the *analysis* wrote; the collect step's own yield per
    source only existed as one run-wide total in `jobs.result`, so a user could
    not answer "did the collect task get anything from this source?" — the page
    showed a last-checked timestamp and nothing else.
    """
    from datetime import datetime, timedelta, timezone

    from app.models import Job

    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcCollect")
        source_id = None
        job_id = None
        try:
            await _login(client, user.username)
            source_id = await _make_source(client, "источник сбора")

            # Two runs: one that produced content, one that found nothing. Both
            # must be listed, and distinguished.
            now = datetime.now(timezone.utc)
            with tenant_scope(tenant_id):
                full = await Job.objects.create(
                    job_type="collect",
                    status="done",
                    run_at=now - timedelta(hours=2),
                    result={
                        "sources": 1,
                        "collected": 1,
                        "empty": 0,
                        "error": 0,
                        "items": 42,
                        "collected_sources": ["источник сбора"],
                        "empty_sources": [],
                        "error_sources": [],
                        "excluded_sources": [],
                        "error_messages": [],
                        "per_source": [
                            {
                                "source_id": source_id,
                                "name": "источник сбора",
                                "items": 42,
                                "outcome": "collected",
                                "analyzed": 3,
                            }
                        ],
                    },
                )
                job_id = full.id
                await Job.objects.create(
                    job_type="collect",
                    status="done",
                    run_at=now - timedelta(hours=1),
                    result={
                        "sources": 1,
                        "collected": 0,
                        "empty": 1,
                        "error": 0,
                        "items": 0,
                        "collected_sources": [],
                        "empty_sources": ["источник сбора"],
                        "error_sources": [],
                        "excluded_sources": [],
                        "error_messages": [],
                        "per_source": [
                            {
                                "source_id": source_id,
                                "name": "источник сбора",
                                "items": 0,
                                "outcome": "empty",
                                "analyzed": 0,
                            }
                        ],
                    },
                )

            page = await client.get(f"/app/sources/{source_id}")
            assert page.status_code == 200
            assert "Что собрано" in page.text
            # The item count of the run that produced content is on the page...
            assert "42" in page.text
            # ...and the empty run is labelled as empty, not silently dropped.
            assert "собрано" in page.text
            assert "пусто" in page.text
        finally:
            with tenant_scope(bypass=True):
                if job_id is not None:
                    await Job.objects.delete(job_type="collect", created_at__gte=now - timedelta(days=1))
            await _drop_source(source_id)
            await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_a_source_without_collect_history_says_so():
    """No history must read as "not yet collected", never as a fabricated zero."""
    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcNoColl")
        source_id = None
        try:
            await _login(client, user.username)
            source_id = await _make_source(client, "молчащий источник")

            page = await client.get(f"/app/sources/{source_id}")
            assert page.status_code == 200
            assert "Что собрано" in page.text
            assert "ещё не зафиксировано" in page.text
        finally:
            await _drop_source(source_id)
            await _drop(user, tenant_id)


async def test_the_sources_page_does_not_push_the_vk_login():
    """The sources page no longer carries a VK badge; Settings owns that state.

    What it must still get right is *silence*: with no source that needs a
    personal token, nothing about VK is shown at all. A login button parked on
    this page was noise for everyone whose collection works with the community
    token alone.
    """
    client = await _client()
    user, tenant_id = await _register(client, "vkbadge")
    try:
        await _login(client, user.username)

        from app.models.managers.user_credential_manager import user_credentials

        page = await client.get("/app/sources")
        assert page.status_code == 200
        assert "Войти через VK ID" not in page.text

        await user_credentials.store(
            user_id=user.id,
            platform="vk",
            kind="user_token",
            secret="test-token",
            label="VK test",
        )
        page = await client.get("/app/sources")
        assert page.status_code == 200
        assert "Войти через VK ID" not in page.text
    finally:
        await _drop(user, tenant_id)
