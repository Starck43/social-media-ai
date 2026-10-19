"""Pure replay safety rules shared by the Jobs UI and admission manager."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.jobs.recovery_policy import outcome_unconfirmed

REPLAYABLE_TYPES = frozenset({"collect", "analyze", "learn", "reflect"})
REPLAYABLE_STATUSES = frozenset({"done", "failed"})
REVIEW_CODES = frozenset({"partial_delivery_requires_review", "digest_run_in_progress_requires_review"})
REVIEW_FLAGS = ("needs_review", "review_required", "operator_review", "delivery_review")


def _has_replay_window(task_payload: Any) -> bool:
    dates = task_payload.get("cli_dates") if isinstance(task_payload, dict) else None
    if not isinstance(dates, dict):
        return False
    start, end = dates.get("start_date"), dates.get("end_date")
    if type(start) is not str or type(end) is not str:
        return False
    try:
        parsed_start = date.fromisoformat(start)
        parsed_end = date.fromisoformat(end)
    except (TypeError, ValueError):
        return False
    return parsed_end >= parsed_start


def replay_block_reason(job: Any) -> str | None:
    """Return a safe operator-facing reason when a row cannot be replayed."""
    if job is None or job.status not in REPLAYABLE_STATUSES:
        return "Повторить можно только завершённый запуск."
    if job.job_type not in REPLAYABLE_TYPES or job.job_type == "prune":
        return "Этот тип задания нельзя безопасно повторить из Jobs."
    result = job.result if isinstance(job.result, dict) else {}
    if outcome_unconfirmed(job.error) or job.error in {"Cancelled by operator", "Cancelled before start"}:
        return "Итог прошлого запуска не подтверждён. Проверьте внешний эффект перед повтором."
    if any(result.get(key) is True for key in REVIEW_FLAGS):
        return "Результат требует ручной проверки; повтор заблокирован до её завершения."
    if any(
        code in str(value).lower()
        for code in REVIEW_CODES
        for value in (
            job.error,
            result.get("status"),
            result.get("error"),
            result.get("diagnostic_code"),
            result.get("delivery_status"),
        )
    ):
        return "Результат требует ручной проверки; повтор заблокирован до её завершения."
    if job.job_type in {"collect", "analyze"}:
        payload = job.payload if isinstance(job.payload, dict) else {}
        snapshot = payload.get("_effective_snapshot")
        task_payload = snapshot.get("task_payload") if isinstance(snapshot, dict) else None
        source_ids = task_payload.get("source_ids") if isinstance(task_payload, dict) else None
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("version") != 1
            or not isinstance(source_ids, list)
            or not source_ids
            or any(type(value) is not int or value <= 0 for value in source_ids)
            or len(source_ids) != len(set(source_ids))
        ):
            return "У старого запуска нет надёжного снимка источников. Создайте новый запуск из задачи."
        if not _has_replay_window(task_payload):
            return "У запуска нет сохранённого периода. Создайте новый запуск из задачи, чтобы не обработать новые данные повторно."
        if snapshot.get("action_type"):
            return "У задачи настроено внешнее действие. Проверьте результат и запустите её со страницы задачи."
    elif job.agent_task_id is not None:
        return "Для повтора задачи откройте её страницу: там будут использованы сохранённые настройки."
    return None


def can_replay(job: Any) -> bool:
    return replay_block_reason(job) is None
