"""Source-only pipeline projection and actual template, no bootstrap/DB/network.

Storage query double evaluates tenant/status filters, ordering and limit against
synthetic rows (including rows outside the preview); not ORM/SQL acceptance.
Actual middleware/revocation remains covered by the P10-T08 companion suite.
"""

import asyncio
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pipeline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network in source-only tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    before = {key for key in sys.modules if key == "app" or key.startswith("app.")}
    state = NS(rows=[], queries=[], stages=17, analyses=23)

    class Column:
        def __init__(self, name):
            self.name = name

        def desc(self):
            return self.name

    class Query:
        def __init__(self, model, filters=None, order=()):
            self.model, self.filters, self.order = model, filters, order

        def filter(self, **kwargs):
            assert kwargs["tenant_id"] == 11
            state.queries.append((self.model, kwargs))
            return Query(self.model, kwargs)

        async def count(self):
            assert self.filters == {"tenant_id": 11}
            assert self.model != "job", "Preview must not add a total-count read"
            return state.stages if self.model == "collecteditem" else state.analyses

        def order_by(self, *columns):
            return Query(self.model, self.filters, columns)

        async def limit(self, size):
            assert self.model == "job" and size == 3
            assert self.filters == {"tenant_id": 11, "status__in": ["pending", "running"]}
            assert self.order in (("created_at",), ("created_at", "id"))
            rows = [row for row in state.rows if row.tenant_id == 11 and row.status in self.filters["status__in"]]
            return sorted(rows, key=lambda row: tuple(getattr(row, column) for column in self.order), reverse=True)[
                :size
            ]

    def model(name):
        return NS(objects=Query(name), created_at=Column("created_at"), id=Column("id"))

    module = load_isolated_source(
        "_p10_t09_web.dashboard",
        ROOT / "app/web/dashboard.py",
        {
            "app.models.scheduling.agent_task": NS(AgentTask=model("agenttask")),
            "app.models.analysis.ai_analytics": NS(AIAnalytics=model("aianalytics")),
            "app.models.collection.source": NS(Source=model("source")),
            "app.models.collection.collected_item": NS(CollectedItem=model("collecteditem")),
            "app.models.scheduling.job": NS(Job=model("job")),
            "_p10_t09_web.deps": NS(render=None, tenant_filter_context=None),
        },
    )
    state.offer = {
        "analytics": True,
        "collected_items": True,
        "jobs": True,
        "tasks": False,
        "sources": False,
        "digests": False,
        "add_source": False,
        "add_task": False,
        "add_scenario": False,
    }
    state.project = lambda: asyncio.run(module._pipeline(11, False, state.offer))
    now = datetime(2026, 10, 26, tzinfo=timezone.utc)
    state.row = lambda identifier, kind="collect", status="running", tenant=11, age=0: NS(
        id=identifier,
        tenant_id=tenant,
        job_type=kind,
        status=status,
        created_at=now - timedelta(seconds=age),
        run_at=now + timedelta(days=2) if status == "pending" else now,
    )
    yield state
    assert {key for key in sys.modules if key == "app" or key.startswith("app.")} == before


def render(pipeline, value):
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"web/base.html": "{% block content %}{% endblock %}"}),
                FileSystemLoader(ROOT / "app/web/templates"),
            ]
        ),
        undefined=StrictUndefined,
        autoescape=select_autoescape(("html",)),
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = str
    return env.get_template("web/dashboard.html").render(
        offer=pipeline.offer,
        pipeline=value,
        tenant=NS(name="synthetic"),
        next_step=None,
        is_superuser=False,
        can_open_chat=False,
        recent_results=[],
        recent_tasks=[],
        source_schedule_status_unknown=False,
    )


@pytest.mark.parametrize(
    "kind,label",
    [
        ("collect", "Сбор данных выполняется…"),
        ("analyze", "Анализ выполняется…"),
        ("digest", "Подготовка дайджеста выполняется…"),
        ("prune", "Очистка данных выполняется…"),
        ("learn", "Обучение выполняется…"),
        ("reflect", "Рефлексия выполняется…"),
        ("future", "Задание выполняется…"),
        ("<img src=x onerror=alert(1)>", "Задание выполняется…"),
        (None, "Задание выполняется…"),
    ],
)
def test_running_activity_matches_type_not_collect_else_llm(pipeline, kind, label):
    pipeline.rows = [pipeline.row(1, kind)]
    value = pipeline.project()
    assert value["running"] == [{"job_type": kind, "label": label}]
    html = render(pipeline, value)
    assert label in html
    assert "Модель анализирует" not in html
    assert "<img src=x" not in html


@pytest.mark.parametrize("size", [0, 1, 3, 4, 9])
def test_job_counts_are_explicit_bounded_preview_not_totals(pipeline, size):
    # Newest jobs pending, older running jobs intentionally outside the preview.
    pipeline.rows = [pipeline.row(i, status="pending" if i >= size - 3 else "running") for i in range(size)]
    pipeline.rows += [pipeline.row(900, tenant=22), pipeline.row(901, status="done")]
    value = pipeline.project()
    assert value["job_count"] == min(size, 3)
    assert value["queued_count"] == min(size, 3)
    assert value["running"] == []
    assert value["preview_limit"] == 3
    assert len(pipeline.queries) == 3
    html = render(pipeline, value)
    assert "Предпросмотр" in html and "не общее число" in html
    assert "не более 3" in html
    assert "выполняются в предпросмотре" in html
    assert "ожидают в предпросмотре" in html
    assert "скоро" not in html and "Очередь пуста" not in html
    if size:
        assert "В предпросмотре — ожидающие задания" in html
    else:
        assert "Нет ожидающих или выполняющихся заданий" in html


def test_preview_sort_is_newest_first_with_id_tiebreaker(pipeline):
    pipeline.rows = [
        pipeline.row(2, "prune"),
        pipeline.row(1, "digest"),
        pipeline.row(4, "learn"),
        pipeline.row(3, "reflect"),
        pipeline.row(90, "analyze", age=60),
    ]
    value = pipeline.project()
    assert [row["job_type"] for row in value["running"]] == ["learn", "reflect", "prune"]
    assert value["queued_count"] == 0 and value["job_count"] == 3


def test_staged_is_literal_stored_count_not_eligibility(pipeline):
    value = pipeline.project()
    assert value["staged"] == 17
    assert ("collecteditem", {"tenant_id": 11}) in pipeline.queries
    html = render(pipeline, value)
    assert "сохранено материалов" in html
    assert "ожидают анализа" not in html
    assert "сохранено анализов" in html


def test_denied_jobs_do_not_render_injected_activity_or_preview(pipeline):
    value = pipeline.project()
    pipeline.offer["jobs"] = False
    html = render(pipeline, value)
    assert "Предпросмотр" not in html and "не общее число" not in html
    assert 'href="/app/jobs"' not in html
    assert "Нет ожидающих или выполняющихся заданий" not in html
    assert "сохранено материалов" in html
