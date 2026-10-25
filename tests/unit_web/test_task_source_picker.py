"""Compact inline source picker on the task edit page.

Isolated render checks (no app imports, no DB — same harness as
`test_template_autoescape.py`, run with `--noconftest`): selected sources
render as removable chips, adding goes through a same-style button with a
searchable list, the POST contract stays one `source_ids` value per source,
and hostile source names stay inert data.
"""

from __future__ import annotations

import ast
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[2]
PAYLOADS = [
    '<script id="xss-probe">alert(1)</script>',
    '"><img id="xss-probe" src=x onerror=alert(1)>',
    """Q"uote ' & <b>bold</b> Кириллица ` ${alert(1)} &lt;script&gt;""",
]


class Document(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)

    def elements(self, tag):
        return [attrs for found, attrs in self.tags if found == tag]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.chdir(ROOT)
    tree = ast.parse((ROOT / "app/web/deps.py").read_text())
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_j2_env" for target in node.targets)
    )
    environment = eval(
        compile(ast.Expression(assignment.value), "web Jinja constructor", "eval"),
        {"Environment": Environment, "FileSystemLoader": FileSystemLoader, "select_autoescape": select_autoescape},
    )
    environment.filters["ui_label"] = str
    environment.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    environment.globals["static_v"] = lambda path: f"/static/{path}"
    environment.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}
    return environment


@pytest.fixture
def page_env(env):
    return env.overlay(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": (
                            "<html><head><title>{% block title %}{% endblock %}</title>"
                            "{% block extra_head %}{% endblock %}</head>"
                            "<body>{% block content %}{% endblock %}</body></html>"
                        ),
                    }
                ),
                env.loader,
            ]
        )
    )


def render_edit(page_env, names):
    sources = [
        {
            "id": 7 + i,
            "name": name,
            "external_id": name,
            "tenant_id": 9,
            "platform": {"id": 1, "name": name, "platform_type": {"db_value": "vk"}},
            "source_type": {"db_value": "group", "display_name": name},
            "is_active": True,
            "params": {},
        }
        for i, name in enumerate(names)
    ]
    ctx = {
        "task": {
            "id": 5,
            "name": "task",
            "job_type": "collect",
            "cron_expr": "0 9 * * *",
            "tenant": {"name": "t"},
            "payload": {},
            "agent_scenario_id": None,
            "next_run_at": None,
            "last_run_at": None,
            "last_status": None,
            "created_at": None,
        },
        "sources": sources,
        "scenarios": [],
        "edit": {"id": 5, "name": "task", "source_ids": [7], "cron_expr": "0 9 * * *"},
        "scenario_analysis": {},
        "request": Request(
            {
                "type": "http",
                "scheme": "http",
                "server": ("localhost", 80),
                "path": "/app/tasks/5/edit",
                "query_string": b"",
                "headers": [],
            }
        ),
        "perms": SimpleNamespace(can=lambda *args: True),
        "perms_can": lambda *args: True,
        "csrf": "csrf-fixture",
        "is_superuser": False,
        "filter_tenant_id": None,
        "job_types": [("collect", "Collect")],
        "cron_to_human": lambda value: value,
        "trigger_types": [],
        "action_types": [],
    }
    return page_env.get_template("web/task_edit.html").render(**ctx), sources


@pytest.mark.parametrize("payload", PAYLOADS)
def test_picker_chips_remove_and_searchable_add(page_env, payload):
    """Chips carry × remove controls; adding opens a searchable same-style list."""
    rendered, _ = render_edit(page_env, ["alpha", payload])
    parsed = Document(rendered)
    # No script breakout, no live elements from the hostile name.
    assert not any(attrs.get("id") == "xss-probe" for _, attrs in parsed.tags)
    assert not parsed.elements("img")
    assert payload in "".join(parsed.text)
    # Chip remove control and add button with search field.
    assert "@click=\"removeSource(src.id)\"" in rendered
    assert "Добавить" in rendered
    assert 'aria-label="Поиск источника по названию"' in rendered
    assert 'role="listbox"' in rendered
    # Empty selection is honest: "all active" instead of an empty strip.
    assert "Все активные источники" in rendered
    # The catalog reaches Alpine as data, round-tripping without breakout.
    data = re.search(r"const SOURCES = (\[.*?\]);", rendered, re.S).group(1)
    assert json.loads(data) == [
        {"id": 7, "name": "alpha"},
        {"id": 8, "name": payload},
    ]


def test_post_contract_and_nojs_fallback(page_env):
    """Runtime chips bind hidden `source_ids`; no-JS checkboxes submit the same."""
    rendered, _ = render_edit(page_env, ["alpha", "beta"])
    assert 'name="source_ids" :value="sid"' in rendered
    parsed = Document(rendered)
    fallback = [
        attrs
        for attrs in parsed.elements("input")
        if attrs.get("name") == "source_ids" and attrs.get("type") == "checkbox"
    ]
    assert sorted(attrs["value"] for attrs in fallback) == ["7", "8"]
    assert any(attrs.get("value") == "7" and "checked" in attrs for attrs in fallback)
    assert not any(attrs.get("value") == "8" and "checked" in attrs for attrs in fallback)
