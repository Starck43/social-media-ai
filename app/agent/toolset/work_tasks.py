"""Canonical work-task tools (P22-T07) over the same services as `/api/v2/tasks`.

The agent surface never re-implements rights, versions or admission: every
handler calls the module the API calls, and a refusal becomes the same bounded
`{"error", "code"}` shape the rest of the toolset uses.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from app.services.execution_plan_contracts import ExecutionPlanError
from app.services.planner.canonical_tasks import CreateTask, UpdateTask, task_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.services.task_run_admission import TaskRunAdmissionError

ERRORS = {
    "not_found": ("Work task unavailable", "work_task_unavailable"),
    "denied": ("Permission denied", "work_task_denied"),
    "stale": ("The task changed; reload it", "work_task_stale"),
    "invalid": ("Invalid work task request", "work_task_invalid_request"),
}


def _failure(kind: str) -> dict[str, Any]:
    message, code = ERRORS[kind]
    return {"error": message, "code": code}


async def _call(operation, *, conflict: str = "stale") -> Any:
    try:
        return await operation
    except PlannerUnavailable:
        return _failure("not_found")
    except PlannerConflict:
        return _failure(conflict)
    except (ExecutionPlanError, TaskRunAdmissionError):
        return _failure("invalid")
    except PermissionError:
        return _failure("denied")
    except ValueError:
        return _failure("invalid")


def _failed(result) -> bool:
    return isinstance(result, dict) and "error" in result


def _dump(model) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "__dataclass_fields__"):
        return dataclasses.asdict(model)
    return model


async def work_task_list(limit: int = 50, before_id: int | None = None, view: str | None = None) -> dict:
    result = await _call(task_service.list(limit=limit, before_id=before_id, view=view))
    if _failed(result):
        return result
    return {**result, "items": [_dump(item) for item in result["items"]]}


async def work_task_get(task_id: int) -> dict:
    result = await _call(task_service.get(task_id))
    return result if _failed(result) else _dump(result)


async def work_task_create(task: dict[str, Any]) -> dict:
    try:
        request = CreateTask.model_validate(task)
    except ValueError:
        return _failure("invalid")
    result = await _call(task_service.create(request))
    return result if _failed(result) else _dump(result)


async def work_task_edit(task_id: int, changes: dict[str, Any]) -> dict:
    try:
        request = UpdateTask.model_validate(changes)
    except ValueError:
        return _failure("invalid")
    result = await _call(task_service.update(task_id, request))
    return result if _failed(result) else _dump(result)


async def work_task_preview(task_id: int, plan: dict[str, Any]) -> dict:
    from app.services.task_execution_plans import execution_plan_service

    return await _call(execution_plan_service.preview(task_id, json.dumps(plan)))


async def work_task_complete(task_id: int, expected_version: int) -> dict:
    result = await _call(task_service.complete(task_id, expected_version))
    if _failed(result):
        return result
    return result.model_dump(mode="json")


async def work_task_delete_preview(task_id: int, scope: str) -> dict:
    from app.services.commands.task_deletion import task_deletion_service

    result = await _call(task_deletion_service.preview(task_id, scope=scope))
    return result if _failed(result) else {**_dump(result), "expires_in": result.expires_in}


async def work_task_delete(task_id: int, scope: str, confirmation: str) -> dict:
    from app.services.commands.task_deletion import task_deletion_service

    result = await _call(task_deletion_service.confirm(task_id, scope=scope, confirmation=confirmation))
    return result if _failed(result) else _dump(result)


async def work_task_run(task_id: int, request_key: str) -> dict:
    from app.services.task_execution_plans import execution_plan_service
    from app.services.task_run_admission import task_run_service

    async def admit():
        reference = await execution_plan_service.get_reference(task_id)
        if reference is None:
            raise PlannerConflict("stale_version")
        return await task_run_service.admit_manual(
            task_id, expected_revision=reference.revision, request_key=request_key
        )

    result = await _call(admit())
    if _failed(result):
        return result
    return {
        "run_id": result.run_id,
        "task_id": result.task_id,
        "revision": result.revision,
        "job_id": result.job_id,
        "status": result.status,
        "result": result.result,
        "replayed": result.replayed,
    }


async def work_task_runs(task_id: int, limit: int = 50, before_id: int | None = None) -> dict:
    from app.services.task_run_admission import task_run_service

    page = await _call(task_run_service.list_receipts(task_id, limit=limit, before_id=before_id))
    if _failed(page):
        return page
    return {**page, "items": [_dump(item) for item in page["items"]]}


async def work_task_actions(task_id: int) -> dict:
    from app.services.workspace.task_surface import canonical_task_actions

    return await _call(canonical_task_actions(task_id))


async def work_task_decision_preview(task_id: int, reference: str, outcome: str) -> dict:
    from app.services.commands.task_decisions import task_decision_service

    return await _call(task_decision_service.preview(task_id, reference, outcome=outcome))


async def work_task_decide(task_id: int, confirmation: str, reason: str = "") -> dict:
    from app.services.commands.task_decisions import task_decision_service

    return await _call(task_decision_service.decide(task_id, confirmation=confirmation, reason=reason))


async def work_task_resolve_legacy(namespace: str, legacy_id: int) -> dict:
    from app.core.tenant_context import current_tenant_id
    from app.services.planner.legacy_migration import project_legacy_task

    projected = await _call(project_legacy_task(current_tenant_id(), namespace, legacy_id))
    if _failed(projected):
        return projected
    return {"namespace": namespace, "legacy_id": legacy_id, "task": projected.model_dump(mode="json")}


READ = "planner.task.view"
WRITE = "planner.task.update"


def register_work_task_tools() -> None:
    from app.agent.tools import TOOL_REGISTRY, tool
    from app.core.access_catalog import resolve_permission_key

    if any(
        resolve_permission_key(f"planner.task.{action}") is None for action in ("view", "create", "update", "delete")
    ):
        raise ValueError("work_task_catalog_not_registered")

    string = {"type": "string"}
    integer = {"type": "integer", "minimum": 1}
    declarations = (
        (
            "work_task_list",
            work_task_list,
            "Показать канонические задачи пространства; личные и командные.",
            READ,
            False,
            {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "before_id": {"type": ["integer", "null"], "minimum": 1},
                "view": {"type": ["string", "null"], "enum": ["mine", "team", None]},
            },
            [],
        ),
        (
            "work_task_get",
            work_task_get,
            "Прочитать одну каноническую задачу.",
            READ,
            False,
            {"task_id": integer},
            ["task_id"],
        ),
        (
            "work_task_create",
            work_task_create,
            "Создать личную или командную задачу; повтор с тем же содержанием создаёт новую.",
            "planner.task.create",
            True,
            {
                "task": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": string,
                        "visibility": {"type": "string", "enum": ["private", "shared", "workspace"]},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "due_at": {"type": ["string", "null"]},
                        "due_date": {"type": ["string", "null"]},
                        "timezone": string,
                        "recurrence": {"type": ["string", "null"]},
                        "tags": {"type": "array", "items": string},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                }
            },
            ["task"],
        ),
        (
            "work_task_edit",
            work_task_edit,
            "Изменить задачу по ожидаемой версии; устаревшая версия отклоняется.",
            WRITE,
            True,
            {
                "task_id": integer,
                "changes": {
                    "type": "object",
                    "properties": {
                        "expected_version": {"type": "integer", "minimum": 1},
                        "scope": {"type": "string", "enum": ["occurrence", "series"]},
                        "title": {"type": ["string", "null"]},
                        "description": {"type": ["string", "null"]},
                        "priority": {"type": ["string", "null"]},
                        "status": {"type": ["string", "null"], "enum": ["todo", "in_progress", "cancelled", None]},
                    },
                    "required": ["expected_version", "scope"],
                    "additionalProperties": False,
                },
            },
            ["task_id", "changes"],
        ),
        (
            "work_task_preview",
            work_task_preview,
            "Проверить план задачи без побочных эффектов: не публикует и не запускает.",
            WRITE,
            False,
            {"task_id": integer, "plan": {"type": "object"}},
            ["task_id", "plan"],
        ),
        (
            "work_task_complete",
            work_task_complete,
            "Завершить работу по задаче с ожидаемой версией; повторное завершение читает преемника.",
            WRITE,
            True,
            {"task_id": integer, "expected_version": {"type": "integer", "minimum": 1}},
            ["task_id", "expected_version"],
        ),
        (
            "work_task_delete_preview",
            work_task_delete_preview,
            "Показать, что удалит подтверждение: вхождения, расписания, очереди, чеки.",
            "planner.task.delete",
            False,
            {"task_id": integer, "scope": {"type": "string", "enum": ["occurrence", "series"]}},
            ["task_id", "scope"],
        ),
        (
            "work_task_delete",
            work_task_delete,
            "Удалить вхождение или серию по подписанному токену предпросмотра.",
            "planner.task.delete",
            True,
            {
                "task_id": integer,
                "scope": {"type": "string", "enum": ["occurrence", "series"]},
                "confirmation": string,
            },
            ["task_id", "scope", "confirmation"],
        ),
        (
            "work_task_run",
            work_task_run,
            "Запустить текущую опубликованную ревизию вручную; повтор ключа возвращает тот же чек.",
            WRITE,
            True,
            {"task_id": integer, "request_key": {"type": "string"}},
            ["task_id", "request_key"],
        ),
        (
            "work_task_runs",
            work_task_runs,
            "Показать чеки прогонов задачи от новых к старым.",
            READ,
            False,
            {
                "task_id": integer,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "before_id": {"type": ["integer", "null"], "minimum": 1},
            },
            ["task_id"],
        ),
        (
            "work_task_actions",
            work_task_actions,
            "Доступные действия и ожидающие решения по задаче.",
            READ,
            False,
            {"task_id": integer},
            ["task_id"],
        ),
        (
            "work_task_decision_preview",
            work_task_decision_preview,
            "Подготовить решение по задаче: возвращает подписанный токен подтверждения.",
            WRITE,
            False,
            {
                "task_id": integer,
                "reference": string,
                "outcome": {"type": "string", "enum": ["accepted", "rejected"]},
            },
            ["task_id", "reference", "outcome"],
        ),
        (
            "work_task_decide",
            work_task_decide,
            "Закрыть ожидающее решение предпросмотра; повтор с тем же исходом идемпотентен.",
            WRITE,
            True,
            {"task_id": integer, "confirmation": string, "reason": string},
            ["task_id", "confirmation"],
        ),
        (
            "work_task_resolve_legacy",
            work_task_resolve_legacy,
            "Перевести устаревшую ссылку в каноническую задачу через её отображение.",
            READ,
            False,
            {"namespace": {"type": "string"}, "legacy_id": integer},
            ["namespace", "legacy_id"],
        ),
    )
    for name, handler, description, permission, confirm, properties, required in declarations:
        if name in TOOL_REGISTRY:
            continue
        tool(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            required_permission=permission,
            confirm=confirm,
        )(handler)
