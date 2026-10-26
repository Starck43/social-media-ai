"""Who may write the owner of a catalog row — and whose rows the console shows.

Two separate questions, both answered on source alone (no database, no console
mount, no HTTP server), so an auditor can check them without a fixture:

* **the catalog is personal.** `llm_provider` / `llm_model` are
  `__personal_scoped__`, so their manager filters every query by the *current*
  catalog owner and fails closed when none is bound. The console therefore runs
  each of its queries as the signed-in operator — one hook on `BaseAdmin`
  (`_catalog_scope`, used by `get_object_for_edit`/`get_object_for_details` and
  by every read/write of the two LLM views). A grant never widens it: the
  platform owner sees their own catalog, an `admin` theirs.
* **the owner field is a rule, not a text box.** `LLMProviderAdmin` offers
  `owner_user_id` to the platform owner only; for anyone else the field is not in
  the form and `on_model_change` stamps the actor, so a forged POST is dropped
  too. `LLMModelAdmin` has no owner field at all: the model inherits the
  provider's owner, and the provider is looked up inside the actor's scope — a
  foreign id reads as "not found".

The views are exercised as plain objects that borrow the real `BaseAdmin`
attributes, so `super()` calls land on the real base without sqladmin's
constructor.
"""

from __future__ import annotations

# Source-only: these tests never read or write storage (no ORM call, no schema).
# `tests/conftest.py` skips the suite database when only such modules are selected.
__source_only__ = True

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqladmin import ModelView
from sqladmin.fields import SelectField
from starlette.requests import Request

from app.admin import views as console
from app.admin.base import BaseAdmin
from app.admin.views import LLMModelAdmin, LLMProviderAdmin
from app.core.personal_catalog_context import _catalog_owner

SUPERUSER = SimpleNamespace(id=2, username="starck", is_superuser=True, is_active=True)
ADMIN = SimpleNamespace(id=1, username="operator", is_superuser=False, is_active=True)

PROVIDER_MODEL = type("Provider", (), {"__personal_scoped__": True, "__name__": "Provider"})
PLAIN_MODEL = type("Plain", (), {"__name__": "Plain"})


def _awaitable(value):
    async def inner():
        return value

    return inner()


def _request(actor) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/admin/llm-provider/create", "headers": []})
    request.state.admin_user = actor
    return request


_DOUBLES: dict[int, type] = {}


def _double_class(cls) -> type:
    """`BaseAdmin`'s base with `cls`'s own overrides, built without sqladmin.

    A real subclass on purpose: the overrides call `super()`, which needs an
    instance of the class it names, and sqladmin's constructor needs a session
    maker and an admin instance these rules never read.
    """
    if id(cls) not in _DOUBLES:
        _DOUBLES[id(cls)] = type(
            f"_Double{cls.__name__}",
            (cls,),
            {"__init__": lambda self, model: None},
        )
    return _DOUBLES[id(cls)]


def _view(cls=LLMProviderAdmin, model=PROVIDER_MODEL):
    instance = object.__new__(_double_class(cls))
    instance.model = model
    # sqladmin fills these in its constructor, which needs a session maker and
    # an admin instance; the form rule only reads the column list.
    instance._form_prop_names = list(cls.form_columns)
    instance.session_maker = None
    for name in ("_column_labels", "_form_args", "_form_widget_args", "_form_overrides", "_form_ajax_refs"):
        setattr(instance, name, getattr(cls, name.lstrip("_") if name != "_column_labels" else "column_labels",
                                        getattr(cls, name, {})))
    instance.form_base_class = getattr(cls, "form_base_class", None)
    instance.form_include_pk = getattr(cls, "form_include_pk", False)
    instance.form_converter = cls.form_converter
    return instance


def _scoped(instance) -> bool:
    return instance.catalog_scoped


# --- the personal catalog scope -------------------------------------------


def test_only_the_catalog_views_are_personal_scoped():
    from app.models import LLMModel, LLMProvider

    assert LLMProvider.__personal_scoped__ is True
    assert LLMModel.__personal_scoped__ is True
    assert _scoped(_view()) is True
    assert _scoped(_view(model=PLAIN_MODEL)) is False


