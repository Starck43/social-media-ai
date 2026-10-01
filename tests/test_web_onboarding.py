"""Onboarding and navigation for a workspace that starts empty (docs/design/ui.md §4.1).

A fresh sign-up lands on an empty dashboard, which is where the product used to
lose people: the only hint was a flash message. `/app/onboarding` turns that
into a two-step checklist whose forms POST to the *existing* create endpoints
with `next=/app/onboarding`, so there is one implementation of "create" and the
wizard cannot drift from the list pages.

The navigation half pins the two invariants the old template violated: sections
live as data (`app/web/nav.py`, not a tuple literal in the HTML), and the
mobile bar never contains a `#` placeholder — it has no room for a roadmap.

`tests/conftest.py` forces `is_bypass()` for every test, so reads inside the
ASGI stack see *all* workspaces: what a checklist step decides cannot be
asserted by planting rows here. The tests therefore pin the rule the page
implements — a step that is not done carries a form, a step that is done does
not — and let the create endpoints be covered by what they write to the DB.
"""

from __future__ import annotations

import re
import secrets

from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, Platform, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import UserRoleType
from app.web.nav import MOBILE_NAV_ITEMS, NAV_ITEMS

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав для этого действия"
DONE = "готово"
TODO = "не сделано"
# Both wizard forms carry this hidden field; its presence is how a test tells
# "the member got the form" from "the member got the read-only note".
WIZARD_MARKER = 'name="next" value="/app/onboarding"'


# ── navigation as data ─────────────────────────────────────────────────────


def test_every_ready_section_has_a_real_route() -> None:
    # A `#` href is a dead link; placeholders carry `ready=False` instead.
    assert all(item.href.startswith("/app") for item in NAV_ITEMS if item.ready)
    assert all(item.href == "#" for item in NAV_ITEMS if not item.ready)


def test_mobile_bar_only_offers_reachable_sections() -> None:
    # The fixed bottom bar replaces the sidebar below `md`, so it has no room
    # for the "скоро" roadmap — a placeholder there is just a broken tap.
    assert MOBILE_NAV_ITEMS
    assert all(item.ready and item.href.startswith("/app") for item in MOBILE_NAV_ITEMS)
    assert set(MOBILE_NAV_ITEMS) <= set(NAV_ITEMS)


def test_no_dead_links_in_the_sidebar() -> None:
    # Regression: the old template listed a section with a `#` href.
    dead = [item.label for item in NAV_ITEMS if item.ready and item.href == "#"]
    assert dead == []


def test_ready_sections_cover_the_pages_that_exist() -> None:
    keys = {item.key for item in NAV_ITEMS if item.ready}
    assert {"dashboard", "sources", "tasks", "scenarios"} <= keys


# ── the wizard, through the ASGI stack ─────────────────────────────────────


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200, f"{path} -> {page.status_code}"
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up: the new user owns a fresh workspace (membership role `owner`)."""
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
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


async def _platform_value() -> str:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.platform_type.db_value


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


async def _invitee(prefix: str, platform_role: UserRoleType, tenant_id: int, password: str) -> User:
    """A web user in someone else's workspace with a *non*-owner membership."""
    username = _name(prefix)
    role = await Role.objects.get(codename=platform_role.name)
    assert role is not None, f"role {platform_role.name} is seeded"
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password=password,
        role_id=role.id,
        is_superuser=False,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role="member")
    return user


async def test_owner_gets_a_form_for_every_step_that_is_not_done() -> None:
    """A step is either `готово` (no form) or `не сделано` (form + CTA)."""
    async with await _client() as client:
        user, tenant_id = await _register(client, "OnbFresh")
        try:
            page = await client.get("/app/onboarding")
            assert page.status_code == 200
            assert "Начало работы" in page.text
            assert page.text.count(WIZARD_MARKER) == page.text.count(TODO)
            assert page.text.count(DONE) + page.text.count(TODO) == 2, "two steps"
        finally:
            await _drop(user, tenant_id)


