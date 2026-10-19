"""No-app/no-DB regression checks for the exact web Jinja configuration.

Run isolated with --noconftest under the remote no-DB skill exception: the
suite's root conftest performs shared-schema bootstrap. These checks intentionally import no application code.
Owner acceptance must also render with the real app environment and check Alpine.
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
WEB = ROOT / "app/web/templates/web"
PAYLOADS = [
    '<script id="xss-probe">alert(1)</script>',
    '"><img id="xss-probe" src=x onerror=alert(1)>',
    '</textarea><script id="xss-probe">alert(1)</script>',
    """Q"uote ' & <b>bold</b> Кириллица ` ${alert(1)} &lt;script&gt;""",
    """');window.xssProbe=1;//""",
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
    # Evaluate only the actual _j2_env constructor, never app imports/engines.
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
    # Match the production tojson policy while retaining all HTML-safe escaping.
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


def context(name):
    source = {
        "id": 7,
        "name": name,
        "external_id": name,
        "tenant_id": 9,
        "platform": {"id": 1, "name": name, "platform_type": {"db_value": "vk"}},
        "source_type": {"db_value": "group", "display_name": name},
        "is_active": True,
        "params": {"monitored_users": [name]},
    }
    task = {
        "id": 5,
        "name": name,
        "job_type": "collect",
        "cron_expr": "0 9 * * *",
        "tenant": {"name": name},
        "payload": {"monitored_users": [name]},
        "agent_scenario_id": 3,
        "next_run_at": None,
        "last_run_at": None,
        "last_status": None,
        "created_at": None,
    }
    chain = {
        "name": name,
        "chain_id": "fixture-chain",
        "url": "/app/analytics/chains/fixture-chain",
        "total_analyses": 1,
        "date_range": {"start": None, "end": None},
        "evolution": [
            {
                "id": 8,
                "date": name,
                "analysis_summary": name,
                "topics": [],
                "source_info": {"source_id": 7},
            }
        ],
        "avg_sentiment": None,
    }
    return {
        "task": task,
        "source": source,
        "sources": [source],
        "scenario": {"id": 3, "name": name},
        "scenarios": [{"id": 3, "name": name}],
        "edit": {"id": 5, "name": name, "source_ids": [7], "cron_expr": "0 9 * * *"},
        "scenario_analysis": {},
        "chain": chain,
        "chains": [chain],
        "source_names": {},
        "back_url": "/app/analytics",
        "chains_url": "/app/analytics/chains",
        "back_label": name,
        "request": Request(
            {
                "type": "http",
                "scheme": "http",
                "server": ("localhost", 80),
                "path": "/app/analytics/chains",
                "query_string": b"",
                "headers": [],
            }
        ),
        "chain_filters": {},
        "sorts": [],
        "filter_return_to": "/app/analytics",
        "perms": SimpleNamespace(can=lambda *args: True, can_manage_workspace=lambda *args: True),
        "perms_can": lambda *args: True,
        "csrf": "csrf-fixture",
        "is_superuser": False,
        "filter_tenant_id": None,
        "effective_active": {5},
        "linked_source_ids": {7},
        "recent_jobs": [],
        "latest_run": {
            "job": None,
            "summary": None,
            "sources": [],
            "can_view_sources": False,
            "run_scope": None,
            "legacy_status_label": "результат не подтверждён",
        },
        "running_job_id": None,
        "running_job_status": None,
        "job_outcomes": {},
        "JOB_TYPE_TITLES": {"collect": "Collect"},
        "job_types": [("collect", "Collect")],
        "cron_to_human": lambda value: value,
        "trigger_types": [],
        "action_types": [],
        "running": {"collect": None, "analyze": None},
        "mode": {"key": "pull", "label": name, "hint": name, "is_push": False},
        "modes": (("pull", name), ("push", name), ("user", name)),
        "mode_labels": {"pull": name, "push": name, "user": name},
        "members": [],
        "token_owner": None,
        "orphaned_owner_id": None,
        "owner_label": name,
        "owner_connection": None,
        "analytics_count": 0,
        "recent_count": 0,
        "usage_count": 0,
        "can_delete": True,
        "can_update": True,
        "can_view_tasks": True,
        "cost_usd": 0,
        "last_checked_label": "—",
        "is_ready": True,
        "readiness_hint": "",
        "monitored_users": [name],
        "platforms": [source["platform"]],
        "source_types": [source["source_type"]],
        "pending_count": 0,
        "given_up": 0,
        "schedules": [],
        "collections": [],
        "recent": [],
        "latest_progress": None,
        "latest_diagnostics": None,
    }