def test_the_scope_binds_the_signed_in_operator():
    view = _view()
    with view._catalog_scope(_request(ADMIN)):
        assert _catalog_owner.get() == ADMIN.id
    assert _catalog_owner.get() is None


def test_the_scope_never_leaks_between_actors():
    view = _view()
    with view._catalog_scope(_request(ADMIN)):
        with view._catalog_scope(_request(SUPERUSER)):
            assert _catalog_owner.get() == SUPERUSER.id
        assert _catalog_owner.get() == ADMIN.id
    assert _catalog_owner.get() is None


def test_no_actor_means_no_scope():
    """Fail closed: the manager refuses instead of answering for someone else."""
    with _view()._catalog_scope(None):
        assert _catalog_owner.get() is None


def test_a_plain_view_does_not_bind_a_catalog():
    with _view(model=PLAIN_MODEL)._catalog_scope(_request(ADMIN)):
        assert _catalog_owner.get() is None


def _catalog_model():
    """A model double whose `owner_user_id` renders as `owner_user_id = N`."""
    from sqlalchemy import Column, Integer
    from sqlalchemy.orm import declarative_base

    base = declarative_base()

    class Rows(base):
        __tablename__ = "rows"
        __personal_scoped__ = True
        id = Column(Integer, primary_key=True)
        owner_user_id = Column(Integer)

    return Rows


def _where(statement) -> str:
    """A `SELECT`'s `WHERE`, or `None`; a bare `==` expression passes through."""
    from sqlalchemy import Select

    return str(statement.whereclause) if isinstance(statement, Select) else str(statement)


def test_the_list_page_is_restricted_to_the_operators_own_rows():
    """sqladmin builds `select(model)` itself, so the list needs its own filter.

    The platform owner is no exception: one catalog per person, and the count
    query follows the same rule or the pager shows pages that are not there.
    """
    rows = _catalog_model()
    view = _view(model=rows)
    for actor in (ADMIN, SUPERUSER):
        request = _request(actor)
        assert _where(view.list_query(request)) == str(rows.owner_user_id == actor.id)
        assert _where(view.count_query(request)) == str(rows.owner_user_id == actor.id)


def test_the_list_page_shows_nothing_without_an_actor():
    rows = _catalog_model()
    view = _view(model=rows)
    request = Request({"type": "http", "method": "GET", "path": "/admin/llm-provider/list", "headers": []})
    assert _where(view.list_query(request)) == "false"
    assert _where(view.count_query(request)) == "false"


def test_a_plain_view_lists_everything():
    rows = _catalog_model()
    view = _view(model=rows)
    view.model = type("Plain", (rows,), {"__personal_scoped__": False, "__table__": rows.__table__})
    assert _where(view.list_query(_request(ADMIN))) == "None"


def test_the_row_reads_run_inside_the_scope():
    view = _view()
    seen = []

    async def base(self, request):
        seen.append(_catalog_owner.get())
        return SimpleNamespace()

    original = ModelView.get_object_for_edit
    ModelView.get_object_for_edit = base
    try:
        asyncio.run(view.get_object_for_edit(_request(ADMIN)))
    finally:
        ModelView.get_object_for_edit = original
    assert seen == [ADMIN.id]
    assert _catalog_owner.get() is None


# --- the provider owner field ---------------------------------------------


@contextmanager
def _columns():
    """`get_model_form` served as a form double of the requested columns.

    The view rebuilds the form per actor, so the double is decided by the
    `only=` list: that is exactly what the view asks for, and it makes "the
    owner is not in an admin's form" a property of the request, not of a form
    instance somebody mutated earlier.
    """

    def build(**kwargs):
        names = kwargs["only"]
        fields = {}
        for name in names:
            given = (kwargs.get("form_args") or {}).get(name, {})
            fields[name] = SimpleNamespace(
                render_kw={}, description="", choices=given.get("choices", []), coerce=given.get("coerce")
            )
        return SimpleNamespace(**fields)

    target = "app.admin.views.get_model_form"

    async def fake(**kwargs):
        return build(**kwargs)

    module = console
    original = module.get_model_form
    module.get_model_form = fake
    try:
        yield build
    finally:
        module.get_model_form = original