async def test_wizard_creates_a_source_and_returns_to_the_wizard() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "OnbSource")
        external_id = secrets.token_hex(6)
        source_id = None
        try:
            platform = await _platform_value()
            token = await _csrf(client, "/app/onboarding")
            resp = await client.post(
                "/app/sources",
                data={
                    "name": "Wizard source",
                    "platform": platform,
                    "external_id": external_id,
                    "source_type": "user",
                    "monitored_users": "",
                    "next": "/app/onboarding",
                    "_csrf": token,
                },
            )
            # It landed back on the wizard, not on the list page.
            assert resp.status_code == 200
            assert resp.url.path == "/app/onboarding"
            assert "добавлен" in resp.text

            with tenant_scope(bypass=True):
                source = await Source.objects.filter(external_id=external_id).first()
            assert source is not None
            source_id = source.id

            # The step reads as done for everyone the wizard is shown to.
            page = await client.get("/app/onboarding")
            assert DONE in page.text
            assert WIZARD_MARKER not in page.text
        finally:
            if source_id is not None:
                with tenant_scope(bypass=True):
                    await Source.objects.delete(id=source_id)
            await _drop(user, tenant_id)


async def test_wizard_creates_a_schedule() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "OnbTask")
        name = _name("wizard-task")
        task_id = None
        try:
            token = await _csrf(client, "/app/onboarding")
            resp = await client.post(
                "/app/tasks",
                data={
                    "name": name,
                    "job_type": "collect",
                    "cron_custom": "0 9 * * *",
                    "next": "/app/onboarding",
                    "_csrf": token,
                },
            )
            assert resp.status_code == 200
            assert resp.url.path == "/app/onboarding"
            assert "создана" in resp.text

            with tenant_scope(bypass=True):
                task = await AgentTask.objects.filter(name=name).first()
            assert task is not None
            task_id = task.id
        finally:
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task_id)
            await _drop(user, tenant_id)


async def test_read_only_member_gets_no_wizard_form_and_cannot_create() -> None:
    owner = member = None
    tenant_id = None
    external_id = secrets.token_hex(6)
    platform = await _platform_value()
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "OnbROwner")
            member = await _invitee("OnbRViewer", UserRoleType.VIEWER, tenant_id, "secret-password-1")

        async with await _client() as m:
            await _login(m, member.username)

            page = await m.get("/app/onboarding")
            assert page.status_code == 200
            assert WIZARD_MARKER not in page.text, "a read-only member must not get the wizard forms"

            # The hidden form is not the check: a crafted POST is refused, and
            # nothing is written.
            token = CSRF_RE.search(page.text).group(1)
            resp = await m.post(
                "/app/sources",
                data={
                    "name": "Sneaky source",
                    "platform": platform,
                    "external_id": external_id,
                    "source_type": "user",
                    "monitored_users": "",
                    "next": "/app/onboarding",
                    "_csrf": token,
                },
            )
            assert resp.status_code == 200
            assert DENIED in resp.text
            with tenant_scope(bypass=True):
                assert await Source.objects.filter(external_id=external_id).first() is None
    finally:
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_next_cannot_be_an_open_redirect() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "OnbRedir")
        try:
            token = await _csrf(client, "/app/onboarding")
            resp = await client.post(
                "/app/sources",
                data={
                    "name": "Redirect probe",
                    "platform": await _platform_value(),
                    "external_id": secrets.token_hex(6),
                    "source_type": "user",
                    "monitored_users": "",
                    "next": "https://evil.example/steal",
                    "_csrf": token,
                },
            )
            # A rejected `next` falls back to the list page — still ours.
            assert resp.url.host == "testserver"
            assert str(resp.url) in ("http://testserver/app/sources",)
        finally:
            with tenant_scope(bypass=True):
                for source in await Source.objects.filter(name="Redirect probe").all():
                    await Source.objects.delete(id=source.id)
            await _drop(user, tenant_id)
