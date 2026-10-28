"""Task run and job status presentation models."""

from __future__ import annotations

from .task_shared import (
    CANCELLED_BEFORE_START_ERROR,
    OPERATOR_CANCELLED_ERROR,
    AgentTaskManager,
    Any,
    Job,
    Request,
    Source,
    datetime,
    logger,
    perms_can,
    plural,
    project_run,
    read_snapshot,
    sidebar_sources,
    timedelta,
    timezone,
)


async def _task_run_scope(
    task: Any, *, include_raw: bool = False, include_sources: bool = False
) -> dict[str, Any] | None:
    """Return task source scope and, when authorized, its current raw backlog."""
    if task.job_type not in ("collect", "analyze"):
        return None
    from app.core.tenant_context import tenant_scope
    from app.models.collection.source import Source

    with tenant_scope(task.tenant_id):
        all_sources = await Source.objects.filter(tenant_id=task.tenant_id)
        linked_ids = set(await _task_source_ids(task.id))
    if linked_ids:
        scoped = [item for item in all_sources if item.id in linked_ids]
    else:
        # A task-scoped query lane is a flag on one task, not part of the
        # workspace's "all active sources" set.
        scoped = [item for item in all_sources if item.is_active and item.owner_task_id is None]
    scope: dict[str, Any] = {
        "total": len(scoped),
        "active": sum(1 for item in scoped if item.is_active),
        "platforms": len({item.platform_id for item in scoped}),
    }
    if include_sources:
        scope["inventory"] = [
            {"source_id": item.id, "name": item.name[:160]}
            for item in sorted(scoped, key=lambda item: (item.name, item.id))
        ]
    if include_raw and task.job_type == "analyze":
        from app.models.collection.collected_item import CollectedItem
        from app.models.managers.agent_task_manager import AgentTaskManager

        task_payload = task.payload if isinstance(task.payload, dict) else {}
        task_dates = task_payload.get("cli_dates")
        windows = []
        unknown_sources = 0
        for source in scoped:
            source_params = source.params if isinstance(source.params, dict) else {}
            try:
                start_date, end_date = AgentTaskManager.resolve_cli_date_window(
                    task_dates, source_params.get("cli_dates")
                )
            except ValueError:
                unknown_sources += 1
                continue
            windows.append({"source_id": source.id, "start_date": start_date, "end_date": end_date})
        with tenant_scope(task.tenant_id):
            backlog = await CollectedItem.objects.backlog_counts_for_sources(windows)
        backlog["unknown_sources"] = unknown_sources
        scope["raw_backlog"] = backlog
    return scope


def _population_view(job: Any) -> dict[str, Any] | None:
    """The population step of the run: what it found, or why it could not search."""
    result = job.result if isinstance(job.result, dict) else {}
    collection = result.get("collection") if isinstance(result, dict) else None
    population = collection.get("population") if isinstance(collection, dict) else None
    if not isinstance(population, dict):
        return None
    return {
        "note": population.get("note"),
        "queries": [str(value) for value in (population.get("queries") or []) if value],
        "found": population.get("found"),
        "created": population.get("created"),
        "reused": population.get("reused"),
        "city": population.get("city"),
        "error_code": population.get("error_code"),
        "error_hint": population.get("error_hint"),
        "unsupported_reason": population.get("unsupported_reason"),
    }


async def _lane_run_rows(job: Any) -> list[dict[str, Any]]:
    """Per-lane evidence of the run: counts and bounds, never the rejected text."""
    from app.models import CollectionLaneRun

    rows = await CollectionLaneRun.objects.filter(job_id=job.id).order_by(CollectionLaneRun.id)
    evidence: list[dict[str, Any]] = []
    for row in rows:
        evidence.append(
            {
                "lane_id": row.lane_id,
                "lane_type": row.lane_type,
                "query": row.query,
                "fetched": row.fetched,
                "limit_count": getattr(row, "limit_count", None),
                "selected": row.selected,
                "needs_review": row.needs_review,
                "rejected": row.rejected,
                "limit_reached": row.limit_reached,
                "error_code": row.error_code,
                "unsupported_reason": row.unsupported_reason,
            }
        )
    return evidence


def _legacy_task_status_label(task: Any) -> str:
    """Translate the task's historical schedule receipt without implying a live Job."""
    return {
        "queued": "последний запуск ожидал очереди",
        "ok": "завершилась успешно",
        "failed": "завершилась с ошибкой",
        "skipped": "пропущена",
    }.get(getattr(task, "last_status", None), "результат не подтверждён")


async def _attach_task_lane_evidence(panel: dict[str, Any], job: Any | None) -> None:
    """Add the per-lane evidence of the run to the panel summary.

    The panel builder is synchronous, so the lane rows (an async read) are
    attached here by the async callers.
    """
    if job is None:
        return
    summary = panel.get("summary")
    if not isinstance(summary, dict):
        return
    lane_runs = await _lane_run_rows(job)
    if lane_runs:
        summary["lane_runs"] = lane_runs