@contextmanager
def _owners(*users):
    from app.models import User

    class Query:
        def filter(self, **criteria):
            return self

        def order_by(self, *names):
            return _awaitable(list(users))

    original = User.objects
    User.objects = SimpleNamespace(filter=lambda **_: Query())
    try:
        yield
    finally:
        User.objects = original


async def _scaffold(view, request):
    """`scaffold_form` reads the actor from the request the backend bound."""
    from app.admin.authorization import _console_request

    token = _console_request.set(request)
    try:
        return await view.scaffold_form(None)
    finally:
        _console_request.reset(token)


def test_an_admin_gets_no_owner_field():
    view = _view()
    with _columns(), _owners(SUPERUSER, ADMIN):
        form = asyncio.run(_scaffold(view, _request(ADMIN)))
    assert not hasattr(form, "owner_user_id")
    assert hasattr(form, "uid")


def test_the_platform_owner_picks_an_owner_by_username():
    view = _view()
    with _columns(), _owners(SUPERUSER, ADMIN):
        form = asyncio.run(_scaffold(view, _request(SUPERUSER)))
    assert hasattr(form, "owner_user_id")
    assert form.owner_user_id.choices == [("2", "starck"), ("1", "operator")]


def test_the_form_is_rebuilt_per_actor_not_cached_from_the_first_request():
    """`scaffold_form` caches on the instance; the owner must not ride along."""
    view = _view()
    with _columns(), _owners(SUPERUSER, ADMIN):
        asyncio.run(_scaffold(view, _request(SUPERUSER)))
        second = asyncio.run(_scaffold(view, _request(ADMIN)))
    assert not hasattr(second, "owner_user_id")


def test_the_owner_field_is_a_declared_override_because_it_is_a_foreign_key():
    """sqladmin drops an FK column from the form unless it is overridden.

    Pins the reason `form_overrides` carries `owner_user_id`: without it the
    column named in `form_columns` produces no widget at all.
    """
    assert "owner_user_id" in LLMProviderAdmin.form_columns
    assert LLMProviderAdmin.form_overrides.get("owner_user_id") is SelectField


# --- what `on_model_change` writes ----------------------------------------


def _change(view, data, model, is_created, request):
    """Run the rule with the shared base write stubbed out."""
    original = BaseAdmin.on_model_change
    BaseAdmin.on_model_change = AsyncMock()
    try:
        return asyncio.run(view.on_model_change(data, model, is_created, request))
    finally:
        BaseAdmin.on_model_change = original


def test_an_admin_creates_a_provider_in_their_own_catalog_whatever_was_posted():
    model = SimpleNamespace(uid=None)
    data = {"name": "UnoRouter.AI", "uid": "UnoRouter.AI", "owner_user_id": SUPERUSER.id}

    _change(_view(), data, model, True, _request(ADMIN))

    assert data["owner_user_id"] == ADMIN.id and model.owner_user_id == ADMIN.id
    assert data["uid"] == model.uid == "unorouter"


def test_a_forged_owner_on_edit_is_dropped_for_an_admin():
    data = {"uid": "zeta", "owner_user_id": SUPERUSER.id}

    _change(_view(), data, SimpleNamespace(uid="zeta"), False, _request(ADMIN))

    assert "owner_user_id" not in data


def test_the_platform_owner_may_reassign_a_provider():
    data = {"uid": "zeta", "owner_user_id": ADMIN.id}

    _change(_view(), data, SimpleNamespace(uid="zeta"), True, _request(SUPERUSER))

    assert data["owner_user_id"] == ADMIN.id


def test_a_name_without_a_usable_slug_is_refused_before_any_write():
    with pytest.raises(HTTPException) as error:
        _change(_view(), {"name": "...", "uid": "..."}, SimpleNamespace(), True, _request(ADMIN))
    assert error.value.status_code == 400


# --- the model: no owner field, owner inherited from the provider ----------


def test_the_model_view_has_no_owner_field():
    assert "owner_user_id" not in LLMModelAdmin.form_columns
    assert "provider" in LLMModelAdmin.form_columns


def _manager(provider):
    """`LLMProvider.objects.get` answering only rows of the bound owner."""

    class Providers:
        def get(self, **criteria):
            owns = provider is not None and _catalog_owner.get() == provider.owner_user_id
            return _awaitable(provider if owns else None)

    return Providers()


