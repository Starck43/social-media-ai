"""The recovery contract of the operator console (P10-T01).

Acceptance says recovery must be account-bound and authenticated: either an
expiring one-use self-service flow or the approved operator-only reset. This
project chose the operator reset, so the *absence* of an anonymous password
route is part of the contract. These checks are static on purpose — the router
table and the templates are the whole surface an anonymous caller can reach, and
no database, session or SQLAdmin mount is needed to read them.

The operator path itself is pinned elsewhere: `test_admin_owner_guards.py` proves
the console refuses the owner account on both password pages without writing, and
the manager/permission suites pin the `user.update` gate.
"""

from __future__ import annotations

from pathlib import Path

from app.admin import endpoints

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "app" / "templates" / "sqladmin"


def _routes() -> list:
    return list(endpoints.router.routes)


def test_the_anonymous_password_reset_routes_are_gone():
    """No route answers `/admin/reset-password`, by path or by handler name."""

    paths = {getattr(route, "path", "") for route in _routes()}
    names = {getattr(route, "name", "") for route in _routes()}

    assert "/admin/reset-password" not in paths
    assert "reset_password" not in names
    assert "reset_password_page" not in names


def test_the_operator_password_routes_still_exist():
    """Removing the anonymous form must not remove the accepted recovery path."""

    paths = {getattr(route, "path", "") for route in _routes()}

    assert "/admin/user/change-password/{user_id}" in paths
    assert "/admin/verify-current-password" in paths


def test_the_admin_login_page_does_not_offer_password_recovery():
    """The login page must not advertise a recovery flow that does not exist."""

    login = (TEMPLATES / "login.html").read_text(encoding="utf-8")

    assert "reset-password" not in login
    assert "Забыли пароль" not in login


def test_the_legacy_reset_template_is_removed():
    """A template nothing renders is a form waiting for a future route."""

    assert not (TEMPLATES / "user" / "reset_password.html").exists()
