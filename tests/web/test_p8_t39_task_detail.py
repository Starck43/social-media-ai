"""Permission-gated chat entry and canonical task-plan summaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app/web/templates"
__source_only__ = True


def _environment() -> Environment:
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": (
                            "<html><body>{% block page_heading %}{% endblock %}"
                            "{% block content %}{% endblock %}</body></html>"
                        )
                    }
                ),
                FileSystemLoader(TEMPLATES),
            ]
        ),
        autoescape=select_autoescape(("html", "htm", "xml"), default_for_string=True),
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    env.globals["static_v"] = lambda path: "/static/" + path
    env.globals["url_for"] = lambda name, path: "/static/" + path
    return env


def _dashboard(can_open_chat: bool) -> str:
    offer = {
        "sources": False,
        "add_source": False,
        "add_scenario": False,
        "tasks": False,
        "add_task": False,
        "digests": False,
        "analytics": False,
        "collected_items": False,
        "jobs": False,
    }
    request = SimpleNamespace(
        url=SimpleNamespace(path="/app/", query=""),
        query_params={},
    )
    return (
        _environment()
        .get_template("web/dashboard.html")
        .render(
            section="dashboard",
            can_open_chat=can_open_chat,
            is_superuser=False,
            tenants=[],
            filter_tenant_id=None,
            next_step=None,
            offer=offer,
            recent_results=[],
            recent_tasks=[],
            source_schedule_status_unknown=False,
            pipeline=None,
            _tz="Europe/Kirov",
            request=request,
            perms=SimpleNamespace(can_manage_workspace=lambda target: False, is_owner=False, workspace_role="VIEWER"),
            member_level=None,
        )
    )


def test_overview_foregrounds_chat_only_when_permission_allows_it():
    allowed = _dashboard(True)
    denied = _dashboard(False)

    assert "data-home-chat-primary" in allowed
    assert 'href="/app/chat"' in allowed
    assert allowed.count('href="/app/chat"') == 1
    assert "Начните с разговора" in allowed
    assert "Начните с разговора" not in denied
    assert 'href="/app/chat"' not in denied
    assert "data-home-chat" not in denied
    assert "Обзор" in allowed
    assert "data-home-results" in allowed


def _render_plan(task_definition: dict, *, task=None, scenario=None) -> str:
    return (
        _environment()
        .get_template("web/_task_plan_summary.html")
        .render(
            task=task or SimpleNamespace(name="Research", job_type="analyze", cron_expr="@once"),
            task_definition=task_definition,
            scenario=scenario,
            sources=[SimpleNamespace(id=9, name="Kirov public group")],
            collection={
                "collection_content_types": ["posts"],
                "collection_engagement_types": [],
                "collection_limit": 20,
                "collection_queries": "",
                "collection_discovery_queries": "",
                "collection_city": "",
                "collection_people": False,
                "collection_communities": False,
                "collection_comments_max_per_post": 0,
                "collection_comments_total": 0,
                "collection_candidates_limit": 0,
                "collection_candidates_confirm": "manual",
                "start_date": "",
                "end_date": "",
            },
            cron_to_human=lambda cron: "каждый день в 9:00" if cron != "@once" else "разово",
        )
    )


def test_canonical_plan_summary_renders_six_groups_without_raw_connection_fields():
    plan = {
        "goal": "Find local public updates <script>",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [9],
        "collection": {
            "limit": 20,
            "queries": ["renovation update"],
            "discovery": {"queries": ["contractor discovery"]},
            "comments": {"total": 0},
            "candidates": {"limit": 0},
        },
        "window": {"type": "rolling", "days": 14},
        "trigger": {"type": "once"},
        "vk_access": {"mode": "user", "credential_id": 4812},
    }
    html = _render_plan(
        {"plan_status": "available", "plan": plan},
        scenario=SimpleNamespace(name="Public sentiment"),
    )

    for label in ("Цель", "Данные", "Обработка", "Результат", "Срок данных и запуск", "Ограничения"):
        assert label in html
    for text in ("Find local public updates", "ВКонтакте", "выбранные источники", "Kirov public group"):
        assert text in html
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert "Сценарий «Public sentiment»" in html
    assert "Последние 14 дн." in html
    assert "персональное" in html
    assert "№4812" in html
    assert "credential_id" not in html
    assert '"source_ids"' not in html
    assert "Фразы для публикаций: renovation update" in html
    assert "Запросы для обнаружения источников: contractor discovery" in html

    collect_html = _render_plan(
        {"plan_status": "available", "plan": plan},
        task=SimpleNamespace(name="Collect only", job_type="collect", cron_expr="@once"),
        scenario=SimpleNamespace(name="Public sentiment"),
    )
    assert "Только сбор" in collect_html
    assert "Сценарий «Public sentiment»" not in collect_html


def test_legacy_and_stale_plans_have_safe_human_messages_without_plan_values():
    legacy = _render_plan({"plan_status": "legacy", "plan": None})
    stale = _render_plan({"plan_status": "scope_changed", "plan": None})

    assert "старой задачи" in legacy
    assert "Связанные источники изменились" in stale
    assert "Find local public updates" not in stale
    assert "source_ids" not in legacy + stale
