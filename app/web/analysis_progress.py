"""Pure, bounded projections for source analysis progress."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.jobs.progress import read_snapshot

# Every projection status maps to a fixed label. Raw job text is never rendered:
# an unknown status is "Статус неизвестен", not whatever string the row carries.
_STATUS_LABELS = {
    "pending": "В очереди",
    "running": "Выполняется",
    "done": "Завершено",
    "failed": "Ошибка",
    "cancelled": "Остановлено",
    "unconfirmed": "Статус не подтверждён",
    "empty": "Нет данных",
    "partial": "Обработано частично",
    "unknown": "Статус неизвестен",
}

_UNKNOWN_LABEL = "Статус неизвестен"
_COLLECT_RUNNING_LABEL = "Сбор данных"
_COLLECT_DONE_LABEL = "Сбор завершён"
_COLLECT_INLINE_RUNNING_LABEL = "Сбор и анализ"
_COLLECT_INLINE_DONE_LABEL = "Сбор и анализ завершены"
_UNIFIED_COLLECTION_RUNNING_LABEL = "Сбор новых данных"
_UNIFIED_COLLECTION_DONE_LABEL = "Сбор завершён, анализ впереди"
_KNOWN_JOB_STATUSES = frozenset({"pending", "running", "done", "failed"})


def _count(value: Any) -> int | None:
    """A bounded integer, or None for anything malformed (bool included)."""
    return value if type(value) is int and 0 <= value <= 10**9 else None


def project_job(job: Any, source_id: int) -> dict[str, Any]:
    """Return the small source-row status, keeping unknown coverage unknown.

    `per_source` is validated as a list; an absent or malformed breakdown and an
    attempt-mismatched snapshot behave as "no data". Counts are sanitized before
    any empty/zero/known decision, so malformed optional counts stay unknown
    without replacing a known job lifecycle. A missing or stale heartbeat on a
    running job is `unconfirmed` — never confused with an explicit `cancelled` stop.
    """
    raw_status = getattr(job, "status", None)
    if type(raw_status) is not str or raw_status not in _KNOWN_JOB_STATUSES:
        status, label = "unknown", _UNKNOWN_LABEL
    else:
        status, label = raw_status, _STATUS_LABELS[raw_status]
    job_status = status
    if status == "failed" and getattr(job, "error", None) in ("Cancelled by operator", "Cancelled before start"):
        status, label = "cancelled", _STATUS_LABELS["cancelled"]

    result = job.result if isinstance(getattr(job, "result", None), dict) else {}
    per_source = result.get("per_source")
    rows = per_source if isinstance(per_source, list) else []
    final = next((row for row in rows if isinstance(row, dict) and row.get("source_id") == source_id), None)
    before_start = getattr(job, "error", None) == "Cancelled before start"
    snapshot = None if before_start else read_snapshot(result, getattr(job, "attempts", None))
    snapshot_item = next((row for row in (snapshot or {}).get("items", []) if row.get("source_id") == source_id), None)

    if job_status == "done":
        item = final or snapshot_item or {}
    elif job_status == "pending":
        # Attempts increment only when claimed. A pending retry still has the
        # previous attempt number, so its stored snapshot can look current.
        item = {}
    else:
        item = snapshot_item or {}
    if status in ("pending", "running"):
        now = datetime.now(timezone.utc)
        heartbeat = getattr(job, "locked_at", None) if status == "running" else getattr(job, "created_at", None)
        unconfirmed = status == "running" and not isinstance(heartbeat, datetime)
        if status == "running" and isinstance(heartbeat, datetime):
            heartbeat = heartbeat if heartbeat.tzinfo else heartbeat.replace(tzinfo=timezone.utc)
            unconfirmed = now - heartbeat.astimezone(timezone.utc) > timedelta(seconds=90)
        item_state = snapshot_item.get("state") if snapshot_item else None
        unified_collection = getattr(job, "job_type", None) == "analyze" and (snapshot or {}).get("phase") == "collection"
        if status == "running" and snapshot_item is not None:
            if item_state == "waiting":
                status, label = "pending", _STATUS_LABELS["pending"]
            elif item_state == "skipped":
                status, label = "skipped", "Пропущено"
            elif item_state == "done":
                status = "done"
                label = _STATUS_LABELS["done"]
                if unified_collection:
                    label = _UNIFIED_COLLECTION_DONE_LABEL
                elif getattr(job, "job_type", None) == "collect":
                    label = _COLLECT_INLINE_DONE_LABEL if getattr(job, "_analysis_inline", False) else _COLLECT_DONE_LABEL
            elif item_state == "running":
                if unconfirmed:
                    status, label = "unconfirmed", _STATUS_LABELS["unconfirmed"]
                elif unified_collection:
                    label = _UNIFIED_COLLECTION_RUNNING_LABEL
                elif getattr(job, "job_type", None) == "collect":
                    label = (
                        _COLLECT_INLINE_RUNNING_LABEL
                        if getattr(job, "_analysis_inline", False)
                        else _COLLECT_RUNNING_LABEL
                    )
            else:
                status, label = "pending", _STATUS_LABELS["pending"]
        elif status == "running":
            status, label = ("unconfirmed", _STATUS_LABELS["unconfirmed"]) if unconfirmed else ("pending", _STATUS_LABELS["pending"])
        if unconfirmed and status in ("running", "pending"):
            status, label = "unconfirmed", _STATUS_LABELS["unconfirmed"]
    elif getattr(job, "job_type", None) == "collect" and status == "done":
        label = _COLLECT_INLINE_DONE_LABEL if getattr(job, "_analysis_inline", False) else _COLLECT_DONE_LABEL

    raw_total = item.get("raw_total")
    raw_processed = item.get("raw_processed")
    total = _count(raw_total)
    processed = _count(raw_processed)
    if status == "done":
        # A per-source error reads as an error, never hidden behind a partial
        # ratio: «0 из 13» after a failed LLM call is an error first, so the
        # row label cannot pass for a merely incomplete success. The coverage
        # bar stays visible either way.
        if item.get("error") is True or item.get("outcome") == "error":
            status, label = "failed", _STATUS_LABELS["failed"]
        elif item.get("collection_error") is True or item.get("outcome") == "rate_limited":
            status, label = "partial", _STATUS_LABELS["partial"]
        elif total == 0 and processed == 0:
            status, label = "empty", _STATUS_LABELS["empty"]
        elif total is not None and total > 0 and processed is not None and processed < total:
            status, label = "partial", _STATUS_LABELS["partial"]
    if total is None or processed is None or total <= 0 or processed > total:
        percent = None
        if processed is not None and total is not None and processed > total:
            processed = None
    else:
        percent = int(processed * 100 / total)
    if total == 0 and processed == 0 and status in ("done", "empty"):
        status, label = "empty", _STATUS_LABELS["empty"]
        percent = None
    return {
        "job_type": getattr(job, "job_type", None), "status": status, "label": label,
        "percent": percent, "processed": processed, "total": total,
    }
