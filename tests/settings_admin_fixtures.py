"""Offline fixtures use actual settings and SQLAdmin shell/modals; no application import."""

import json
import runpy
import sys
from importlib.metadata import distribution
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = distribution("sqladmin").locate_file("sqladmin")


def render(out):
    out.mkdir(parents=True, exist_ok=True)
    fixture = runpy.run_path(str(ROOT / "tests/settings_layout_fixtures.py"))
    for mode in ("platforms", "empty", "no-workspace"):
        c = fixture["context"]("no-workspace" if mode == "no-workspace" else "owner-workspace")
        c["user"].is_platform_admin = True
        c["user"].has_admin_access = lambda: True
        c.update(
            tab="platforms",
            visible_tabs=["account", "platforms"] if mode == "no-workspace" else [*c["visible_tabs"], "platforms"],
            platforms=(
                []
                if mode == "empty"
                else [
                    NS(
                        id=7,
                        name="ВКонтакте",
                        base_url="https://api.example.invalid/v1/",
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
        html = fixture["environment"]().get_template("web/settings.html").render(**c)
        html = html.replace("http://fixture.test/static/", (ROOT / "app/static").as_uri() + "/")
        for source, name in [
            ("https://cdn.tailwindcss.com", "tailwind.js"),
            ("https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js", "alpine.js"),
            ("https://unpkg.com/htmx.org@1.9.12", "htmx.js"),
        ]:
            html = html.replace(source, (Path(sys.argv[2]) / name).as_uri())
        (out / f"settings-{mode}.html").write_text(html)
    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(ROOT / "app/templates"), FileSystemLoader(PACKAGE / "templates")]),
        autoescape=select_autoescape(),
    )

    def url(name, **kw):
        if name == "static":
            return (ROOT / "app/static" / kw["path"]).as_uri()
        if name == "admin:statics":
            return (PACKAGE / "statics" / kw["path"]).as_uri()
        return "/admin/" + kw.get("identity", "") + "/" + name.split(":")[-1]

    env.globals.update(url_for=url, _=lambda s: s, csrf_token=lambda: "fixture-csrf")
    # Copy the real list action markup, not a reimplementation of its selectors.
    source = (ROOT / "app/templates/sqladmin/list.html").read_text()
    actions = source[
        source.index("                  <button {% if not can_delete_any") : source.index(
            "                    {% for custom_action, label in list_actions.items() %}"
        )
    ]
    actions += "</div>{% endif %}"
    child = (
        """{% extends 'sqladmin/layout.html' %}{% block content %}
    <section class="card"><div class="card-body"><h2>Действия с записями</h2>
    <div class="dropdown">"""
        + actions
        + """</div></div>
    <div class="d-flex flex-wrap gap-2 mt-4">
    {% for kind in ['default','primary','secondary','light','outline-secondary','danger','success','info','warning'] %}
    <button class="btn {{ 'btn-' + kind if kind != 'default' else '' }}" data-test-kind="{{ kind }}">{{ kind }}</button>
    {% endfor %}</div>
    {% for i in range(200) %}<label class="d-none"><input type="checkbox" class="select-box" checked><input type="hidden" value="{{ 1276-i }}"></label>{% endfor %}
    </div></section>{% include 'sqladmin/modals/delete.html' %}{% endblock %}"""
    )
    user = NS(
        username="Admin",
        role=NS(label="Администратор"),
        is_active=True,
        is_platform_admin=True,
        has_admin_access=lambda: True,
    )
    (out / "admin.html").write_text(
        env.from_string(child).render(
            request=NS(state=NS(admin_user=user), session={}, url_for=url),
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
            model_view=NS(name="Пользователь", identity="user"),
        )
    )


if __name__ == "__main__":
    render(Path(sys.argv[1]))
    print(json.dumps(dict(states=["platforms", "empty", "no-workspace", "admin"])))
