"""P11-T27 S7: SQLAdmin domain menu categories and ADMIN.md contract alignment."""

from __future__ import annotations

__source_only__ = True

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from sqladmin.authorization import Action
from test_admin_model_permission_policy import TYPES, user_with

from app.admin.setup import setup_admin

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CATEGORIES = {
    "Система": ["Реестр таблиц"],
    "Пользователи и доступ": ["Пользователи", "Роли", "Разрешения", "Рабочие пространства", "Личные креды"],
    "Источники и сбор": ["Платформы", "Источники", "Собранный контент"],
    "ИИ и аналитика": ["Сценарии", "ИИ-аналитика", "Провайдеры ИИ", "Модели ИИ"],
    "Задачи и операции": ["Задачи", "Действия ботов", "Очередь выполнения", "Уведомления"],
}


def _build_console():
    app = FastAPI()
    return setup_admin(app)


def test_all_17_console_views_are_grouped_into_domain_categories():
    admin = _build_console()
    assert len(admin._views) == 17
    for view in admin._views:
        assert getattr(view, "category", None), f"{type(view).__name__} is missing category"
        assert getattr(view, "category_icon", None), f"{type(view).__name__} is missing category_icon"

    grouped = {item.name: [child.display_name for child in item.children] for item in admin._menu.items}
    assert all(item.type_ == "Category" for item in admin._menu.items)
    assert grouped == EXPECTED_CATEGORIES


import pytest


@pytest.mark.asyncio
async def test_category_visibility_follows_accessible_child_views():
    admin = _build_console()
    categories = {item.name: item for item in admin._menu.items}

    owner = user_with(role="superuser")
    owner.has_perm_for = lambda model, action: True
    owner_req = SimpleNamespace(
        state=SimpleNamespace(admin_user=owner),
        app=SimpleNamespace(state=SimpleNamespace(admin=admin)),
        session={},
    )
    await admin.authorization_backend.load(owner_req)
    assert [
        name for name, cat in categories.items() if cat.is_visible(owner_req) and cat.is_accessible(owner_req)
    ] == list(EXPECTED_CATEGORIES)

    # An operator granted only `collection.source` VIEW sees only "Источники и сбор" -> "Источники".
    source_only = user_with(TYPES.ActionType.VIEW, role="operator", resource="collection.source")
    source_req = SimpleNamespace(
        state=SimpleNamespace(admin_user=source_only),
        app=SimpleNamespace(state=SimpleNamespace(admin=admin)),
        session={},
    )
    await admin.authorization_backend.load(source_req)
    visible_categories = [
        name for name, cat in categories.items() if cat.is_visible(source_req) and cat.is_accessible(source_req)
    ]
    assert visible_categories == ["Источники и сбор"]
    visible_children = [
        child.display_name
        for child in categories["Источники и сбор"].children
        if child.is_visible(source_req) and child.is_accessible(source_req)
    ]
    assert visible_children == ["Источники"]


def test_admin_docs_describe_resulting_s1_through_s7_contract():
    admin_doc = (ROOT / "docs/ADMIN.md").read_text()
    for category_name in EXPECTED_CATEGORIES:
        assert category_name in admin_doc
    for required_term in (
        "AdminCSRFMiddleware",
        "VERIFY_PASSWORD_RATE_LIMIT",
        "app/admin/secrets.py",
        "app/admin/identity.py",
        "sqladmin/action_confirm.html",
        "row_criteria_scope",
        "_ScopedCategoryMenu",
    ):
        assert required_term in admin_doc, f"Missing {required_term!r} in docs/ADMIN.md"


@pytest.mark.asyncio
async def test_rendered_sidebar_menu_contains_collapsible_category_submenus():
    admin = _build_console()
    owner = user_with(role="superuser")
    owner.has_perm_for = lambda model, action: True
    owner_req = SimpleNamespace(
        state=SimpleNamespace(admin_user=owner),
        app=SimpleNamespace(state=SimpleNamespace(admin=admin)),
        session={},
        path_params={"identity": "user"},
        url=SimpleNamespace(path="/admin/user/list"),
        url_for=lambda name, **kw: f"/admin/{name}/{kw.get('identity', '')}",
    )
    await admin.authorization_backend.load(owner_req)
    mod = await admin.templates.env.get_template("sqladmin/_macros.html").make_module_async()
    html = await mod.display_menu(admin._menu, owner_req)

    for category_name, children in EXPECTED_CATEGORIES.items():
        assert f'data-sqladmin-menu-category="{category_name}"' in html
        for child_title in children:
            assert child_title in html

    custom_css = (ROOT / "app/static/css/custom.css").read_text()
    assert ".admin-menu .dropdown-menu" in custom_css
    assert "position: static !important;" in custom_css
