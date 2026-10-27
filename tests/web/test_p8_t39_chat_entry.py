"""Primary chat entry and permission-filtered management links for P8-T39."""

from __future__ import annotations

from pathlib import Path

from jinja2 import ChoiceLoader, DictLoader, Environment

from app.web.deps import templates
from app.web.nav import NAV_ITEMS


def test_chat_entry_explains_task_inputs_and_keeps_direct_management_links():
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": "<html><head>{% block extra_head %}{% endblock %}</head>"
                        "<body>{% block content %}{% endblock %}</body></html>"
                    }
                ),
                templates.env.loader,
            ]
        )
    )
    permitted = tuple(item for item in NAV_ITEMS if item.key in {"sources", "scenarios", "tasks", "digests"})
    html = env.get_template("web/chat.html").render(
        messages=[],
        csrf="fixture",
        is_owner=True,
        nav=permitted,
        url_for=lambda name, **kwargs: "/static/" + kwargs["path"],
        static_v=lambda path: "/static/" + path,
    )

    for href in ("/app/sources", "/app/scenarios", "/app/tasks", "/app/digests"):
        assert f'href="{href}"' in html
    assert 'aria-label="Управление данными и задачами"' in html
    for group in (
        "цель",
        "данные и источники",
        "способ обработки",
        "нужный результат",
        "период или расписание",
        "ограничения",
    ):
        assert group in html
    assert "расписание и отправка дайджеста требуют подтверждения" in html

    starter_script = Path("app/static/js/chat.js").read_text()
    assert "Разовый сбор и анализ" in starter_script
    assert "Новое расписание" in starter_script
    assert "Статус и результаты" in starter_script
    assert "Сначала уточни источники, период и ограничения" in starter_script


def test_chat_management_links_only_render_sections_in_permission_filtered_navigation():
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": "<html><head>{% block extra_head %}{% endblock %}</head>"
                        "<body>{% block content %}{% endblock %}</body></html>"
                    }
                ),
                templates.env.loader,
            ]
        )
    )
    # The shell supplies this already filtered navigation from the active
    # workspace membership. A denied section therefore cannot be linked here.
    allowed = tuple(item for item in NAV_ITEMS if item.key in {"sources", "tasks"})
    html = env.get_template("web/chat.html").render(
        messages=[],
        csrf="fixture",
        is_owner=False,
        member_level=type("Level", (), {"label": "reader", "hint": "read only"})(),
        nav=allowed,
        url_for=lambda name, **kwargs: "/static/" + kwargs["path"],
        static_v=lambda path: "/static/" + path,
    )
    assert 'href="/app/sources"' in html
    assert 'href="/app/tasks"' in html
    assert 'href="/app/scenarios"' not in html
    assert 'href="/app/digests"' not in html
