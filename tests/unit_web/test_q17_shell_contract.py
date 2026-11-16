"""Q-17: keep the isolated shell doubles honest against the templates they render.

The surface is read as text and rendered by Jinja with declared filters and
globals only: no application import, no DB, no ASGI startup.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape

from tests.shared.template_shell import (
    SHELL_PERMS_ATTRIBUTES,
    SHELL_USER_ATTRIBUTES,
    shell_perms,
    shell_user,
)

__source_only__ = True

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "app/web/templates"
USER_ATTR = re.compile(r"\buser\.([A-Za-z_][A-Za-z0-9_]*)")
PERMS_ATTR = re.compile(r"\bperms\.([A-Za-z_][A-Za-z0-9_]*)")


def read_shell_templates() -> str:
    """Every template that can render inside the shell."""
    return "\n".join(path.read_text() for path in sorted((WEB / "web").glob("*.html")))


def read_attribute_contract(pattern: re.Pattern[str]) -> set[str]:
    return set(pattern.findall(read_shell_templates()))


def test_every_rendered_user_attribute_is_declared_by_the_shared_double():
    used = read_attribute_contract(USER_ATTR)
    assert used, "the scan found no user attributes; the template glob is wrong"
    assert used <= set(SHELL_USER_ATTRIBUTES), (
        "templates read user attributes missing from tests/shared/template_shell.py: "
        f"{sorted(used - set(SHELL_USER_ATTRIBUTES))}"
    )
    assert all(hasattr(shell_user(), name) for name in used)


def test_every_rendered_perms_attribute_is_declared_by_the_shared_double():
    used = read_attribute_contract(PERMS_ATTR)
    assert used, "the scan found no perms attributes; the template glob is wrong"
    assert used <= set(SHELL_PERMS_ATTRIBUTES), (
        "templates read perms attributes missing from tests/shared/template_shell.py: "
        f"{sorted(used - set(SHELL_PERMS_ATTRIBUTES))}"
    )
    assert all(hasattr(shell_perms(), name) for name in used)


@pytest.mark.parametrize("name", SHELL_PERMS_ATTRIBUTES)
def test_default_perms_double_denies_every_grant(name):
    perms = shell_perms()
    value = getattr(perms, name)
    assert value in (False, None) or (callable(value) and value() is False)


def test_default_user_double_has_no_admin_access():
    assert shell_user().has_admin_access() is False


def environment():
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {"shell-fixture.html": '{% extends "web/base.html" %}{% block page_heading %}Fixture{% endblock %}'}
                ),
                FileSystemLoader(WEB),
            ]
        ),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    env.globals.update(
        url_for=lambda name, path: "/static/" + path,
        static_v=lambda path: "/static/" + path,
        plural=lambda value, one, few, many: many,
    )
    return env


def context(**overrides):
    values = dict(
        user=shell_user(),
        tenant=SimpleNamespace(id=7, name="Fixture workspace"),
        perms=shell_perms(),
        request=SimpleNamespace(query_params={}),
        section="dashboard",
        csrf="fixture-csrf",
        flashes=[],
        nav=[],
        mobile_nav=[],
        workspaces=[],
        member_level=None,
    )
    values.update(overrides)
    return values


def test_default_doubles_render_the_whole_shell_in_strict_mode():
    markup = environment().get_template("shell-fixture.html").render(**context())
    assert "shell-sidebar" in markup
    assert "shell-chat" not in markup
    assert 'href="/admin/"' not in markup


def test_admin_entry_appears_only_when_the_capability_is_granted():
    markup = (
        environment()
        .get_template("shell-fixture.html")
        .render(**context(user=shell_user(has_admin_access=lambda: True)))
    )
    assert 'href="/admin/"' in markup
