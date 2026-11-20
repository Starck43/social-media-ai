"""Latest recorded task runs and source coverage for scenario cards."""

from sqlalchemy import String, and_, cast, func, select

from app.core.database import new_session
from app.jobs.recovery_policy import CANCELLED_BEFORE_START_ERROR, OPERATOR_CANCELLED_ERROR
from app.models.scheduling.agent_task import AgentTask
from app.models.scheduling.job import Job

from .task_presenters import _OUTCOME_LABELS, _run_outcome

_COLLECTION_ISSUES = {
    "auth_required": "Нужно переподключить источник",
    "rate_limited": "Достигнут лимит запросов",
    "error": "Ошибка сбора",
    "narrowed": "Источник проверен с ограниченным доступом",
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _source_coverage(job):
    result = _mapping(job.result)
    snapshot = _mapping(job.snapshot)
    if job.status != "done" or result.get("dry_run") or snapshot.get("dry_run"):
        return None

    stages = _mapping(_mapping(result.get("pipeline")).get("stages"))
    if stages:
        collection = _mapping(_mapping(stages.get("collect")).get("result"))
        counts = _mapping(collection.get("counts"))
        rows = collection.get("per_source")
    elif job.job_type == "analyze":
        collection = _mapping(result.get("collection"))
        counts = collection
        per_source = result.get("per_source")
        if not isinstance(per_source, list):
            return None
        rows = [
            {**row["collection"], "source_id": row.get("source_id"), "name": row.get("name")}
            for row in per_source
            if isinstance(row, dict) and isinstance(row.get("collection"), dict)
        ]
    else:
        collection = counts = result
        rows = result.get("per_source")

    expected = counts.get("sources")
    if type(expected) is not int or expected < 0 or not isinstance(rows, list) or len(rows) != expected:
        return None

    planned = _mapping(snapshot.get("task_payload")).get("source_ids")
    if not isinstance(planned, list) or any(type(source_id) is not int or source_id <= 0 for source_id in planned):
        return None

    seen = set()
    checked = 0
    issues = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        source_id = row.get("source_id")
        if type(source_id) is not int or source_id <= 0 or source_id in seen:
            return None
        seen.add(source_id)
        outcome = row.get("outcome")
        if outcome in ("collected", "empty") and not row.get("narrowed_access"):
            checked += 1
            continue
        if outcome in ("collected", "empty"):
            outcome = "narrowed"
        if isinstance(outcome, str) and outcome in _COLLECTION_ISSUES:
            name = row.get("name")
            issues.append((name[:80] if isinstance(name, str) and name else None, _COLLECTION_ISSUES[outcome]))

    resolved = collection.get("coverage_source_ids")
    if isinstance(resolved, list) and all(type(source_id) is int and source_id > 0 for source_id in resolved):
        planned = set(planned).union(resolved)
    total = len(seen.union(planned))
    skipped = _mapping(collection.get("task_budget")).get("sources_skipped")
    if not total or (type(skipped) is int and expected + skipped > total):
        return None
    return {
        "total": total,
        "checked": checked,
        "issues": issues[:2],
        "issue_count": len(issues),
        "unconfirmed": total - checked - len(issues),
    }


def _run_card(job, *, can_view_sources):
    coverage = _source_coverage(job) if can_view_sources else None
    if job.status == "failed":
        status = "Отменён" if job.error in (OPERATOR_CANCELLED_ERROR, CANCELLED_BEFORE_START_ERROR) else "Ошибка"
    elif not can_view_sources:
        status = "Завершён"
    else:
        result = _mapping(job.result)
        if result.get("dry_run") or _mapping(job.snapshot).get("dry_run"):
            status = "Тестовый прогон"
        elif result.get("status") == "partial" or (coverage and coverage["checked"] < coverage["total"]):
            status = _OUTCOME_LABELS["partial"]
        else:
            status = _OUTCOME_LABELS[_run_outcome(job.job_type, result)]
    return {
        "id": job.id,
        "task_id": job.task_id,
        "task_name": job.task_name,
        "finished_at": job.finished_at,
        "status": status,
        "coverage": coverage,
    }


async def latest_scenario_runs(tenant_id: int, scenario_ids: list[int], *, can_view_sources: bool) -> dict[int, dict]:
    if not scenario_ids:
        return {}

    # Use the admitted scenario rather than attaching old runs to a task's current binding.
    snapshot = Job.payload["_effective_snapshot"]
    scenario_id = snapshot["scenario_id"].as_string()
    ranked = (
        select(
            Job.id,
            scenario_id.label("scenario_id"),
            func.row_number()
            .over(partition_by=scenario_id, order_by=(Job.finished_at.desc(), Job.id.desc()))
            .label("position"),
        )
        .where(
            Job.tenant_id == tenant_id,
            Job.agent_task_id.is_not(None),
            Job.job_type.in_(("collect", "analyze")),
            Job.status.in_(("done", "failed")),
            Job.finished_at.is_not(None),
            snapshot["task_id"].as_string() == cast(Job.agent_task_id, String),
            scenario_id.in_([str(value) for value in scenario_ids]),
        )
        .subquery()
    )
    columns = [
        Job.id,
        ranked.c.scenario_id,
        Job.job_type,
        Job.status,
        Job.error,
        Job.finished_at,
        AgentTask.id.label("task_id"),
        AgentTask.name.label("task_name"),
    ]
    if can_view_sources:
        columns.extend((Job.result, snapshot.label("snapshot")))
    query = (
        select(*columns)
        .join(ranked, Job.id == ranked.c.id)
        .join(AgentTask, and_(AgentTask.id == Job.agent_task_id, AgentTask.tenant_id == Job.tenant_id))
        .where(ranked.c.position == 1, Job.tenant_id == tenant_id)
    )
    async with new_session() as session:
        rows = (await session.execute(query)).all()
    return {int(job.scenario_id): _run_card(job, can_view_sources=can_view_sources) for job in rows}
