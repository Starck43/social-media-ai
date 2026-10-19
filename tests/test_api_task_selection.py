"""API: у задачи есть отбор, у мониторинга больше нет маршрута `monitored`.

Owner command:
`.venv/bin/python -m pytest tests/test_api_task_selection.py -q --noconftest -o addopts=`

Контракт из `docs/design/task-selection.md`: схемы задачи принимают
`tracked_users`, `keywords_list`, `keywords_match` (в payload или отдельными
полями), а `/monitoring/collect/monitored` и `CollectMonitoredRequest` удалены
вместе с `ContentCollector.collect_monitored_users`.

Схемы импортируются напрямую (pydantic, без сервера); эндпоинты разбираются
через AST — их импорт тянет `app.api.deps` и модели, а тесту нужен только
объявленный контракт.
"""

import ast
from pathlib import Path

import pytest

from app.schemas import monitoring as monitoring_schemas
from app.schemas import task as task_schemas

ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS = ROOT / "app" / "api" / "v1" / "endpoints"

SELECTION_KEYS = ("tracked_users", "keywords_list", "keywords_match")


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _routes(path):
    """(метод, путь) из декораторов `@router.<verb>("...")`."""
    routes = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                routes.add((method, decorator.args[0].value))
    return routes


def _model_fields(model):
    return set(getattr(model, "model_fields", {}) or getattr(model, "__fields__", {}))


def _accepts_selection(payload_schema):
    """Схема либо объявляет поля отбора, либо принимает свободный payload.

    Задача хранит отбор в `payload` (см. `AgentTaskManager.build_selection_payload`).
    Свободная форма значения ещё не значит, что схема его принимает: `dict[str, Any]`
    пропустит что угодно, а вот `Optional[dict[str, int]]` — уже нет. Поэтому
    проверяется именно форма аннотации: голый dict/`Any`/`Mapping`.
    """
    fields = _model_fields(payload_schema)
    if any(key in fields for key in SELECTION_KEYS):
        return True
    if "payload" not in fields:
        return False
    annotation = str(payload_schema.model_fields["payload"].annotation).lower()
    if "dict" in annotation and "[" not in annotation:
        return True
    return annotation.endswith("any]") or "mapping" in annotation


class TestTaskSchemasAcceptSelection:
    @pytest.mark.parametrize("name", ["TaskCreate", "TaskUpdate"])
    def test_task_schema_accepts_the_three_keys(self, name):
        schema = getattr(task_schemas, name)
        assert _accepts_selection(schema), f"{name} не принимает отбор задачи"


class TestMonitoringHasNoMonitoredRoute:
    def test_no_collect_monitored_route(self):
        routes = _routes(ENDPOINTS / "monitoring.py")
        assert ("post", "/collect/monitored") not in routes
        assert not any("monitored" in path for _method, path in routes), sorted(routes)

    def test_schema_is_gone(self):
        assert not hasattr(monitoring_schemas, "CollectMonitoredRequest")

    def test_source_collection_endpoints_remain(self):
        """Штатный сбор с источника не удаляется вместе с monitored-маршрутом."""
        routes = _routes(ENDPOINTS / "monitoring.py")
        assert ("post", "/collect/source") in routes

    def test_endpoint_module_does_not_mention_monitored(self):
        source = (ENDPOINTS / "monitoring.py").read_text(encoding="utf-8")
        assert "monitored" not in source