def assert_inert(document):
    assert not any(attrs.get("id") == "xss-probe" for _, attrs in document.tags)
    assert not document.elements("img")
    assert not document.elements("b")
    assert all("onerror" not in attrs for _, attrs in document.tags)
    assert all(
        "xssProbe" not in value
        for _, attrs in document.tags
        for key, value in attrs.items()
        if (key.startswith("@") or key.startswith("x-on:") or key.startswith("on")) and value
    )


@pytest.mark.parametrize("name", ["web/page.html", "web/page.htm", "web/page.xml", "web/page.HTML", None])
def test_html_xml_and_string_templates_autoescape(env, name):
    assert env.autoescape(name)


def test_plain_text_template_policy_is_unchanged(env):
    assert not env.autoescape("mail.txt")


@pytest.mark.parametrize("payload", PAYLOADS)
def test_default_text_attribute_and_textarea_escaping(env, payload):
    rendered = env.from_string(
        '<h1>{{ value }}</h1><input value="{{ value }}"><textarea>{{ value }}</textarea>'
    ).render(value=payload)
    parsed = Document(rendered)
    assert_inert(parsed)
    assert len(parsed.elements("input")) == len(parsed.elements("textarea")) == 1
    assert parsed.elements("input")[0]["value"] == payload
    assert "".join(parsed.text) == payload * 2


@pytest.mark.parametrize("payload", PAYLOADS)
def test_source_name_and_edit_value_are_inert_on_source_detail(page_env, payload):
    rendered = page_env.get_template("web/source_detail.html").render(**context(payload))
    parsed = Document(rendered)
    assert_inert(parsed)
    assert not parsed.elements("script")
    assert payload in "".join(parsed.text)
    assert next(attrs["value"] for attrs in parsed.elements("input") if attrs.get("name") == "name") == payload
    assert any(attrs.get("action") == "/app/sources/7" for _, attrs in parsed.tags)
    assert "Настройки источника" in "".join(parsed.text)
    assert not any(attrs.get("action", "").endswith("/delete-unprocessed") for _, attrs in parsed.tags)
    assert any(
        attrs.get("name") == "_csrf" and attrs.get("value") == "csrf-fixture" for attrs in parsed.elements("input")
    )


@pytest.mark.parametrize("payload", PAYLOADS)
def test_task_detail_links_to_standalone_editor_and_keeps_names_inert(page_env, payload):
    rendered = page_env.get_template("web/task_detail.html").render(**context(payload))
    parsed = Document(rendered)
    assert_inert(parsed)
    assert payload in "".join(parsed.text)
    assert any(attrs.get("href") == "/app/tasks/5/edit" for attrs in parsed.elements("a"))
    assert not any(attrs.get("name") == "name" for attrs in parsed.elements("input"))
    assert not any(attrs.get("action") == "/app/tasks/5/edit" for attrs in parsed.elements("form"))
    messages = [attrs["data-confirm"] for _, attrs in parsed.tags if "data-confirm" in attrs]
    assert messages and any(payload in message for message in messages)
    assert any(
        attrs.get("name") == "_csrf" and attrs.get("value") == "csrf-fixture" for attrs in parsed.elements("input")
    )


@pytest.mark.parametrize("template", ["analytics_chains.html", "analytics_chain_detail.html"])
@pytest.mark.parametrize("payload", PAYLOADS)
def test_chain_names_are_data_not_alpine_code(page_env, template, payload):
    rendered = page_env.get_template("web/" + template).render(**context(payload))
    parsed = Document(rendered)
    assert_inert(parsed)
    assert not parsed.elements("script")
    assert payload in "".join(parsed.text)
    assert any(
        attrs.get("data-confirm") == f"Удалить цепочку «{payload}» и все её анализы?" for _, attrs in parsed.tags
    )
    assert "confirm(" not in (WEB / template).read_text()
    forms = parsed.elements("form")
    assert any(
        attrs.get("action") == "/app/analytics/chains/fixture-chain/delete" and attrs.get("method") == "post"
        for attrs in forms
    )
    assert any(
        attrs.get("name") == "_csrf" and attrs.get("value") == "csrf-fixture" for attrs in parsed.elements("input")
    )


