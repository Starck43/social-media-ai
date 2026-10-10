"""Source detail `/app/sources/{id}` and its mutations (docs/design/ui.md §4.3).

The list page shows *what* the workspace has. This covers the three things it
never showed and the SQL console used to answer: which collection layer a source
runs on, whose personal token it borrows, and what came out of it.

`params["mode"]` and `params["token_owner"]` are the two knobs `owner.py` and
`COLLECTION_MODES` already honour, which the form used to never expose — so the
operator had to guess or edit the DB.
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, Platform, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.web.sources import COLLECTION_MODES, _validate_token_owner

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав"
MODE_USER = "user"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _login(client: AsyncClient, username: str, password: str = "secret-password-1") -> None:
    """Sign a client in — a fresh client starts anonymous and would only
    ever see the login page, which would pass for 'not found' by accident."""
    token = await _csrf(client, "/app/login")
    resp = await client.post("/app/login", data={"username": username, "password": password, "_csrf": token})
    assert resp.status_code == 200


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


async def _platform_value() -> str:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.platform_type.db_value


async def _make_source(tenant_id: int, name: str, external_id: str, params: dict | None = None) -> Source:
    """Create a source row directly, in a known workspace.

    The add/edit forms cannot be used to build a fixture: `conftest` stubs
    `is_bypass() -> True` for every test, so the managers stop stamping
    `tenant_id` and the row lands in the bootstrap workspace instead of the
    caller's. The route's own tenant check then (correctly) refuses to show it.
    """
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    from app.types import SourceType

    with tenant_scope(bypass=True):
        return await Source.objects.create(
            name=name,
            platform_id=platform.id,
            external_id=external_id,
            source_type=SourceType.USER,
            is_active=True,
            params=params or {},
            tenant_id=tenant_id,
        )


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


# ── token_owner validation (unit) ─────────────────────────────────────────


def test_token_owner_is_accepted_only_for_a_workspace_member() -> None:
    # `owner.resolve_source_owner` ignores a non-member id at read time and falls
    # back to the workspace owner; the form rejects it outright so the operator
    # finds out at edit time rather than as a silent collection failure.
    value, error = _validate_token_owner("42", {42, 43})
    assert value == 42 and error is None

    value, error = _validate_token_owner("99", {42, 43})
    assert value is None
    assert error and "99" in error


def test_blank_token_owner_means_the_workspace_default() -> None:
    # "" clears the key — that is how one switches back to the default owner.
    value, error = _validate_token_owner("", {1})
    assert value is None and error is None


def test_garbage_token_owner_is_rejected_not_silently_dropped() -> None:
    value, error = _validate_token_owner("abc", {1})
    assert value is None
    assert error


# ── through the ASGI stack ─────────────────────────────────────────────────


async def test_the_add_form_writes_mode_and_token_owner_into_params() -> None:
    """`mode` and `token_owner` used to be unreachable from the UI.

    `owner.resolve_source_owner` and the collector read them out of
    `Source.params`, so a form that silently dropped them made the source
    collect with whatever the workspace default happened to be.
    """
    external_id = secrets.token_hex(6)
    source_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcParams")
        try:
            token = await _csrf(client, "/app/sources")
            resp = await client.post(
                "/app/sources",
                data={
                    "name": "Detailed source",
                    "platform": await _platform_value(),
                    "external_id": external_id,
                    "source_type": "user",
                    "monitored_users": "",
                    "mode": MODE_USER,
                    "token_owner": "",
                    "_csrf": token,
                },
            )
            assert resp.status_code == 200
            with tenant_scope(bypass=True):
                source = await Source.objects.filter(external_id=external_id).first()
            assert source is not None
            source_id = source.id
            assert (source.params or {}).get("mode") == MODE_USER
        finally:
            if source_id is not None:
                with tenant_scope(bypass=True):
                    await Source.objects.delete(id=source_id)
            await _drop(user, tenant_id)


async def test_detail_page_explains_how_the_source_collects() -> None:
    external_id = secrets.token_hex(6)
    source_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcDetail")
        try:
            source = await _make_source(tenant_id, "Detailed source", external_id, {"mode": MODE_USER})
            source_id = source.id

            page = await client.get(f"/app/sources/{source_id}")
            assert page.status_code == 200
            # The mode is now visible instead of living only in the params JSON.
            assert "Как собирается" in page.text
            assert dict(COLLECTION_MODES)[MODE_USER] in page.text
        finally:
            if source_id is not None:
                with tenant_scope(bypass=True):
                    await Source.objects.delete(id=source_id)
            await _drop(user, tenant_id)


async def test_collect_now_runs_inline_for_that_source_only() -> None:
    """«Собрать сейчас» must finish before the response, not sit in the queue.

    It used to `enqueue` and flash "поставлен в очередь", redirecting with a
    `job_id` that this page cannot render (the run modal lives on the tasks
    page) — so the button only *requested* a collection, and the work happened
    whenever a worker was free.
    """
    external_id = secrets.token_hex(6)
    source_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcCollect")
        try:
            source = await _make_source(tenant_id, "Collect me", external_id, {"mode": MODE_USER})
            source_id = source.id

            token = await _csrf(client, f"/app/sources/{source_id}")
            resp = await client.post(
                f"/app/sources/{source_id}/collect",
                data={"_csrf": token},
            )
            assert resp.status_code == 200
            assert str(resp.url).endswith(f"/app/sources/{source_id}")
            assert "job_id=" not in str(resp.url), "no dead modal parameter"
            assert "в очередь" not in resp.text
            assert "завершён" in resp.text

            from app.models import Job

            with tenant_scope(bypass=True):
                job = await Job.objects.filter(job_type="collect").order_by(Job.id.desc()).first()
            assert job is not None
            assert (job.payload or {}).get("source_ids") == [source_id]
            # Executed, not queued: the worker never saw it as claimable work.
            assert job.status in ("done", "failed")
            assert job.result is not None or job.error is not None
        finally:
            if source_id is not None:
                with tenant_scope(bypass=True):
                    await Source.objects.delete(id=source_id)
            await _drop(user, tenant_id)


def test_collection_modes_are_labeled_for_the_operator() -> None:
    # `mode` is a contract with the collector, not a free-text field: the UI may
    # relabel it but must keep the three values the runtime understands.
    assert {value for value, _hint in COLLECTION_MODES} == {"pull", "push", MODE_USER}
    assert all(hint for _value, hint in COLLECTION_MODES), "every mode explains itself"


async def test_delete_removes_the_source_and_reports_the_cascade() -> None:
    external_id = secrets.token_hex(6)
    source_id = None
    async with await _client() as client:
        user, tenant_id = await _register(client, "SrcDelete")
        try:
            source = await _make_source(tenant_id, "Doomed source", external_id, {"mode": MODE_USER})
            source_id = source.id

            token = await _csrf(client, f"/app/sources/{source_id}")
            resp = await client.post(f"/app/sources/{source_id}/delete", data={"_csrf": token})
            assert resp.status_code == 200
            assert str(resp.url).endswith("/app/sources")
            # The message has to name the cascade, or it reads as "just the row".
            assert "вместе с его аналитикой" in resp.text
            with tenant_scope(bypass=True):
                assert await Source.objects.get(id=source_id) is None
        finally:
            if source_id is not None:
                with tenant_scope(bypass=True):
                    await Source.objects.delete(id=source_id)
            await _drop(user, tenant_id)


async def test_a_workspace_may_not_touch_another_workspaces_source() -> None:
    """Two fresh workspaces: B must neither read A's source nor delete it.

    The list page filters by tenant, so this is the only guard on the detail
    route and its buttons.
    """
    other_external = secrets.token_hex(6)
    async with await _client() as owner_client:
        owner, owner_tenant = await _register(owner_client, "SrcOwn")
    async with await _client() as client:
        other, other_tenant = await _register(client, "SrcOth")
    source_id = None
    try:
        # Created as a row, not through the form: the fixture has to live in A's
        # workspace, and `conftest`'s tenant bypass would stamp the form-created
        # row into the bootstrap workspace instead (see `_make_source`).
        source = await _make_source(owner_tenant, "Private source", other_external)
        source_id = source.id

        async with await _client() as intruder:
            await _login(intruder, other.username)
            page = await intruder.get(f"/app/sources/{source_id}")
            assert page.status_code == 200
            # Renders the not-found branch: no name, no delete button.
            assert "Private source" not in page.text
            assert f"/app/sources/{source_id}/delete" not in page.text

            token = await _csrf(intruder, "/app/sources")
            resp = await intruder.post(f"/app/sources/{source_id}/delete", data={"_csrf": token})
            assert resp.status_code == 200
            assert "не найден" in resp.text
            with tenant_scope(bypass=True):
                assert await Source.objects.get(id=source_id) is not None
    finally:
        if source_id is not None:
            with tenant_scope(bypass=True):
                await Source.objects.delete(id=source_id)
        await _drop(other, other_tenant)
        await _drop(owner, owner_tenant)


@pytest.mark.tenancy
@pytest.mark.parametrize("read_only", [False, True], ids=["owner", "member"])
@pytest.mark.parametrize(
    "stored_name",
    [
        'Source <img data-xss-probe=p onerror=alert(1)> & "quotes"',
        'Source </title><img data-xss-probe=p onerror=alert(1)>',
        'Source" autofocus onfocus="alert(1)" data-xss-probe="p',
    ],
    ids=["text", "title-close", "quoted-value"],
)
async def test_source_name_is_text_in_list_detail_title_dialog_and_input(read_only, stored_name):
    from html.parser import HTMLParser

    from app.core.permissions import service_permission_scope
    from app.models import Job, Role
    from app.types import SourceType, UserRoleType

    class SourceNameParser(HTMLParser):
        def __init__(self, source_id):
            super().__init__()
            self.source_href = f"/app/sources/{source_id}"
            self.active = {}
            self.texts = {"title": [], "h1": [], "p": [], "source-link": []}
            self.values = []
            self.unsafe = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if any(key in attributes for key in ("data-xss-probe", "onerror", "onfocus")):
                self.unsafe.append((tag, attributes))
            if tag in {"title", "h1", "p"}:
                self.active[tag] = []
            if tag == "a" and attributes.get("href") == self.source_href:
                self.active["source-link"] = []
            if tag == "input" and attributes.get("name") == "name":
                self.values.append(attributes.get("value"))

        def handle_endtag(self, tag):
            key = "source-link" if tag == "a" else tag
            if key in self.active:
                self.texts[key].append("".join(self.active.pop(key)).strip())

        def handle_data(self, data):
            for parts in self.active.values():
                parts.append(data)

    owner = member = None
    tenant_id = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "SourceEscape")
            platform = await Platform.objects.filter(is_active=True).first()
            assert platform is not None
            with tenant_scope(tenant_id), service_permission_scope("source", "create"):
                source = await Source.objects.create(
                    name=stored_name, platform_id=platform.id, external_id=_name("escape-ext"),
                    source_type=SourceType.USER, is_active=True, params={}, tenant_id=tenant_id,
                )
            if read_only:
                role = await Role.objects.filter(codename=UserRoleType.VIEWER.name).first()
                assert role is not None
                username = _name("sourceviewer")
                member = await User.objects.create_user(
                    username=username, email=f"{username}@example.com", password="secret-password-1",
                    role_id=role.id, is_superuser=False,
                )
                await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=member.id, role="member")
                async with await _client() as viewer_client:
                    await _login(viewer_client, member.username)
                    listing = await viewer_client.get("/app/sources")
                    detail = await viewer_client.get(f"/app/sources/{source.id}")
            else:
                listing = await client.get("/app/sources")
                detail = await client.get(f"/app/sources/{source.id}")
        assert listing.status_code == detail.status_code == 200
        list_parser = SourceNameParser(source.id)
        list_parser.feed(listing.text)
        detail_parser = SourceNameParser(source.id)
        detail_parser.feed(detail.text)
        assert list_parser.texts["source-link"] == [stored_name]
        assert detail_parser.texts["title"] == [stored_name + " — AI Monitor"]
        assert detail_parser.texts["h1"] == [stored_name]
        assert detail_parser.values == [stored_name], "the quoted input must retain the entire original name"
        assert not list_parser.unsafe and not detail_parser.unsafe
        assert stored_name not in listing.text and stored_name not in detail.text
        delete_action = f'action="/app/sources/{source.id}/delete"'
        if read_only:
            assert delete_action not in detail.text
        else:
            assert delete_action in detail.text
            assert any(f"«{stored_name}»" in text for text in detail_parser.texts["p"])
        with tenant_scope(tenant_id):
            unchanged = await Source.objects.get(id=source.id)
            assert unchanged.name == stored_name and unchanged.is_active
            assert await Job.objects.filter().count() == 0, "rendering must not enqueue collection or analysis"
    finally:
        if member is not None:
            await User.objects.delete_user(member.id)
        await _drop(owner, tenant_id)
