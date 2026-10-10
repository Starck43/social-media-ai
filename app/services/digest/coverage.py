"""Conservative digest caveats from bounded, workspace-owned job history.

Execution history is not a source/content-window coverage ledger. Never turn
missing/pruned/legacy records or a later success into proof of completeness.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 200
COVERAGE_UNKNOWN = (
    "Дайджест основан на сохранённых результатах анализа. "
    "Полнота сбора и анализа источников не подтверждена; "
    "отсутствие записей не означает отсутствие публикаций."
)


def _source_failed(per: dict) -> bool:
    return per.get("error") is True or per.get("staged_error") is True or per.get("outcome") in (
        "error",
        "auth_required",
    )


def _reported_errors(result: dict, selected: set[int]) -> bool:
    per_source = result.get("per_source")
    rows = per_source if isinstance(per_source, list) else []
    for per in rows:
        if not isinstance(per, dict):
            continue
        if selected and (type(per.get("source_id")) is not int or per["source_id"] not in selected):
            continue
        if _source_failed(per):
            return True
    if selected:
        # Aggregate counts/names cannot attribute a failure to selected IDs.
        return False
    return any(type(result.get(key)) is int and result[key] > 0 for key in ("error", "staged_errors"))


async def job_coverage_note(
    start: date, end: date, source_ids: list[int] | None = None, scenario_id: int | None = None
) -> str:
    """Only attributable observed failures; no claim that data is complete.

    Dates select task completions in UTC, NOT the content window a task read.
    Task relations/configuration are mutable, so never infer historical scope
    from current AgentTask fields or a payload override that may be ignored.
    """
    from app.core.tenant_context import current_tenant_id, is_bypass
    from app.models import Job

    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        return COVERAGE_UNKNOWN + " История задач недоступна без выбранного рабочего пространства."
    if scenario_id is not None:
        # Historical per_source results contain no executed scenario identity.
        return COVERAGE_UNKNOWN + " История задач не подтверждает охват выбранного сценария."
    if any(type(value) is not int or value <= 0 for value in (source_ids or [])):
        raise ValueError("Digest source scope requires positive source IDs")
    selected = set(source_ids or [])
    lower = datetime.combine(start, time.min, tzinfo=timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    try:
        rows = list(
            await Job.objects.filter(
                tenant_id=tenant_id,
                job_type__in=["collect", "analyze"],
                status__in=["done", "failed"],
                finished_at__gte=lower,
                finished_at__lt=upper,
            ).order_by(Job.finished_at.desc(), Job.id.desc()).limit(HISTORY_LIMIT + 1)
        )
    except Exception:
        logger.warning("digest_job_history_unavailable error_code=history_read_failed")
        return COVERAGE_UNKNOWN + " История выполнения задач недоступна."

    failed_runs = 0
    for job in rows[:HISTORY_LIMIT]:
        # The query is tenant-scoped; retain a backstop for unexpected rows.
        if job.tenant_id != tenant_id or job.job_type not in {"collect", "analyze"}:
            continue
        if job.status not in {"done", "failed"}:
            continue
        result = job.result if isinstance(job.result, dict) else {}
        if result.get("status") == "skipped":
            continue
        failed = (job.status == "failed" and not selected) or _reported_errors(result, selected)
        failed_runs += int(failed)
    note = COVERAGE_UNKNOWN
    if failed_runs:
        note += (
            f" Завершённых задач сбора/анализа с подтверждёнными ошибками: {failed_runs} "
            f"(история выполнения за {start.isoformat()} — {end.isoformat()}, UTC). "
            "Это история ошибок выполнения, а не измерение охвата контента; данные могут быть неполными."
        )
    if len(rows) > HISTORY_LIMIT:
        note += f" Проверены только последние {HISTORY_LIMIT} завершённых задач, не вся история периода."
    return note