def test_an_admin_cannot_file_a_model_into_a_foreign_catalog():
    from app.models import LLMProvider

    view = _view(LLMModelAdmin, type("Model", (), {"__personal_scoped__": True, "__name__": "Model"}))
    original = LLMProvider.objects
    LLMProvider.objects = _manager(SimpleNamespace(id=44, owner_user_id=SUPERUSER.id))
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(view.insert_model(_request(ADMIN), {"provider": "44"}))
    finally:
        LLMProvider.objects = original
    assert error.value.status_code == 400


def test_a_new_model_inherits_the_owner_of_its_own_provider():
    from app.models import LLMProvider

    view = _view(LLMModelAdmin, type("Model", (), {"__personal_scoped__": True, "__name__": "Model"}))
    captured = {}

    async def insert(self, request, data):
        captured.update(data)
        return SimpleNamespace(id=99)

    original_objects = LLMProvider.objects
    original_insert = ModelView.insert_model
    LLMProvider.objects = _manager(SimpleNamespace(id=7, owner_user_id=ADMIN.id))
    ModelView.insert_model = insert
    try:
        asyncio.run(view.insert_model(_request(ADMIN), {"provider": "7", "name": "Probe"}))
    finally:
        LLMProvider.objects = original_objects
        ModelView.insert_model = original_insert

    assert captured["owner_user_id"] == ADMIN.id
    assert captured["provider_id"] == 7
    assert "provider" not in captured


def test_editing_a_model_never_resolves_the_composite_relation():
    """`provider` carries two FK columns; sqladmin can only resolve one."""
    view = _view(LLMModelAdmin, type("Model", (), {"__personal_scoped__": True, "__name__": "Model"}))
    data = {"name": "Renamed", "provider": "44", "provider_id": 44}

    original = BaseAdmin.on_model_change
    BaseAdmin.on_model_change = AsyncMock()
    try:
        asyncio.run(view.on_model_change(data, SimpleNamespace(), False, _request(ADMIN)))
    finally:
        BaseAdmin.on_model_change = original

    assert data == {"name": "Renamed"}


# --- the grants: only the platform owner writes without a grant -----------


def _grants(granted):
    from app.admin.authorization import BUILTIN_ACTION_TYPES

    return {
        (identity, action_name)
        for identity in ("llm-provider", "llm-model")
        for action_name, action_type in BUILTIN_ACTION_TYPES.items()
        if action_type in granted
    }


def test_an_admin_needs_an_explicit_llm_right_to_add_or_change_a_provider():
    """An `admin` is not a wildcard holder: its console is its stored grants.

    Reading `role_permission` is what decides this; the assertion is on the
    mapping the backend applies. VIEW gives list/details only, CREATE adds the
    create form, UPDATE the edit form. The platform owner is checked next.
    """
    from app.types import ActionType

    viewer = _grants({ActionType.VIEW})
    assert {action for _, action in viewer} == {"list", "details"}
    assert ("llm-provider", "create") not in viewer and ("llm-provider", "edit") not in viewer
    assert ("llm-model", "create") not in viewer and ("llm-model", "edit") not in viewer

    writer = _grants({ActionType.VIEW, ActionType.UPDATE})
    assert ("llm-provider", "edit") in writer
    assert ("llm-provider", "create") not in writer


def test_only_the_platform_owner_holds_the_wildcard():
    from app.admin.authorization import AdminAuthorizationBackend
    from app.types import ActionType

    backend = AdminAuthorizationBackend()
    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace()))

    backend.resolve_user = AsyncMock(return_value=SimpleNamespace(is_superuser=True, has_admin_access=lambda: True))
    assert asyncio.run(backend.get_grants(request)) == {("*", "*")}

    admin = SimpleNamespace(
        is_superuser=False,
        has_admin_access=lambda: True,
        model_permissions=lambda name: {ActionType.VIEW} if name == "llmprovider" else set(),
    )
    backend.resolve_user = AsyncMock(return_value=admin)
    backend._catalog = lambda request: ({"llm-provider": "llmprovider"}, {})
    grants = asyncio.run(backend.get_grants(request))
    assert ("llm-provider", "create") not in grants and ("llm-provider", "edit") not in grants
    assert ("llm-provider", "list") in grants
