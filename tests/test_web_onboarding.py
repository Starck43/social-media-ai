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
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, Platform, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import JobType, UserRoleType
from app.web.nav import MOBILE_NAV_ITEMS, NAV_ITEMS
from app.web.onboarding import SCHEDULE_PRESETS

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав для этого действия"
DONE = "готово"
TODO = "не сделано"
# Both wizard forms carry this hidden field; its presence is how a test tells
# "the member got the form" from "the member got the read-only note".
WIZARD_MARKER = 'name="next" value="/app/onboarding"'
# The control class as it was written out by hand, 80 times across the
# templates. One macro (`_macros.control`) owns it now; a template that spells
# it out again has reintroduced the drift that macro removed.
RAW_CONTROL_CLASS = "w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2"
FORM_FIELD_RE = re.compile(r'<form[^>]*\baction="([^"]+)"[^>]*>(.*?)</form>', re.S)
FIELD_NAME_RE = re.compile(r'\bname="([^"]+)"')
LABEL_FOR_RE = re.compile(r'<label[^>]*\bfor="([^"]+)"')


def _form_fields(html: str, action: str) -> set[str]:
    """Every field name a given form posts.

    Used to compare the wizard against the page it mirrors: both post to the
    same endpoint, so a field that exists in one and not the other is drift,
    not a design choice.
    """
    for found_action, body in FORM_FIELD_RE.findall(html):
        if found_action == action:
            return set(FIELD_NAME_RE.findall(body))
    raise AssertionError(f"no form posting to {action}")


class _Perms:
    """A member who may do everything — the wizard only hides a form."""

    def can(self, *_args, **_kwargs) -> bool:
        return True


def _render(name: str, **extra) -> str:
    """Render a page template directly, with the context its route supplies.

    Not through the ASGI stack: `conftest` forces `is_bypass()`, so a request
    sees *every* workspace's rows and the checklist may read as already done —
    the very thing these tests must be able to see unfinished. Rendering the
    template with an explicit context makes "no source yet" a fact of the test
    rather than of whatever the database happens to hold.
    """
    from app.tasks.cron import cron_to_human
    from app.types import SourceType
    from app.web.deps import templates
    from app.web.nav import MOBILE_NAV_ITEMS, NAV_ITEMS

    platforms = [SimpleNamespace(platform_type=SimpleNamespace(db_value="vk"), name="ВКонтакте")]
    context: dict = {
        "nav": NAV_ITEMS,
        "mobile_nav": MOBILE_NAV_ITEMS,
        "user": None,
        "memberships": [],
        "workspaces": [],
        "tenant": SimpleNamespace(name="WS", slug="ws", plan="free"),
        "unread_notifications": 0,
        "perms": _Perms(),
        "csrf": "test-csrf",
        "flashes": [],
        "path": "/app/",
        "section": "",
        # Shared by both pages under test.
        "platforms": platforms,
        "source_types": list(SourceType),
        "job_types": JobType.choices(),
        "cron_to_human": cron_to_human,
        "modes": (("pull", "Через API платформы"),),
        "members": [],
        # The list pages.
        "is_superuser": False,
        "filter_tenant_id": None,
        "tenants": [],
        "scenarios": [],
        "sources": [],
        "tasks": [],
        "effective_active": set(),
        "job_id": None,
        # The editor's row data and the task a `?task_id=` link names — both
        # empty here: the list pages under test carry no tasks.
        "edit_tasks": {},
        "open_task_id": None,
        # Connection state per source row, plus a banner per blocked platform.
        # Empty here: the list pages under test carry no sources.
        "source_connections": {},
        "connection_banners": [],
        "user_sources_json": [],
        "filter_scenario_id": None,
        # The wizard: a workspace that has neither yet.
        "has_source": False,
        "has_task": False,
        "presets": SCHEDULE_PRESETS,
    }
    context.update(extra)
    return templates.get_template(name).render(**context)


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

            # The step reads as done for everyone the wizard is shown to: the
            # source form is gone, the schedule form legitimately keeps its
            # return-to-wizard marker until a schedule exists.
            page = await client.get("/app/onboarding")
            assert DONE in page.text
            assert page.text.count(WIZARD_MARKER) == 1, "only the schedule form keeps the marker"
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
                    "start_date": "2026-09-01",
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


# ── the wizard and the pages it mirrors share one form definition ──────────


