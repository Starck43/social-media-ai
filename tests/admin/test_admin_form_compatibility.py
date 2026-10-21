"""Canonical Tenant form and numeric converter checks; no settings, DB or app."""

import ast
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from source_import_isolation import load_isolated_source
from sqlalchemy import DOUBLE, FLOAT, REAL, Column, Double, Float, Integer, Numeric, inspect
from sqlalchemy.orm import DeclarativeBase
from starlette.datastructures import FormData
from wtforms import DecimalField, FloatField

ROOT = Path(__file__).resolve().parents[2]


def no_storage(*args, **kwargs):
    raise AssertionError("Form scaffolding must not perform storage I/O")


def admin_source(path=None):
    formatting = NS(
        **{
            name: (lambda *args: "")
            for name in ["bool_formatter", "date_formatter", "datetime_formatter", "empty_formatter"]
        }
    )
    return load_isolated_source(
        "app.admin._numeric_base",
        path or ROOT / "app/admin/base.py",
        {"app.types": NS(ActionType=object), "app.admin.formatters": formatting},
    )


def tenant_case(base_source=None):
    config = NS(settings=NS(DB_SCHEMA="synthetic_no_database"))
    orm = load_isolated_source("app.models._numeric_base", ROOT / "app/models/base.py", {"app.core.config": config})
    tenant = load_isolated_source(
        "app.models.tenancy._numeric_tenant",
        ROOT / "app/models/tenancy/tenant.py",
        {
            "app.core.config": config,
            "app.core.decorators": NS(app_label=lambda _: lambda cls: cls),
            "app.models.base": orm,
        },
    )
    base = admin_source(base_source)
    node = next(
        n
        for n in ast.parse((ROOT / "app/admin/views.py").read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "TenantAdmin"
    )
    ns = {"BaseAdmin": base.BaseAdmin, "Tenant": tenant.Tenant, "plan_badge": lambda value: value}
    tree = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(tree), "canonical-TenantAdmin", "exec"), ns)
    view = ns["TenantAdmin"]()
    view.session_maker = no_storage
    return view, tenant.Tenant


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "edit"])
async def test_canonical_tenant_form_scaffolds_and_validates_without_storage(mode):
    view, model = tenant_case()

    # Any unexpected storage operation must fail, not become a fake successful read.
    def no_storage(*args, **kwargs):
        raise AssertionError("Canonical Tenant form must not perform storage I/O")

    view.session_maker = no_storage
    rules = view._form_create_rules if mode == "create" else view._form_edit_rules
    form_class = await view.scaffold_form(rules)
    form = form_class(
        FormData(
            {
                "name": "Offline workspace",
                "slug": "offline",
                "plan": "pro",
                "timezone": "Europe/Moscow",
                "is_active": "True",
                "max_sources": "10",
                "daily_cost_limit": "2.75",
                "agent_style": '{"tone":"brief"}',
            }
        )
    )
    assert form.validate(), form.errors
    assert isinstance(form.daily_cost_limit, FloatField)
    assert form.daily_cost_limit.data == 2.75
    assert isinstance(form.daily_cost_limit.data, float)
    assert form.agent_style.data == {"tone": "brief"}
    assert set(form.data) == set(view.form_columns)
    for prop in inspect(model).column_attrs:
        if isinstance(prop.columns[0].type, Float):
            view.form_converter().get_converter(prop)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["not-a-number", ""])
async def test_invalid_or_missing_required_workspace_float_is_not_a_valid_form(value):
    view, _ = tenant_case()
    form = (await view.scaffold_form())(FormData({"daily_cost_limit": value}))
    assert not form.validate()
    assert form.daily_cost_limit.errors


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [Float, FLOAT, REAL, Double, DOUBLE])
async def test_float_families_and_nullable_values_use_float_field(kind):
    base = admin_source()

    class Base(DeclarativeBase):
        pass

    class Row(Base):
        __tablename__ = "numeric_fixture"
        id = Column(Integer, primary_key=True)
        amount = Column(kind, nullable=True)

    class View(base.BaseAdmin, model=Row):
        session_maker = staticmethod(no_storage)
        form_columns = ["amount"]

    form_class = await View().scaffold_form()
    form = form_class(FormData({"amount": "0.125"}))
    assert form.validate(), form.errors
    assert isinstance(form.amount, FloatField)
    assert form.amount.data == 0.125
    blank = form_class(FormData({"amount": ""}))
    assert blank.validate() and blank.amount.data is None


@pytest.mark.asyncio
async def test_exact_decimal_money_does_not_change_to_float():
    base = admin_source()

    class Base(DeclarativeBase):
        pass

    class Row(Base):
        __tablename__ = "decimal_fixture"
        id = Column(Integer, primary_key=True)
        amount = Column(Numeric(18, 9), nullable=False)

    class View(base.BaseAdmin, model=Row):
        session_maker = staticmethod(no_storage)
        form_columns = ["amount"]

    form = (await View().scaffold_form())(FormData({"amount": "0.123456789"}))
    assert form.validate(), form.errors
    assert isinstance(form.amount, DecimalField)
    assert form.amount.data == Decimal("0.123456789")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["create", "edit"])
async def test_actual_related_tenant_get_uses_canonical_form_without_storage(mode):
    from unittest.mock import AsyncMock

    import httpx
    from jinja2 import ChoiceLoader, DictLoader
    from sqladmin.authorization import AuthorizationBackend
    from starlette.applications import Starlette

    related = load_isolated_source("_numeric_related", ROOT / "app/admin/related.py")
    view, tenant = tenant_case()

    class SyntheticPlatformAdmin(AuthorizationBackend):
        async def load(self, request):
            request.state.admin_user = NS(is_platform_admin=True)

        def has_permission(self, *args):
            return True

    app = Starlette()
    admin = related.RelatedAdmin(app, authorization_backend=SyntheticPlatformAdmin())
    admin.add_view(type(view))
    registered = admin._views[0]
    registered.session_maker = no_storage
    obj = tenant(
        id=1,
        name="Offline workspace",
        slug="offline",
        plan="pro",
        timezone="Europe/Moscow",
        is_active=True,
        max_sources=10,
        daily_cost_limit=2.75,
    )
    registered.get_object_for_details = AsyncMock(return_value=obj)
    registered.get_object_for_edit = AsyncMock(return_value=obj)
    registered.create_template = registered.edit_template = "numeric-form.html"
    admin.templates.env.loader = ChoiceLoader(
        [
            DictLoader({"numeric-form.html": "{{ form.daily_cost_limit() }}{{ form.agent_style() }}"}),
            admin.templates.env.loader,
        ]
    )
    path = f"/admin/tenant/{mode}" + ("/1" if mode == "edit" else "")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://offline.test") as client:
        response = await client.get(path, params={"_related_token": "a" * 32})
    assert response.status_code == 200
    assert 'name="daily_cost_limit"' in response.text
    assert 'name="agent_style"' in response.text