async def _attach_task_llm_diagnostics(request: Request, task: Any, job: Any | None, panel: dict[str, Any]) -> None:
    """Attach only allowlisted LLM usage aggregates for this task's latest job."""
    if job is None or not _diagnostics_admin(request):
        return
    summary = panel.get("summary")
    diagnostics = summary.get("diagnostics") if isinstance(summary, dict) else None
    if not isinstance(diagnostics, dict):
        return
    try:
        from app.models.managers.llm_attempt_manager import LLMAttemptManager

        usage = await LLMAttemptManager().task_run_summary(task.id, job.id)
    except Exception as exc:
        logger.warning("task_llm_diagnostics_unavailable kind=%s", type(exc).__name__)
        return
    if not isinstance(usage, dict):
        return
    for key in ("model", "provider", "succeeded", "failed", "error_code"):
        if key in usage:
            if key == "error_code" and not usage[key]:
                continue
            diagnostics[key] = usage[key]


def _task_run_panel_data(
    request: Request, task: Any, job: Any | None, *, run_scope: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a task-scoped latest-run projection, with diagnostics for admins only."""
    can_view_sources = perms_can(request, "source", "view")
    run_progress = project_run(job, task, authorized=can_view_sources)
    inventory = (run_scope or {}).get("inventory", []) if can_view_sources else []
    source_inventory = sidebar_sources(inventory, job, admin=_diagnostics_admin(request))
    if job is None:
        return {
            "run_progress": run_progress,
            "source_inventory": source_inventory,
            "job": None,
            "summary": None,
            "sources": [],
            "can_view_sources": perms_can(request, "source", "view"),
            "run_scope": run_scope,
            "legacy_status_label": _legacy_task_status_label(task),
            "can_retry_analysis": perms_can(request, "agenttask", "update"),
            "running_job_id": None,
        }

    summary = _job_summary(job, task.name)
    # The task page reports the lifecycle and counters, while raw exception text
    # stays out of the operator UI. Diagnostics retain only their existing allowlist.
    if summary.get("status") == "failed":
        summary["error"] = "Задача завершилась с ошибкой. Повторите запуск позже."
    can_view_sources = perms_can(request, "source", "view")
    sources = _source_links(job) if can_view_sources else []
    if summary.get("status") == "done" and job.job_type in ("collect", "analyze"):
        result = job.result if isinstance(job.result, dict) else {}
        rows = result.get("per_source")
        if isinstance(rows, list) and rows:
            valid_rows = [row for row in rows if isinstance(row, dict) and type(row.get("source_id")) is int]
            steps = []
            for row in valid_rows[:50]:
                failed = row.get("error") is True or row.get("outcome") == "error"
                steps.append(
                    {
                        "source_id": row["source_id"],
                        "name": row.get("name")[:80] if isinstance(row.get("name"), str) else "Источник",
                        "state": "done",
                        "note": _source_note(row),
                        "error": failed,
                    }
                )
            if steps:
                summary["progress"] = {
                    "phase": "Обработка источников",
                    "total": len(valid_rows),
                    "completed": len(valid_rows),
                    "percent": 100,
                    "current": None,
                    "steps": steps,
                    "hidden_after": max(0, len(valid_rows) - len(steps)),
                }
    if can_view_sources:
        if job.job_type == "analyze":
            summary["raw_coverage"] = _raw_coverage(job)
    else:
        summary.pop("raw_coverage", None)
        summary.pop("progress", None)
    if job.job_type in ("collect", "analyze"):
        # What the population step did: how many entities it found, or the
        # named reason it could not search. The owner must see it on the task
        # page — "nothing found" and "the platform refused the search" are
        # different facts.
        population = _population_view(job)
        if population:
            summary["population"] = population
    if _diagnostics_admin(request):
        summary["diagnostics"] = _diagnostics_view(job)
        if not can_view_sources:
            summary["diagnostics"].pop("selected", None)
            summary["diagnostics"].pop("remaining", None)
    return {
        "run_progress": run_progress,
        "source_inventory": source_inventory,
        "job": job,
        "summary": summary,
        "sources": sources,
        "can_view_sources": can_view_sources,
        "run_scope": run_scope,
        "legacy_status_label": _legacy_task_status_label(task),
        "can_retry_analysis": perms_can(request, "agenttask", "update"),
        "running_job_id": job.id if getattr(job, "status", None) in ("pending", "running") else None,
    }


JOB_TYPE_TITLES = {
    "collect": "Сбор данных",
    "analyze": "Анализ соцсетей",
    "digest": "Дайджест",
    "prune": "Очистка",
    "learn": "Обучение",
    "reflect": "Рефлексия",
}

OUTCOME_HEADLINES = {
    # `collect`'s headline is the count of *new* items, not the platform's response
    # size: the platform re-serves the same posts on every run, so "32 collected"
    # twice in a row said nothing about whether the second run found anything.
    # The second tuple is the fallback for jobs recorded before `new_items`
    # existed — better an honest total than a "0 новых" nobody measured.
    "collect": ("Новых записей", "new_items", ("Записей собрано", "items")),
    "analyze": ("Результатов анализа", "analyzed"),
    "digest": ("Отправлено сообщений", "messages_sent"),
    "prune": ("Записей удалено", "deleted"),
}


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Russian count form: 1 запись / 2 записи / 5 записей.

    Now `deps.plural`; kept as a local alias so the call sites below read the
    way they were written.
    """
    return plural(n, one, few, many)


def _stat(value: Any, label: str) -> dict[str, Any]:
    """One stat tile: the number and the word that explains it.

    The old modal showed `источников: 2, собрано: 1, элементов: 12` — labels
    that sound like keys of a config, and `собрано` next to `элементов` reads as
    two versions of the same count. Each tile now carries its own noun so the
    number is readable without knowing the handler's dict.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return {"value": number, "label": label}


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _run_outcome(job_type: str, result: dict[str, Any]) -> str:
    """Classify a finished run: `ok` | `no_data` | `partial` | `skipped`.

    A green box reading «Новых записей: 0» looks like a failure, and a run
    where half the sources errored looks like a success — the numbers alone
    tell both lies. The outcome is what the modal's tone and label are chosen
    from, so "nothing new" and "partly broken" stop being things the reader
    has to infer from a stat tile.
    """
    if job_type == "collect":
        # Per-source failures are caught by the handler, so the job is `done`
        # even when every source failed — the outcome carries that instead.
        if _as_int(result.get("error")):
            return "partial"
        # A source deferred by a platform rate limit was not collected this run.
        if type(result.get("rate_limited")) is int and result["rate_limited"] > 0:
            return "partial"
        if result.get("dry_run"):
            return "dry_run"
        # Jobs recorded before `new_items` existed have no such key; fall back
        # to the total rather than calling a legacy run "no data".
        found = result.get("new_items", result.get("items", 0))
        return "no_data" if not _as_int(found) else "ok"
    if job_type == "analyze":
        # Only recorded nonnegative integer counters are known; never coerce
        # legacy/malformed values or add overlapping source/staged failures.
        if any(
            type(count) is int and count > 0
            for count in (
                result.get("error"),
                result.get("staged_errors"),
                result.get("collection_errors"),
                result.get("collection_rate_limited"),
            )
        ):
            return "partial"
        if result.get("dry_run"):
            return "dry_run"
        if _as_int(result.get("analyzed")):
            return "ok"
        # `skipped` conflates "no active scenario" with "nothing staged to
        # analyse"; the note below stays neutral instead of naming one cause.
        return "skipped" if _as_int(result.get("skipped")) else "no_data"
    if job_type == "prune":
        return "no_data" if not _as_int(result.get("deleted")) else "ok"
    return "ok"


_OUTCOME_LABELS = {
    "ok": "Готово",
    "no_data": "Новых данных нет",
    "partial": "Выполнено частично",
    "skipped": "Пропущено",
    "dry_run": "Тестовый прогон",
}

_OUTCOME_NOTES = {
    "collect": {
        "no_data": "Все источники ответили, но новых записей не нашлось — всё уже собрано ранее.",
        "partial": "Часть источников не ответила, требует авторизации или отложена из-за лимита запросов платформы — подробности ниже.",
        "dry_run": "Тестовый прогон: записи получены, но не сохранены; анализ не запускался.",
    },
    "analyze": {
        "no_data": "Нечего анализировать — новых данных нет.",
        "partial": "Анализ завершён частично: часть источников не удалось собрать или обработать.",
        "skipped": "Часть источников пропущена: нет активного сценария или нет накопленных записей для анализа — подробности в диагностике.",
        "dry_run": "Тестовый прогон: данные не сохранены, модель не вызывалась. Выключите «Тестовый прогон», чтобы выполнить анализ.",
    },
    "prune": {
        "no_data": "Удалять нечего — старых записей нет.",
    },
}


_ACTIVITY = {
    "collect": "Агент по очереди опрашивает источники и сохраняет новые публикации.",
    "analyze": "Агент анализирует собранные записи и готовит результаты и черновики действий.",
    "digest": "Агент собирает итоги за период и отправляет дайджест получателям.",
    "prune": "Удаляются устаревшие записи по правилам хранения данных.",
    "learn": "Агент изучает свежие результаты и обновляет свои выводы.",
    "reflect": "Агент разбирает свою недавнюю работу и сохраняет выводы.",
}
_PENDING_ACTIVITY = "Задача в очереди и начнётся, как только освободится обработчик."

_CANCEL_STOP_NOTE = "Остановка займёт до полуминуты: текущий шаг завершится, следующие не начнутся."
_CANCEL_UNCONFIRMED_NOTE = (
    "Прерванный запуск будет отмечен как неподтверждённый — проверьте результат перед повторным запуском."
)
_CANCEL_TYPE_NOTES = {
    "collect": ["Готовые анализы сохраняются в любом случае."],
    "analyze": [
        "Готовые результаты анализа сохранятся, их стоимость уже учтена.",
        "Необработанные записи останутся и будут проанализированы при следующем запуске.",
        "Подготовленные действия — черновики, сами они не выполняются.",
    ],
    "digest": ["Уже отправленные сообщения отозвать нельзя; часть получателей может не получить дайджест."],
    "prune": ["Уже удалённые записи восстановить нельзя; остальные будут удалены при следующей очистке."],
    "learn": ["Выводы, которые агент уже сохранил, останутся."],
    "reflect": ["Выводы, которые агент уже сохранил, останутся."],
}
_CONTENT_CHOICES = [
    {
        "value": "keep",
        "label": "Оставить собранные записи",
        "hint": "Рекомендуется: их обработает следующий анализ.",
    },
    {
        "value": "discard",
        "label": "Удалить собранные записи",
        "hint": (
            "Записи этого запуска, ещё не прошедшие анализ, будут удалены; " "следующий сбор сможет получить их снова."
        ),
    },
]


def _cancel_info(job: "Job") -> dict[str, Any]:
    """What the modal's «Отменить задачу» does for this job, in plain words."""
    job_type = job.job_type
    if job.status == "pending":
        if _as_int(getattr(job, "attempts", 0)):
            return {"kind": "unavailable", "reason": "Задача ждёт повторной попытки — отменить её отсюда нельзя."}
        return {
            "kind": "before_start",
            "title": "Отменить запуск?",
            "notes": ["Задача ещё не началась — ничего не будет собрано, отправлено или изменено."],
            "confirm": "Отменить запуск",
        }
    info: dict[str, Any] = {
        "kind": "running",
        "title": "Остановить задачу?",
        "notes": [_CANCEL_STOP_NOTE, *_CANCEL_TYPE_NOTES.get(job_type, []), _CANCEL_UNCONFIRMED_NOTE],
        "confirm": "Остановить задачу",
    }
    if job_type == "collect":
        info["content_question"] = "Что сделать с записями, которые этот запуск уже сохранил?"
        info["content_choices"] = _CONTENT_CHOICES
    return info


def _source_note(entry: dict[str, Any]) -> str:
    """One source's line in the modal: what this run got from it.

    `collect` rows carry the visit's outcome at the top level. `analyze` rows
    have no such field: the analysis verdict lives on the row (`analyzed`,
    `error`), the collect stage's own row is stashed under `collection`.
    Falling straight through to «без новых данных» read an analysis failure as
    "nothing was gathered" — the one thing the run did do was gather.
    """
    outcome = entry.get("outcome")
    stage = entry
    if not isinstance(outcome, str):
        collection = entry.get("collection")
        if isinstance(collection, dict) and isinstance(collection.get("outcome"), str) and collection["outcome"]:
            outcome = collection["outcome"]
            stage = collection
        else:
            outcome = "empty"
        # The analysis verdict outranks its collect stage: a row that failed
        # the analysis must not present the gathered items as the run's gain.
        if entry.get("error") is True:
            outcome = "error"
    if outcome == "error":
        note = "ошибка"
        code = entry.get("error_code") or stage.get("error_code")
        hint = entry.get("error_hint") or stage.get("error_hint")
        if code:
            note = f"ошибка · {code}"
        if hint:
            note = f"{note} · {hint}"
    elif outcome == "auth_required":
        note = "нужна авторизация"
    elif outcome == "rate_limited":
        note = "отложено: лимит запросов"
    elif outcome == "collected":
        items = int(stage.get("items") or 0)
        # Prefer the new count: "32 records" for a source that re-serves the
        # same 32 every hour reads as 32 new records, which is not what
        # happened. Older jobs have no counter — fall back to the total.
        if stage.get("new_items") is not None:
            new_items = int(stage["new_items"])
            if new_items:
                note = f"{new_items} {_plural(new_items, 'новая', 'новых', 'новых')}"
            else:
                note = "новых нет"
        else:
            note = f"{items} {_plural(items, 'запись', 'записи', 'записей')}"
    else:
        note = "без новых данных"
    # `analyzed` counts task-filtered analysis results, not all persisted rows or raw source records.
    analyzed = entry.get("analyzed")
    if outcome != "error" and analyzed:
        count = int(analyzed)
        note = f"{count} {_plural(count, 'результат', 'результата', 'результатов')} анализа"
    if stage.get("dry_run") is True or entry.get("dry_run") is True:
        note = f"тест · {note}"
    return note


_STEP_NOTES = {"running": "выполняется…", "waiting": "", "skipped": "пропущен", "stopped": "остановлено"}


def _progress_view(job: "Job", *, stopped: bool = False) -> dict[str, Any] | None:
    """Per-source progress of the current attempt, ready for the modal."""
    snapshot = read_snapshot(job.result, getattr(job, "attempts", None))
    if snapshot is None or not snapshot["total"]:
        return None
    steps = []
    raw_progress = None
    for item in snapshot["items"]:
        state = item["state"]
        if state == "running" and item.get("source_id"):
            from app.web.analysis_progress import project_job

            raw_progress = project_job(job, item["source_id"])
        if stopped and state == "running":
            state = "stopped"
        error = item.get("error") is True or item.get("outcome") == "error"
        if state == "done":
            note = "ошибка" if error else _source_note(item)
        else:
            note = _STEP_NOTES.get(state, "")
        steps.append(
            {
                "source_id": item.get("source_id"),
                "name": item["name"],
                "state": state,
                "note": note,
                "error": state == "done" and error,
            }
        )
    total, completed = snapshot["total"], snapshot["completed"]
    return {
        "phase": {
            "collection": "Сбор новых данных",
            "analysis": "Анализ данных",
        }.get(snapshot.get("phase"), "Обработка источников"),
        "total": total,
        "completed": completed,
        "percent": int(completed * 100 / total),
        "current": None if stopped else snapshot["current"],
        "steps": steps,
        "hidden_before": snapshot["first"],
        "hidden_after": max(0, total - snapshot["first"] - len(steps)),
        "raw_progress": raw_progress,
    }


def _cancelled_summary(job: "Job", title: str, task_name: str | None) -> dict[str, Any]:
    """A cancelled run is its own outcome, not an error with a raw message."""
    before_start = job.error == CANCELLED_BEFORE_START_ERROR
    result = job.result if isinstance(job.result, dict) else {}
    if before_start:
        notes = ["Задача отменена до начала — ничего не выполнялось."]
    else:
        notes = [
            "Задача остановлена по запросу.",
            "Результат прерванного запуска не подтверждён — проверьте данные перед повторным запуском.",
        ]
    cancellation = result.get("cancellation")
    if isinstance(cancellation, dict) and job.job_type == "collect":
        if cancellation.get("discard_collected") is True:
            discarded = cancellation.get("discarded")
            if type(discarded) is int and discarded >= 0:
                notes.append(
                    f"Удалено записей этого запуска: {discarded}."
                    if discarded
                    else "Записей этого запуска для удаления не нашлось."
                )
            if cancellation.get("settled") is not True:
                notes.append("Записи, сохранённые после отмены, будут удалены, когда обработчик остановится.")
        elif cancellation.get("discard_collected") is False:
            notes.append("Собранные записи сохранены — их обработает следующий анализ.")
    summary: dict[str, Any] = {
        "status": "cancelled",
        "label": "Отменено до начала" if before_start else "Задача остановлена",
        "title": title,
        "notes": notes,
    }
    progress = None if before_start else _progress_view(job, stopped=True)
    if progress is not None:
        summary["progress"] = progress
    if task_name:
        summary["task_name"] = task_name
    # A run cancelled before it started wrote nothing, so there is no raw
    # coverage to report; an operator-cancelled or failed run keeps the
    # attempt-matched snapshot the handler persisted while it ran.
    if job.job_type == "analyze" and not before_start:
        summary["raw_coverage"] = _raw_coverage(job)
    return summary


def _job_summary(job: "Job", task_name: str | None = None) -> dict[str, Any]:
    """Structured result summary for a finished job (run-now modal).

    Three parts the modal renders: what ran (`title`), the headline number
    (`headline`) and the supporting stats (`stats`) — task type and outcome only,
    no per-item detail. Source links are added separately by `job_status`, which
    has to resolve them from the database.
    """
    status = job.status
    job_type = job.job_type

    if status in ("pending", "running"):
        label = f"Выполнение задачи «{task_name}»" if task_name else "Выполнение задачи"
        summary: dict[str, Any] = {
            "status": status,
            "label": label,
            "title": JOB_TYPE_TITLES.get(job_type, job_type),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "activity": _PENDING_ACTIVITY if status == "pending" else _ACTIVITY.get(job_type, ""),
            "cancel": _cancel_info(job),
        }
        if status == "running":
            heartbeat = getattr(job, "locked_at", None)
            if heartbeat is None:
                summary["heartbeat_unconfirmed"] = True
            else:
                heartbeat = heartbeat if heartbeat.tzinfo else heartbeat.replace(tzinfo=timezone.utc)
                summary["heartbeat_unconfirmed"] = datetime.now(timezone.utc) - heartbeat.astimezone(
                    timezone.utc
                ) > timedelta(seconds=90)
        if status == "running":
            progress = _progress_view(job)
            if progress is not None:
                summary["progress"] = progress
                if job_type == "analyze" and progress.get("phase") == "collection":
                    summary["activity"] = "Агент собирает новые публикации перед анализом накопленных записей."
        # A job nobody claimed is not "running" — it is waiting for a worker.
        # Without this the modal dead-ends on its client timeout and the row
        # keeps a spinner for a job that never started, which reads as a hang.
        if status == "pending" and job.created_at:
            queued_for = datetime.now(timezone.utc) - job.created_at
            if queued_for > timedelta(minutes=2):
                summary["hint"] = (
                    "Задача ждёт в очереди — похоже, worker не запущен "
                    "(python -m app.runtime или python -m app.worker)"
                )
        return summary
    if status == "failed" and job.error in (OPERATOR_CANCELLED_ERROR, CANCELLED_BEFORE_START_ERROR):
        return _cancelled_summary(job, JOB_TYPE_TITLES.get(job_type, job_type), task_name)
    if status == "failed":
        summary = {
            "status": "failed",
            "label": "Ошибка",
            "title": JOB_TYPE_TITLES.get(job_type, job_type),
            "error": (job.error or "Неизвестная ошибка")[:300],
        }
        if job_type == "analyze":
            summary["raw_coverage"] = _raw_coverage(job)
            progress = _progress_view(job, stopped=True)
            if progress is not None:
                summary["progress"] = progress
        return summary

    result = job.result or {}
    title = JOB_TYPE_TITLES.get(job_type, job_type)

    # The headline is the number that answers "did it work?" — the one the user
    # pressed the button for. Everything else is context.
    headline = None
    if job_type in OUTCOME_HEADLINES:
        spec = OUTCOME_HEADLINES[job_type]
        word, key = spec[0], spec[1]
        if len(spec) > 2 and result.get(key) is None:
            # The counter did not exist for this job — report what *was* recorded
            # under its own name instead of inventing a zero.
            word, key = spec[2]
        try:
            count = int(result.get(key, 0) or 0)
        except (TypeError, ValueError):
            count = 0
        noun = (
            _plural(count, "результат", "результата", "результатов")
            if job_type == "analyze"
            else _plural(count, "запись", "записи", "записей")
        )
        headline = {"value": count, "label": word, "noun": noun}

    stats: list[dict[str, Any]] = []
    if job_type == "collect":
        stats = [
            _stat(result.get("sources", 0), "источников опрошено"),
            _stat(result.get("collected", 0), "ответили данными"),
            _stat(result.get("items", 0), "получено записей"),
            _stat(result.get("empty", 0), "без содержимого"),
            # `error`, not `failed`: the handler writes `error`, so this counter
            # used to read 0 for every run that actually had failures.
            _stat(result.get("error", 0), "ошибок"),
        ]
        if type(result.get("rate_limited")) is int and result["rate_limited"] > 0:
            stats.append(_stat(result["rate_limited"], "отложено (лимит запросов)"))
    elif job_type == "analyze":
        stats = [
            _stat(result.get("sources", 0), "источников проверено"),
            _stat(result.get("actions_created", 0), "действий создано"),
            _stat(result.get("skipped", 0), "пропущено"),
        ]
        if isinstance(result.get("collection"), dict):
            collection = result["collection"]
            stats.extend(
                [
                    _stat(collection.get("collected", 0), "источников собрали данные"),
                    _stat(collection.get("new_items", 0), "новых записей"),
                    _stat(collection.get("error", 0), "ошибок сбора"),
                ]
            )
        for key, label in (
            ("error", "источников с ошибками"),
            ("staged_errors", "ошибок обработки накопленных данных"),
        ):
            count = result.get(key)
            if type(count) is int and count >= 0:
                stats.append(_stat(count, label))
    elif job_type == "digest":
        stats = [_stat(result.get("period", "—"), "период")]
    elif job_type == "prune":
        stats = [_stat(result.get("deleted", 0), "записей удалено")]

    outcome = _run_outcome(job_type, result)
    summary: dict[str, Any] = {
        "status": "done",
        "label": _OUTCOME_LABELS[outcome],
        "title": title,
        "stats": stats,
        "outcome": outcome,
        "outcome_label": _OUTCOME_LABELS[outcome],
    }
    note = _OUTCOME_NOTES.get(job_type, {}).get(outcome)
    if job_type == "analyze" and outcome == "partial":
        note = (
            "Обнаружены ошибки анализа. Полнота результата не подтверждена; "
            "ошибки источников и обработки накопленных данных могут пересекаться."
        )
    if result.get("dry_run"):
        prefix = "Тестовый прогон: данные не сохранены, модель не вызывалась."
        note = f"{prefix} {note}" if note else prefix
    if note:
        summary["outcome_note"] = note
    if job_type == "analyze":
        summary["raw_coverage"] = _raw_coverage(job)
    if headline is not None:
        summary["headline"] = headline
    if task_name:
        summary["task_name"] = task_name
    return summary


def _snapshot_coverage_rows(result: dict, attempts: Any) -> list[dict]:
    """Attempt-matched persisted items as coverage rows.

    An operator-cancelled or failed mid-run `analyze` job never finalized its
    handler stats, so its `result` carries no `per_source` — only the attempt-
    matched progress snapshot it wrote while running. Those raw counters are
    what the modal should keep showing. `read_snapshot` already rejects a stale
    attempt and malformed items, so nothing here can leak a foreign generation.
    """
    snapshot = read_snapshot(result, attempts)
    rows = []
    for item in (snapshot or {}).get("items") or []:
        if not item.get("source_id"):
            continue
        rows.append(
            {
                "source_id": item["source_id"],
                "name": item.get("name"),
                "raw_total": item.get("raw_total"),
                "raw_processed": item.get("raw_processed"),
                "raw_selected": item.get("raw_selected"),
            }
        )
    return rows


def _raw_coverage(job: "Job") -> list[dict[str, Any]]:
    """Per-source persisted raw coverage; malformed or missing values stay unknown."""
    result = job.result if isinstance(job.result, dict) else {}
    if getattr(job, "error", None) == CANCELLED_BEFORE_START_ERROR:
        return []
    status = getattr(job, "status", None)
    if status == "pending":
        return []
    if status == "done":
        per_source = result.get("per_source")
        rows = per_source if isinstance(per_source, list) else []
    elif status in ("running", "failed", "cancelled"):
        rows = _snapshot_coverage_rows(result, getattr(job, "attempts", None))
    else:
        return []
    coverage = []
    for row in rows[:50]:
        if not isinstance(row, dict) or type(row.get("source_id")) is not int:
            continue
        total, processed = row.get("raw_total"), row.get("raw_processed")
        if type(total) is not int or not 0 <= total <= 10**9:
            total = None
        if type(processed) is not int or not 0 <= processed <= 10**9 or (total is not None and processed > total):
            processed = None
        percent = int(processed * 100 / total) if total and processed is not None else None
        name = row.get("name") if type(row.get("name")) is str else "Источник"
        coverage.append(
            {
                "source_id": row["source_id"],
                "name": name[:80],
                "total": total,
                "processed": processed,
                "percent": percent,
            }
        )
    return coverage


def _source_links(job: "Job", task_name: str | None = None) -> list[dict[str, Any]]:
    """Sources to jump to from the run-now modal, with what this run got from each.

    The modal is a summary; it is not where you read the data. Each entry is a
    mini-link to the source page, which shows the collected rows and — for an
    `analyze` run — the agent's per-item analysis.

    Prefer the run's own `per_source` breakdown (it is what actually ran, with
    per-source counts). Jobs written before that breakdown existed have none, so
    fall back to the task's sources with no counts: a link is still useful, a
    fabricated zero is not.
    """
    result = job.result if isinstance(job.result, dict) else {}
    rows = result.get("per_source") or []

    if not rows:
        return []

    links = []
    for entry in rows:
        source_id = entry.get("source_id")
        if not source_id:
            continue
        link = {
            "source_id": source_id,
            "name": entry.get("name") or f"Источник {source_id}",
            "note": _source_note(entry),
            # Failures surface red on the chip: collect rows mark them in the
            # outcome, analyze rows via the row-level error flag (analysis or
            # its inline collect stage).
            "error": (entry.get("outcome") or "empty") == "error" or entry.get("error") is True,
        }
        links.append(link)
    return links


def _diagnostics_view(job: "Job", source_id: int | None = None) -> dict[str, Any]:
    """Allowlisted admin-only diagnostics of the live attempt; safe for HTML/JSON.

    Only bounded correlations and timestamps, never raw provider text: the job id,
    attempt, heartbeat and progress timestamps, the selected/remaining raw counts,
    and one allowlisted error code. `job.error` is truncated and only surfaced
    when it is exactly an allowed code — never verbatim error text.
    """
    result = job.result if isinstance(job.result, dict) else {}
    status = getattr(job, "status", None)
    before_start = getattr(job, "error", None) == CANCELLED_BEFORE_START_ERROR
    progress = None if status == "pending" or before_start else read_snapshot(result, getattr(job, "attempts", None))
    per_source = result.get("per_source") if isinstance(result.get("per_source"), list) else []
    snapshot_rows = [] if before_start else _snapshot_coverage_rows(result, getattr(job, "attempts", None))
    if status == "done":
        if source_id is not None:
            current = next(
                (row for row in per_source if isinstance(row, dict) and row.get("source_id") == source_id), {}
            )
            if not current:
                current = next((row for row in snapshot_rows if row.get("source_id") == source_id), {})
        else:
            current = {}
            if per_source:
                final_rows = [row for row in per_source if isinstance(row, dict)]
                selected_rows = [row.get("raw_selected") for row in final_rows]
                remaining_rows = [
                    row.get("raw_total") - row.get("raw_processed")
                    for row in final_rows
                    if type(row.get("raw_total")) is int
                    and type(row.get("raw_processed")) is int
                    and 0 <= row["raw_processed"] <= row["raw_total"] <= 10**9
                ]
                current = {
                    "raw_selected": (
                        sum(selected_rows)
                        if selected_rows and all(type(v) is int and 0 <= v <= 10**9 for v in selected_rows)
                        else None
                    ),
                    "remaining": (
                        sum(remaining_rows)
                        if len(final_rows) == len(per_source) and len(remaining_rows) == len(final_rows)
                        else None
                    ),
                }
    elif status == "pending":
        # A retry remains at the prior attempt number until claimed. Its saved
        # snapshot is therefore not current progress even if attempt numbers match.
        current = {}
    elif source_id is not None:
        current = next((row for row in snapshot_rows if row.get("source_id") == source_id), {})
    else:
        items = (progress or {}).get("items", [])
        current = next((row for row in items if row.get("state") == "running"), {})
        if not current:
            current = next((row for row in reversed(items) if row.get("raw_selected") is not None), {})
    allowed_codes = {"storage_operation_failed", "provider_failed", "timeout", "cancelled", "unknown"}
    error = str(getattr(job, "error", "") or "")[:80]
    code = error if error in allowed_codes else ("unknown" if error else None)
    if code is None:
        # The run's own LLM journal outcome (failed_transport / failed_output),
        # written into the per-source snapshot by the handler. Identifier-shaped
        # values only: diagnosis codes, never provider or exception text.
        run_code = current.get("error_code") if isinstance(current, dict) else None
        if isinstance(run_code, str) and run_code.isidentifier() and len(run_code) <= 64:
            code = run_code
    selected = current.get("raw_selected")
    if type(selected) is not int or not 0 <= selected <= 10**9:
        selected = None
    updated_at = (progress or {}).get("updated_at")
    try:
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        updated_at = updated_at.astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError):
        updated_at = None
    total, processed = current.get("raw_total"), current.get("raw_processed")
    remaining = current.get("remaining")
    if (
        remaining is None
        and type(total) is int
        and type(processed) is int
        and 0 <= total <= 10**9
        and 0 <= processed <= total
    ):
        remaining = total - processed
    if type(remaining) is not int or not 0 <= remaining <= 10**9:
        remaining = None
    locked_at = getattr(job, "locked_at", None)
    if isinstance(locked_at, datetime):
        locked_at = locked_at if locked_at.tzinfo else locked_at.replace(tzinfo=timezone.utc)
        heartbeat_at = locked_at.astimezone(timezone.utc).isoformat()
    else:
        heartbeat_at = None
    job_id = getattr(job, "id", None)
    if type(job_id) is not int or not 0 < job_id <= 10**9:
        job_id = None
    attempt = getattr(job, "attempts", None)
    if type(attempt) is not int or not 0 <= attempt <= 10**9:
        attempt = None
    # Per-source bounded causes. A collection failure is recorded as a platform
    # code (`api_15`) plus an allowlisted hint; raw provider text stays out. An
    # `empty` outcome is included too: "no posts in the window" is the answer an
    # operator is looking for when nothing was collected.
    source_errors: list[dict[str, Any]] = []
    for row in (per_source or snapshot_rows)[:20]:
        if not isinstance(row, dict):
            continue
        collection = row.get("collection") if isinstance(row.get("collection"), dict) else {}
        outcome = row.get("outcome") or collection.get("outcome")
        # A row-level code, never the job-level one: reusing `code` here would
        # overwrite the run's own diagnosis with the last source's.
        row_code = row.get("error_code") or collection.get("error_code")
        hint = row.get("error_hint") or collection.get("error_hint") or row.get("auth_hint")
        if not hint and outcome == "empty":
            hint = "нет записей в выбранном окне"
        if not hint and outcome == "rate_limited":
            hint = "платформа просит повторить позже"
        name = row.get("name") if type(row.get("name")) is str else "Источник"
        items = row.get("items", collection.get("items"))
        new_items = row.get("new_items", collection.get("new_items"))
        selection = collection.get("selection") if isinstance(collection.get("selection"), dict) else {}
        eligible = selection.get("eligible")
        rejected = selection.get("rejected")
        source_errors.append(
            {
                "source_id": row.get("source_id") if type(row.get("source_id")) is int else None,
                "name": name[:80],
                "outcome": outcome if isinstance(outcome, str) else None,
                "error_code": row_code[:64] if isinstance(row_code, str) else None,
                "error_hint": hint[:120] if isinstance(hint, str) else None,
                "items": items if type(items) is int and 0 <= items <= 10**9 else None,
                "new_items": new_items if type(new_items) is int and 0 <= new_items <= 10**9 else None,
                "eligible": eligible if type(eligible) is int and 0 <= eligible <= 10**9 else None,
                "rejected": rejected if type(rejected) is int and 0 <= rejected <= 10**9 else None,
            }
        )
    return {
        "job_id": job_id,
        "attempt": attempt,
        "heartbeat_at": heartbeat_at,
        "progress_updated_at": updated_at,
        "selected": selected,
        "remaining": remaining,
        "error_code": code,
        "source_errors": source_errors,
    }