async def test_wizard_posts_exactly_the_fields_the_list_pages_expose() -> None:
    """The regression this refactor exists to prevent.

    The wizard step and the list-page modal post to `/app/sources`, and both
    draw their fields from `_macros.source_fields`. The wizard's fields must
    therefore be a *subset* of the list page's — if someone adds a field to the
    modal and forgets the wizard (or renames one), the fresh-workspace path
    silently stops being able to set it and nothing else fails.

    `mode` and `token_owner` are the documented exception: they tune a source
    that already exists, so the wizard takes the endpoint defaults.
    """
    wizard = _render("web/onboarding.html")

    wizard_source = _form_fields(wizard, "/app/sources")
    list_source = _form_fields(_render("web/sources.html"), "/app/sources")
    # `next` is the wizard's own addition — it is what sends the POST back here
    # instead of to the list page — so it is the one field allowed to exist
    # only in the wizard.
    assert wizard_source - {"next"} <= list_source, f"wizard-only fields: {wizard_source - list_source}"
    assert list_source - wizard_source == {"mode", "token_owner"}

    wizard_task = _form_fields(wizard, "/app/tasks")
    list_task = _form_fields(_render("web/tasks.html"), "/app/tasks")
    assert wizard_task - {"next"} <= list_task, f"wizard-only task fields: {wizard_task - list_task}"
    # The schedule widget differs by design (a preset select vs. the
    # interactive builder), but both post the same field name.
    assert "cron_custom" in wizard_task & list_task


def test_wizard_labels_point_at_real_inputs() -> None:
    """The shared macro renders `label for=` next to the id it names.

    `idp` is what keeps the two copies apart; a prefix that collides would
    leave a label bound to the wrong input, which an assertion on field names
    would not catch.
    """
    for name in ("web/onboarding.html", "web/sources.html", "web/tasks.html"):
        page = _render(name)
        targets = set(LABEL_FOR_RE.findall(page))
        assert targets, f"{name} renders no labelled field"
        for target in targets:
            assert f'id="{target}"' in page, f"{name}: label points at a missing id: {target}"


def test_form_controls_come_from_one_macro() -> None:
    """The control class is defined once, in `_macros.control`.

    It was written out by hand 80 times across the templates — which is how the
    focus ring ended up on some fields and not others. Only the macro itself
    may still spell it out.
    """
    from app.web.deps import templates

    offenders = []
    for name in ("sources.html", "tasks.html", "onboarding.html"):
        source = templates.get_template(f"web/{name}").filename
        with open(source, encoding="utf-8") as fh:
            if RAW_CONTROL_CLASS in fh.read():
                offenders.append(name)
    assert offenders == [], f"control class written out by hand in {offenders}"

    # …and the macro still produces it. `_macros.html` holds only definitions,
    # so rendering it yields nothing: ask the template's module for the macro.
    macros = templates.get_template("web/_macros.html").module
    assert RAW_CONTROL_CLASS in macros.control()


# ── the dashboard welcome walkthrough ───────────────────────────────────────


def _dashboard(kpis, *, show_welcome: bool, scenarios_count: int) -> str:
    """Render the dashboard with explicit welcome state (see `_render`)."""
    return _render(
        "web/dashboard.html",
        kpis=kpis,
        analytics=SimpleNamespace(
            toxicity=SimpleNamespace(analyzed=0, toxic=0, toxic_percent=0.0),
            content_mix=[],
            top_topics=[],
            hashtags=[],
            recent=[],
        ),
        recent_tasks=[],
        effective_active=set(),
        cron_to_human=SimpleNamespace(__call__=lambda e: ""),
        show_welcome=show_welcome,
        scenarios_count=scenarios_count,
        # `plan_badge` reads this; the route gets it from `render()`.
        plan_labels={"free": "Free"},
    )


def test_welcome_walkthrough_lists_all_four_steps() -> None:
    """A fresh workspace sees the step-through, each step linking to its page.

    The walkthrough names the four places a newcomer has to find: the source,
    the agent scenario (the web wizard entry point), the scheduled task and the
    digest channel — "собранный контент никуда не попадёт" is the reason it is
    shown at all.
    """
    empty = SimpleNamespace(
        active_sources=0, active_tasks=0, posts_today=0, avg_sentiment=0.0, sentiment_analyzed=False, cost_today_usd=0.0
    )
    html = _dashboard(empty, show_welcome=True, scenarios_count=0)

    assert "Добро пожаловать — собранный контент никуда не попадёт" in html
    assert 'href="/app/onboarding"' in html, "the guided path stays reachable"
    for href in ("/app/sources", "/app/scenarios/new", "/app/tasks", "/app/digests"):
        assert f'href="{href}"' in html, f"the walkthrough must link to {href}"


def test_welcome_hidden_once_the_workspace_is_set_up() -> None:
    """Sources, scenario and schedule in place — no welcome, the KPIs are it."""
    set_up = SimpleNamespace(
        active_sources=2, active_tasks=1, posts_today=3, avg_sentiment=0.5, sentiment_analyzed=True, cost_today_usd=0.1
    )
    html = _dashboard(set_up, show_welcome=False, scenarios_count=2)
    assert "Добро пожаловать" not in html
    assert "Пройти шаги" not in html
