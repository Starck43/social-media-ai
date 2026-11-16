"""Offline settings catalog and SQLAdmin bulk-delete fixtures.

Every page is rendered from the checked-in templates, the installed Tabler
assets and the real stylesheets; no application import, database or provider
call happens here.

Usage:

    python tests/settings_admin_fixtures.py --out <out-dir> --vendor <offline-vendor-dir>
"""

from __future__ import annotations

import argparse
import json
import runpy
from importlib.metadata import distribution
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = distribution("sqladmin").locate_file("sqladmin")
VENDOR = (
    ("https://cdn.tailwindcss.com", "tailwind.js"),
    ("https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js", "alpine.js"),
    ("https://unpkg.com/htmx.org@1.9.12", "htmx.js"),
)


def _static_uri(path: Path) -> str:
    return path.as_uri()


def settings_pages(out: Path, fixture: dict, vendor: Path) -> None:
    for mode in ("platforms", "empty", "no-workspace"):
        context = fixture["context"]("no-workspace" if mode == "no-workspace" else "owner-workspace")
        context["user"].is_platform_admin = True
        context["user"].has_admin_access = lambda: True
        visible = ["account", "platforms"] if mode == "no-workspace" else [*context["visible_tabs"], "platforms"]
        context.update(
            tab="platforms",
            visible_tabs=list(dict.fromkeys(visible)),
            platforms=(
                []
                if mode == "empty"
                else [
                    NS(
                        id=7,
                        name="ВКонтакте",
                        base_url="https://api.example.invalid/v1",
                        platform_type=NS(label="ВКонтакте"),
                        is_active=True,
                    ),
                    NS(
                        id=8,
                        name="ОченьДлинноеНазваниеБезПробелов" * 3,
                        base_url="https://example.invalid/" + "long" * 40,
                        platform_type=NS(label="Telegram"),
                        is_active=False,
                    ),
                ]
            ),
            platform_type_options=[("vk", "ВКонтакте", ""), ("telegram", "Telegram", "")],
        )
        html = fixture["environment"]().get_template("web/settings.html").render(**context)
        html = html.replace("http://fixture.test/static/", _static_uri(ROOT / "app/static") + "/")
        for source, name in VENDOR:
            html = html.replace(source, _static_uri(vendor / name))
        (out / f"settings-{mode}.html").write_text(html)


def list_row_cells(source: str) -> str:
    """Return the checked-in list row cells (selection and record actions) verbatim."""
    hidden = '<input type="hidden" value="{{ get_object_identifier(row) }}">'
    columns = "{% for name in model_view._list_prop_names %}"
    if hidden not in source:
        raise SystemExit("SQLAdmin list row markup moved; update the fixture slice")
    start = source.rindex("<td>", 0, source.index(hidden))
    cells = source[start : source.index(columns, start)].rstrip()
    checkbox = 'class="form-check-input m-0 align-middle select-box" type="checkbox"'
    if checkbox not in cells:
        raise SystemExit("SQLAdmin row selection checkbox moved; update the fixture slice")
    # The dialog receives the IDs of checked rows only, so the fixture renders
    # the page with the list's own select-all state applied.
    return cells.replace(checkbox, checkbox + " checked", 1)