def _diagnostics_admin(request: Request) -> bool:
    """Diagnostics need a valid workspace membership with the admin role, or a platform admin.

    Workspace *owner alone* is insufficient: the owner is not necessarily an
    admin, and analytics detail is an explicit role grant, not an implicit
    side effect of ownership. The membership list is the middleware-resolved
    `tenant_users` web memberships, so `channel`/`is_active` already hold; the
    role still has to be checked per-membership, never via a generic grant.
    """
    user = getattr(request.state, "web_user", None)
    if user is None or not getattr(user, "is_active", False):
        return False
    tenant_id = getattr(request.state, "tenant_id", None)
    if type(tenant_id) is not int or tenant_id <= 0:
        return False
    user_id = getattr(user, "id", None)
    if type(user_id) is not int or user_id <= 0:
        return False
    valid_membership = False
    workspace_admin = False
    for membership in getattr(request.state, "memberships", None) or []:
        if (
            getattr(membership, "tenant_id", None) == tenant_id
            and getattr(membership, "user_id", None) == user_id
            and getattr(membership, "channel", None) == "web"
            and getattr(membership, "is_active", None) is True
            and getattr(membership, "role", None) is not None
        ):
            valid_membership = True
            workspace_admin = str(getattr(membership.role, "codename", "")) == "admin"
            break
    return valid_membership and (workspace_admin or bool(getattr(user, "is_platform_admin", False)))