@pytest.mark.parametrize("payload", PAYLOADS)
def test_task_editor_json_roundtrips_without_script_breakout(page_env, payload):
    ctx = context(payload)
    ctx.update(is_superuser=True, filter_tenant_id=9)
    rendered = page_env.get_template("web/task_edit.html").render(**ctx)
    parsed = Document(rendered)
    assert_inert(parsed)
    scripts = parsed.elements("script")
    inline_scripts = [script for script in scripts if "src" not in script]
    external_scripts = [script.get("src") for script in scripts if "src" in script]
    assert len(inline_scripts) == 1
    assert external_scripts == ["/static/js/task-schedule.js"]
    data = re.search(r"const TASK_EDIT = (.*);", rendered).group(1)
    assert json.loads(data) == ctx["edit"]
    inputs = parsed.elements("input")
    assert any(attrs.get("x-model") == "editing.is_active" for attrs in inputs)
    assert not any(attrs.get("x-model") in {"editing.force_refresh", "editing.force_reanalyze"} for attrs in inputs)
    # Alpine renders one hidden `source_ids` input per selected id at runtime
    # (x-for over `editing.source_ids`); statically the template carries the
    # loop template plus the no-JS checkbox fallback with the same values.
    assert 'name="source_ids" :value="sid"' in rendered
    assert any(attrs.get("name") == "source_ids" and attrs.get("value") == "7" for attrs in inputs)
    assert "const SOURCES = " in rendered
    assert 'x-text="src.name"' in rendered
    assert any(attrs.get("name") == "tenant_id" and attrs.get("value") == "9" for attrs in inputs)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_chat_json_keeps_cyrillic_and_html_safe_serialization(page_env, payload):
    messages = [{"role": "user", "content": payload}]
    rendered = page_env.get_template("web/chat.html").render(
        messages=messages,
        csrf="fixture",
        is_owner=True,
        static_v=lambda path: "/static/" + path,
    )
    data = re.search(r"window.__chatMessages = (.*);", rendered).group(1)
    assert json.loads(data) == messages
    assert "<" not in data and "&" not in data and "'" not in data
    if "Кириллица" in payload:
        assert "Кириллица" in data


@pytest.mark.parametrize("payload", PAYLOADS)
def test_macros_escape_plain_content_and_attribute_values(env, payload):
    macros = env.get_template("web/_macros.html").module
    markup = str(macros.check_card("id", "field", payload, payload, attrs={"data-label": payload}))
    parsed = Document(markup)
    assert_inert(parsed)
    assert payload in "".join(parsed.text)
    assert parsed.elements("input")[0]["value"] == payload
    assert parsed.elements("input")[0]["data-label"] == payload
    toggle = Document(str(macros.switch_toggle("enabled", payload, attrs={"x-model": "editing.enabled"})))
    assert toggle.elements("input")[0]["x-model"] == "editing.enabled"
    assert payload in "".join(toggle.text)
    assert_inert(toggle)


def test_captured_html_and_macro_svg_survive_without_trusting_plain_values(env):
    markup = env.from_string(
        '{% from "web/_macros.html" import check_card, icon %}'
        '{% set label %}<a href="/app/sources/7">{{ name }}</a>{% endset %}'
        '{{ check_card("source-7", "source_ids", 7, label) }}'
        '{{ check_card("kind", "kind", "x", icon("topics") ~ " " ~ name) }}'
    ).render(name=PAYLOADS[0])
    parsed = Document(markup)
    assert_inert(parsed)
    assert parsed.elements("a")[0]["href"] == "/app/sources/7"
    assert len(parsed.elements("svg")) == 3  # two check marks plus the icon
    assert PAYLOADS[0] in "".join(parsed.text)


def test_all_templates_compile_and_raw_attribute_fragments_are_gone(env):
    for path in WEB.glob("*.html"):
        env.get_template("web/" + path.name)
        text = path.read_text()
        assert "attrs='" not in text and "{{ attrs }}" not in text, path
        assert "{% autoescape false" not in text, path
    assert "{{ attrs|xmlattr }}" in (WEB / "_macros.html").read_text()


def test_only_server_generated_invite_qr_retains_explicit_safe_html(page_env):
    safe_lines = [
        (path.name, line.strip())
        for path in WEB.glob("*.html")
        for line in path.read_text().splitlines()
        if "|safe" in line
    ]
    assert len(safe_lines) == 1 and "issued_invite.qr_svg|safe" in safe_lines[0][1]
    qr = '<svg data-qr="fixture"><path d="M0 0h1v1H0z"/></svg>'
    rendered = page_env.get_template("web/_invite_dialog.html").render(
        **context(PAYLOADS[0]),
        tab="team",
        visible_tabs=["team"],
        settings_manage=True,
        settings_team_visible=True,
        tenant={"id": 9},
        issued_invite={
            "qr_svg": qr,
            "link": "/app/invite/code",
            "code": "fixture",
            "role": PAYLOADS[0],
            "expires": "fixture",
        },
        invite_roles=[],
        invite_days=[7],
        memberships=[],
        member_names={},
        role_labels={},
    )
    parsed = Document(rendered)
    assert_inert(parsed)
    assert any(attrs.get("data-qr") == "fixture" for attrs in parsed.elements("svg"))
    assert PAYLOADS[0] in "".join(parsed.text)
