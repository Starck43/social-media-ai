"""The platform-role picker on `/admin/user/edit`.

The manager (`UserManager.set_platform_role`) is the authority and has its own
tests; this file pins the *form*: the edit page offers exactly the roles the
signed-in actor may assign (highest first), a rank that may assign nothing gets
a disabled empty select rather than an editable one, and the create page has no
role field at all. Real SQLAdmin routes, real views, inert storage — no DB, no
engine, so this runs without the shared test schema.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from sqladmin import Admin
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles

from app.admin.views import UserAdmin
from app.core.access_catalog import role_label

ROOT = Path(__file__).resolve().parents[2]


def _actor(codename: str):
    """A `UserAdmin`-grade account by rank only; the view reads `role.codename`."""
    return NS(
        id=1,
        username=f"signed-in-{codename}",
        is_active=True,
        role=NS(codename=codename, label=role_label(codename)),
    )


def _view(actor):
    class Auth(AuthenticationBackend):
        async def authenticate(self, request):
            request.state.admin_user = actor
            return True

    app = Starlette()
    app.mount("/static", StaticFiles(directory=str(ROOT / "app/static")), name="static")
    admin = Admin(
        app,
        session_maker=sessionmaker(),
        authentication_backend=Auth("synthetic-session-key"),
        templates_dir=str(ROOT / "app/templates"),
    )
    admin.templates.env.globals.update(
        csrf_token=lambda: "synthetic-csrf",
        settings=NS(),
        debug=False,
        related_controls=lambda *args, **kwargs: [],
        related_token=AsyncMock(return_value=None),
    )
    admin.add_view(UserAdmin)
    view = admin._views[0]
    assert isinstance(view, UserAdmin)
    return view, actor


def _request(actor):
    return NS(state=NS(admin_user=actor))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actor_role", "expected"),
    [
        ("superuser", ["admin", "operator", "user"]),
        ("admin", ["operator", "user"]),
        ("operator", ["user"]),
        ("user", []),
    ],
)
async def test_edit_form_offers_exactly_what_the_actor_may_assign(actor_role, expected):
    view, actor = _view(_actor(actor_role))
    view._form_request = _request(actor)
    try:
        form_class = await view.scaffold_form(view._form_edit_rules)
    finally:
        view._form_request = None

    field = form_class()._fields.get("role")
    assert field is not None, "the edit page must carry a role picker"
    assert [code for code, _ in field.choices] == expected
    assert all(label for _, label in field.choices), "options must carry readable labels"


@pytest.mark.asyncio
async def test_an_actor_with_nothing_to_assign_gets_a_disabled_select():
    """A client rank assigns nothing, so the select is closed rather than empty-but-editable."""
    view, actor = _view(_actor("user"))
    view._form_request = _request(actor)
    try:
        form_class = await view.scaffold_form(view._form_edit_rules)
    finally:
        view._form_request = None
    field = form_class()._fields["role"]
    assert field.choices == []
    assert field.render_kw.get("disabled") is True


@pytest.mark.asyncio
async def test_create_page_has_no_role_field():
    """New accounts start at `user`; the create form must not offer a rank."""
    view, actor = _view(_actor("superuser"))
    view._form_request = _request(actor)
    try:
        form_class = await view.scaffold_form(view._form_create_rules)
    finally:
        view._form_request = None
    fields = form_class()._fields
    assert "role" not in fields
    assert {"username", "email", "hashed_password", "is_active"} <= set(fields)


@pytest.mark.asyncio
async def test_one_actors_choices_do_not_leak_into_the_next_form():
    """SQLAdmin caches a form class per view; the converter must not."""
    view, owner = _view(_actor("superuser"))
    view._form_request = _request(owner)
    try:
        owner_form = await view.scaffold_form(view._form_edit_rules)
    finally:
        view._form_request = None

    plain = _actor("user")
    view._form_request = _request(plain)
    try:
        plain_form = await view.scaffold_form(view._form_edit_rules)
    finally:
        view._form_request = None

    assert [code for code, _ in owner_form()._fields["role"].choices] == ["admin", "operator", "user"]
    assert plain_form()._fields["role"].choices == []