async def _task_source_ids(task_id: int) -> list[int]:
    """Return the source ids linked to a task via the m2m table.

    Two mistakes lived here, and both were fatal to *every* task page —
    `/app/tasks/{id}` raised 500 for any task at all, so the queue's link to a
    task had nothing to land on:

    * the join column is `agent_task_id` (see `agent_task_sources`), not
      `task_id` — the rest of the codebase spells it the same way as here
      (`AgentTaskManager`), this was the lone outlier;
    * `agent_task_sources` is a declarative `Table`, so `.select()` produces a
      core `Select` — there is no `.fetchall()` on it.

    Rather than reach for the table again, read the relationship the model
    already declares (`AgentTask.sources`, `secondary=agent_task_sources`): the
    secondary table stays an implementation detail of the mapping, and the
    tenant guard of `AgentTask.objects` still applies to the task we load.
    `prefetch_related` is not optional here — `sources` is a lazy m2m, and the
    row comes back detached, so touching it without a prefetch raises
    `DetachedInstanceError` rather than issuing a query.
    """
    from app.models.scheduling.agent_task import AgentTask

    task = await AgentTask.objects.filter(id=task_id).prefetch_related("sources").first()
    return [source.id for source in task.sources] if task else []


__all__ = [name for name in globals() if not name.startswith("__")]
