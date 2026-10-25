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

from app.admin.authorization import bind_console_request
from app.admin.views import UserAdmin
from app.core.access_catalog import role_label
from app.models.identity.user import User

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


def _request(actor, pk=None):
    """A bound console request: the actor, and the edited `pk` when there is one."""
    path_params = {} if pk is None else {"pk": str(pk)}
    return NS(state=NS(admin_user=actor), path_params=path_params)


def _account(user_id, codename, *, active=True):
    """A loaded target: `role_edit` and `_is_platform_owner` read `role.codename`."""
    account = User(is_active=active)
    account.__dict__["id"] = user_id
    account.__dict__["role"] = NS(codename=codename, label=role_label(codename))
    return account


def _patch_target_lookup(monkeypatch, target):
    """The edit route's `User.objects.prefetch_related("role").get(id=pk)`."""

    class _Query:
        async def get(self, *, id, **kwargs):
            return target if id == getattr(target, "id", None) else None

    class _Objects:
        def prefetch_related(self, *args):
            return _Query()

    monkeypatch.setattr(User, "objects", _Objects(), raising=False)


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
    bind_console_request(_request(actor))
    form_class = await view.scaffold_form(view._form_edit_rules)

    field = form_class()._fields.get("role")
    assert field is not None, "the edit page must carry a role picker"
    assert [code for code, _ in field.choices] == expected
    assert all(label for _, label in field.choices), "options must carry readable labels"


@pytest.mark.asyncio
async def test_an_actor_with_nothing_to_assign_gets_a_disabled_select():
    """A client rank assigns nothing, so the select is closed rather than empty-but-editable."""
    view, actor = _view(_actor("user"))
    bind_console_request(_request(actor))
    form_class = await view.scaffold_form(view._form_edit_rules)
    field = form_class()._fields["role"]
    assert field.choices == []
    assert field.render_kw.get("disabled") is True


@pytest.mark.asyncio
async def test_create_page_has_no_role_field():
    """New accounts start at `user`; the create form must not offer a rank."""
    view, actor = _view(_actor("superuser"))
    bind_console_request(_request(actor))
    form_class = await view.scaffold_form(view._form_create_rules)
    fields = form_class()._fields
    assert "role" not in fields
    assert {"username", "email", "hashed_password", "is_active"} <= set(fields)


@pytest.mark.asyncio
async def test_one_actors_choices_do_not_leak_into_the_next_form():
    """SQLAdmin caches a form class per view; the converter must not."""
    view, owner = _view(_actor("superuser"))
    bind_console_request(_request(owner))
    owner_form = await view.scaffold_form(view._form_edit_rules)

    plain = _actor("user")
    bind_console_request(_request(plain))
    plain_form = await view.scaffold_form(view._form_edit_rules)

    assert [code for code, _ in owner_form()._fields["role"].choices] == ["admin", "operator", "user"]
    assert plain_form()._fields["role"].choices == []


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_active", [False, True], ids=["inactive-owner", "active-owner"])
async def test_editing_the_owner_shows_the_owner_role_only_with_a_reason(monkeypatch, owner_active):
    """Ownership follows the role, not `is_superuser`, which drops with `is_active`.

    A suspended owner is exactly the account nobody may re-role, so an admin
    editing it must still see the owner role alone and a closed select.
    """
    owner = _account(2, "superuser", active=owner_active)
    _patch_target_lookup(monkeypatch, owner)
    view, actor = _view(_actor("admin"))
    bind_console_request(_request(actor, pk=2))

    form_class = await view.scaffold_form(view._form_edit_rules)
    field = form_class()._fields["role"]

    assert [code for code, _ in field.choices] == ["superuser"]
    assert field.render_kw.get("disabled") is True
    assert field.description == "Роль собственника платформы не меняется из консоли"


@pytest.mark.asyncio
async def test_editing_your_own_account_shows_your_role_only_with_a_reason(monkeypatch):
    admin = _actor("admin")
    _patch_target_lookup(monkeypatch, _account(1, "admin"))
    view, _ = _view(admin)
    bind_console_request(_request(admin, pk=1))

    form_class = await view.scaffold_form(view._form_edit_rules)
    field = form_class()._fields["role"]

    assert [code for code, _ in field.choices] == ["admin"]
    assert field.render_kw.get("disabled") is True
    assert "Свою платформенную роль" in field.description


@pytest.mark.asyncio
async def test_editing_an_ordinary_account_keeps_the_actor_choices_editable(monkeypatch):
    target = _account(2, "user")
    _patch_target_lookup(monkeypatch, target)
    view, actor = _view(_actor("admin"))
    bind_console_request(_request(actor, pk=2))

    form_class = await view.scaffold_form(view._form_edit_rules)
    field = form_class()._fields["role"]

    assert [code for code, _ in field.choices] == ["operator", "user"]
    assert not field.render_kw.get("disabled")