def admin_page(out: Path, vendor: Path) -> None:
    environment = Environment(
        loader=ChoiceLoader([FileSystemLoader(ROOT / "app/templates"), FileSystemLoader(PACKAGE / "templates")]),
        autoescape=select_autoescape(),
    )

    def url(name: str, **values) -> str:
        if name == "static":
            return _static_uri(ROOT / "app/static" / values["path"])
        if name == "admin:statics":
            return _static_uri(PACKAGE / "statics" / values["path"])
        return "/admin/" + values.get("identity", "") + "/" + name.split(":")[-1]

    environment.globals.update(url_for=url, _=lambda text: text, csrf_token=lambda: "fixture-csrf")
    list_source = (ROOT / "app/templates/sqladmin/list.html").read_text()
    start = "                  <button {% if not can_delete_any"
    end = "                    {% for custom_action, label in list_actions.items() %}"
    if start not in list_source or end not in list_source:
        raise SystemExit("SQLAdmin list action markup moved; update the fixture slice")
    actions = list_source[list_source.index(start) : list_source.index(end)]
    actions += "</div>{% endif %}"
    rows = list_row_cells(list_source)
    child = (
        """{% extends 'sqladmin/layout.html' %}
    {% block content %}
    <section class="card"><div class="card-body"><h2>Действия с записями</h2>
    <div class="dropdown">"""
        + actions
        + """</div></div>
    <div class="d-flex flex-wrap gap-2 mt-4">
    {% for kind in ['default','primary','secondary','light','outline-secondary','danger','success','info','warning'] %}
    <button class="btn {{ 'btn-' + kind if kind != 'default' else '' }}" data-test-kind="{{ kind }}">{{ kind }}</button>
    {% endfor %}</div>
    <div class="d-flex flex-wrap gap-2 mt-3">
    {% for context in ['input-group', 'form-group', 'card-footer'] %}
    {% for kind in ['primary', 'secondary', 'light', 'danger'] %}
    <div class="{{ context }}">
    {% if context == 'form-group' %}
    <input type="submit" class="btn btn-{{ kind }}" data-test-kind="{{ context }}-{{ kind }}" value="{{ kind }}">
    {% else %}
    <button class="btn btn-{{ kind }}" data-test-kind="{{ context }}-{{ kind }}">{{ kind }}</button>
    {% endif %}
    </div>
    {% endfor %}{% endfor %}</div>
    <div class="table-responsive">
    <table class="table card-table table-vcenter text-nowrap">
    <thead><tr>
    <th class="w-1"><input class="form-check-input m-0 align-middle" type="checkbox" aria-label="Выбрать все" id="select-all"></th>
    <th class="w-1"></th>
    </tr></thead>
    <tbody>
    {% for row in pagination.rows %}
    <tr>
    """
        + rows
        + """
    </tr>
    {% endfor %}
    </tbody></table></div>
    </div></section>{% include 'sqladmin/modals/delete.html' %}{% endblock %}"""
    )
    user = NS(
        username="Admin",
        role=NS(label="Администратор"),
        is_active=True,
        is_platform_admin=True,
        has_admin_access=lambda: True,
    )
    html = environment.from_string(child).render(
        request=NS(state=NS(admin_user=user), session={}, url_for=url, url=NS(query="")),
        admin=NS(
            title="Администрирование",
            logo_url=None,
            favicon_url=None,
            _menu=NS(items=[]),
            authentication_backend=True,
            base_url="/admin",
        ),
        title="Пользователи",
        subtitle="",
        can_delete_any=True,
        list_actions={},
        pagination=NS(rows=[NS(id=row_id) for row_id in range(1276, 1076, -1)]),
        get_object_identifier=lambda row: row.id,
        model_view=NS(
            name="Пользователь",
            identity="user",
            check_can_view_details=lambda *arguments: True,
            check_can_edit=lambda *arguments: True,
            check_can_delete=lambda *arguments: True,
            _build_url_for=lambda name, request, row: f"/admin/{name.split(':')[-1]}",
            _url_for_delete=lambda request, row: "/admin/user/delete",
        ),
    )
    for source, name in VENDOR:
        html = html.replace(source, _static_uri(vendor / name))
    (out / "admin.html").write_text(html)


def render(out: Path, vendor: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    settings_pages(out, runpy.run_path(str(ROOT / "tests/shared/settings_layout_fixtures.py")), vendor)
    admin_page(out, vendor)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--vendor", required=True, type=Path, help="Directory with tailwind.js, alpine.js and htmx.js")
    arguments = parser.parse_args()
    render(arguments.out, arguments.vendor)
    print(json.dumps({"states": ["platforms", "empty", "no-workspace", "admin"]}))
