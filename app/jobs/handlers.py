"""Background job handlers. Heavy work runs here (worker process)."""

import logging
import math
from typing import TYPE_CHECKING, Any

from app.services.social.credentials import AuthorizationRequired

# Selection is a task property applied to staged content; `handlers` owns when
# it runs, `selection` owns what it means (docs/design/task-selection.md).
from app.services.monitoring import selection

if TYPE_CHECKING:
    from app.types import AgentActionType

logger = logging.getLogger(__name__)


def _log_id(value: Any) -> int | None:
    """Only persisted integer IDs; never stringify payload-like values."""
    return value if type(value) is int and 0 < value <= 2**63 - 1 else None


def _fresh_analytics_ids(rows: Any) -> list[int]:
    """Return a bounded allowlist of persisted IDs from this analyzer result."""
    if not isinstance(rows, (list, tuple)):
        return []
    ids: list[int] = []
    seen: set[int] = set()
    for row in rows:
        analytics_id = _log_id(getattr(row, "id", None))
        if analytics_id is None or analytics_id in seen:
            continue
        ids.append(analytics_id)
        seen.add(analytics_id)
        if len(ids) >= ANALYZE_STAGE_BATCH:
            break
    return ids


def _error_kind(error: BaseException) -> str:
    """Bounded categories, not exception messages, repr, args or tracebacks."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "connection_error"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, ValueError):
        return "value_error"
    if isinstance(error, RuntimeError):
        return "runtime_error"
    return "unexpected_error"


# A platform error code is a bounded, meaningful cause an operator can act on.
# The raw provider text is not stored or rendered; only the numeric code and a
# short hint from this allowlist reach the admin diagnostics.
_PROVIDER_ERROR_HINTS = {
    "5": "токен недействителен",
    "6": "слишком много запросов в секунду",
    "9": "flood control",
    "14": "требуется капча",
    "15": "доступ к стене только у участников",
    "18": "страница удалена",
    "29": "исчерпан лимит метода",
    "100": "неверные параметры запроса",
    "214": "доступ к альбому ограничен",
    "1051": "метод недоступен для типа приложения",
}


def _provider_error(error: BaseException) -> tuple[str, str | None]:
    """`(code, hint)` for a collection failure, safe for the admin panel."""
    import re

    if isinstance(error, AuthorizationRequired):
        return "auth_required", "нужна авторизация"
    match = re.search(r"Ошибка API \((\d+)\)", str(error))
    if match:
        code = match.group(1)
        return f"api_{code}", _PROVIDER_ERROR_HINTS.get(code, "ошибка платформы")
    return _error_kind(error), None


def _task_plan_platforms(plan: dict[str, Any]) -> set[str] | None:
    """Return a validated platform allowlist, or None for malformed plans."""
    raw = plan.get("platforms")
    if not isinstance(raw, list) or not raw or any(
        not isinstance(value, str) or value not in {"vk", "telegram"} for value in raw
    ):
        return None
    return set(raw)


def _source_in_task_platform_scope(source: Any, platforms: set[str]) -> bool:
    platform = getattr(source, "platform", None)
    kind = getattr(platform, "platform_type", None)
    key = getattr(kind, "db_value", kind)
    return isinstance(key, str) and key.lower() in platforms


async def _load_task(task_id: int | None):
    """Load the AgentTask (with its m2m sources) that triggered this job, if any.

    Handlers run inside the job's workspace scope, so this lookup is filtered by
    tenant. A task that exists but lives in *another* workspace is a broken
    cross-workspace link: returning None would let `_resolve_sources` fall back
    to "all active sources" and quietly process the wrong workspace instead of
    reporting the problem.
    """
    if not task_id:
        return None
    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask

    task = await (
        AgentTask.objects.filter(id=task_id).prefetch_related("sources", "sources.platform", "agent_scenario").first()
    )
    if task is not None:
        return task
    with tenant_scope(bypass=True):
        other = await AgentTask.objects.get(id=task_id)
    if other is not None:
        raise ValueError(f"Task {task_id} belongs to workspace {other.tenant_id}, not to the job's workspace")
    logger.warning(f"Job points at task {task_id}, which no longer exists; falling back to payload sources")
    return None


async def _resolve_sources(
    task,
    payload: dict[str, Any] | None = None,
    *,
    discovered_source_ids: list[int] | tuple[int, ...] | None = None,
) -> list:
    """Sources a job should operate on.

    Sources come from the task's m2m `sources`; an empty set means all active
    sources. When no task triggered the job, fall back to the legacy
    `payload["source_ids"]` if present, else all active sources.
    """
    from app.models import Source

    if task is not None:
        # `sources` is a plain m2m, so the prefetch is not tenant-filtered: keep
        # only the task's own workspace (link writes validate this, this is the
        # runtime backstop for rows created before that check existed).
        task_sources = [s for s in (task.sources or []) if s.tenant_id == task.tenant_id]
        foreign = [s.id for s in (task.sources or []) if s.tenant_id != task.tenant_id]
        if foreign:
            logger.warning(f"Task {task.id} links foreign source(s) {foreign}; skipping them")
        task_payload = _task_payload(task)
        plan = task_payload.get("task_plan")
        scope = plan.get("source_scope") if isinstance(plan, dict) else None
        if scope in {"selected", "discovery", "all"}:
            platforms = _task_plan_platforms(plan)
            if platforms is None:
                logger.warning(f"Task {task.id} has invalid platform scope; refusing collection")
                return []
            raw_snapshot = (payload or {}).get("source_ids") or []
            if not isinstance(raw_snapshot, list) or any(
                type(value) is not int or value <= 0 for value in raw_snapshot
            ):
                logger.warning(f"Task {task.id} has invalid admitted source snapshot; refusing collection")
                return []
            allowed = set(raw_snapshot)
            allowed.update(value for value in discovered_source_ids or () if type(value) is int and value > 0)
            if scope == "selected":
                raw_selected = plan.get("source_ids")
                if not isinstance(raw_selected, list) or any(type(value) is not int for value in raw_selected):
                    logger.warning(f"Task {task.id} has invalid selected source scope; refusing collection")
                    return []
                if not set(raw_selected).issubset(allowed):
                    logger.warning(
                        f"Task {task.id} selected sources are outside its admitted snapshot; refusing collection"
                    )
                    return []
            # Admission snapshots bind the ordinary scope. This run may add
            # only exact sources returned by its own population/mention steps;
            # current m2m links and workspace contents cannot widen the job.
            if not allowed:
                return []
            rows = list(
                await Source.objects.filter(is_active=True, id__in=allowed, tenant_id=task.tenant_id)
                .select_related("platform")
            )
            return [source for source in rows if _source_in_task_platform_scope(source, platforms)]
        logger.info(f"_resolve_sources: task={task.id} has {len(task_sources)} sources: {[s.id for s in task_sources]}")
        if task_sources:
            return task_sources
        logger.info("_resolve_sources: task has no sources, falling back to all active")
        # A task-scoped query lane is owned by its task and must never leak into
        # another task's "all active sources" shortcut (or a taskless run).
        return list(
            await Source.objects.filter(is_active=True, owner_task_id__isnull=True).select_related("platform")
        )

    source_ids = (payload or {}).get("source_ids") or []
    plan = (payload or {}).get("task_plan")
    scope = plan.get("source_scope") if isinstance(plan, dict) else None
    if scope in {"selected", "discovery", "all"}:
        platforms = _task_plan_platforms(plan)
        if platforms is None:
            return []
        # A detached replay has no task relationship to re-resolve. Its source
        # IDs were captured by admission; do not widen them using today's task
        # links, generated lanes, or workspace sources.
        allowed_ids = {value for value in source_ids if type(value) is int and value > 0}
        if scope == "selected":
            selected = plan.get("source_ids")
            if not isinstance(selected, list) or any(type(value) is not int or value <= 0 for value in selected):
                return []
            if not set(selected).issubset(allowed_ids):
                return []
        allowed_ids.update(value for value in discovered_source_ids or () if type(value) is int and value > 0)
        if not allowed_ids:
            return []
        rows = list(
            await Source.objects.filter(is_active=True, id__in=allowed_ids).select_related("platform")
        )
        return [source for source in rows if _source_in_task_platform_scope(source, platforms)]
    if source_ids:
        return list(await Source.objects.filter(id__in=source_ids).select_related("platform"))
    return list(await Source.objects.filter(is_active=True, owner_task_id__isnull=True).select_related("platform"))


def _task_payload(task) -> dict[str, Any]:
    """Payload dict of the task (or empty if there is no task)."""
    return dict(task.payload or {}) if task is not None else {}


def _effective_replay_snapshot(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Admission-time settings required to replay a source job safely."""
    if not payload.get("retry_of_job_id") or payload.get("agent_task_id") is not None:
        return None
    snapshot = payload.get("_effective_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        return None
    task_payload = snapshot.get("task_payload")
    source_ids = task_payload.get("source_ids") if isinstance(task_payload, dict) else None
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or any(type(value) is not int or value <= 0 for value in source_ids)
        or len(source_ids) != len(set(source_ids))
    ):
        return None
    from app.jobs.replay_policy import _has_replay_window

    if not _has_replay_window(task_payload):
        return None
    return snapshot


def _replay_task_view(snapshot: dict[str, Any]):
    """Task-shaped view for saved trigger settings during a detached replay."""
    from types import SimpleNamespace

    from app.types import BotTriggerType

    trigger_type = BotTriggerType.get_by_name(snapshot.get("trigger_type")) if snapshot.get("trigger_type") else None
    return SimpleNamespace(
        id=None,
        tenant_id=None,
        trigger_type=trigger_type,
        trigger_config=snapshot.get("trigger_config") or {},
        action_type=None,
        rate_limit_per_hour=None,
        cooldown_seconds=None,
        dry_run=bool(snapshot.get("dry_run")),
        sources=[],
        agent_scenario=None,
    )


def _resolve_content_window(task_payload: dict[str, Any], payload: dict[str, Any], source) -> dict[str, Any]:
    """The content window (cli_dates) for a run, resolved once and applied at every stage.

    One rule everywhere — scheduled or forced: the window is the most specific
    `cli_dates` available, in priority order:
      1. job payload override (per-run, e.g. CLI `--start-date` / run-now flags)
      2. task payload (the task's configured window)
      3. source params (source-level fallback)

    Returns a dict with parsed `start_date`/`end_date` `date` objects (or None).
    An empty result means "no window" — collection falls back to incremental
    (last_checked) and analysis drains whatever is staged.
    """
    from app.models.managers.agent_task_manager import AgentTaskManager

    cli_dates = (
        (payload or {}).get("cli_dates")
        or (task_payload or {}).get("cli_dates")
        or (getattr(source, "params", None) or {}).get("cli_dates")
    )
    if not cli_dates:
        return {}
    start = AgentTaskManager.parse_date(cli_dates.get("start_date"))
    end = AgentTaskManager.parse_date(cli_dates.get("end_date"))
    return {"start_date": start, "end_date": end}


# How many staged raw items one analyze run takes per source. A collect run
# stages everything the platform returned, so this is the cap that keeps a
# single pass bounded and lets the remainder roll into the next run.
ANALYZE_STAGE_BATCH = 100


def _resolve_action_type(task) -> "AgentActionType | None":
    """The action this task performs, or None when it performs none.

    `action_type` moved from the scenario to the task, so a task that was never
    configured has no action to take. Returning None (rather than defaulting to
    COMMENT) keeps "never configured" from looking like "comment on everything":
    a default here would create BotAction rows the owner never asked for.
    """
    from app.types import AgentActionType

    return getattr(task, "action_type", None) if task is not None else None


async def _retire_staged(
    analytics: Any,
    source_id: int,
    tenant_id: int | None = None,
    completed_hashes: set[str] | None = None,
    analysis_fingerprint: str | None = None,
) -> int:
    """Drop the raw rows whose content these saved analyses actually cover.

    Keyed on the stored item hashes rather than on the job that fetched them,
    which is what makes the promise "the raw copy goes only after a successful
    save" hold in the cases a run id cannot express: rows staged by an API or
    CLI collection carry no job at all, and a partially successful analysis
    covers only part of the batch.

    Only ever called on the success path, and never raises — leftovers are swept
    by the retention pass in `handle_prune`.
    """
    from app.core.database import new_session
    from app.models import CollectedItem
    from app.services.ai.dedup import analysed_hashes

    hashes = analysed_hashes(analytics) | (completed_hashes or set())
    if not hashes:
        return 0
    session = new_session()
    try:
        async with session.begin():
            if tenant_id is None and not completed_hashes and not analysis_fingerprint:
                # Compatibility for diagnostic callers; production handler
                # always supplies the explicit source tenant and context.
                return await CollectedItem.objects.delete_hashes(session, source_id, hashes)
            return await CollectedItem.objects.delete_hashes(
                session,
                source_id,
                hashes,
                tenant_id=tenant_id,
                analysis_fingerprint=analysis_fingerprint,
            )
    except Exception:  # noqa: BLE001 — leftovers are swept by the retention pass
        # Log only bounded correlation, never storage errors, SQL or raw content.
        safe_source_id = source_id if type(source_id) is int and 0 < source_id <= 2**63 - 1 else None
        logger.warning(
            "staged_retirement_failed source_id=%s error_code=storage_operation_failed",
            safe_source_id,
        )
        return 0
    finally:
        await session.close()


async def _count_failed_staged(source_id: int, staged: list, completed_hashes: set[str] | None = None) -> int:
    """Count one failed analysis attempt against the staged rows that produced nothing.

    The counterpart to `_retire_staged`: a row that was analysed but saved
    nothing (an LLM timeout stores no `content_hash`, so it is never retired)
    would otherwise be handed out again on every run, each time burning a full
    request timeout and stopping the batch from draining. After
    `give_up_after_attempts` such failures `for_source` stops offering the row.

    Increments only — a row that keeps failing stays on disk, because it still
    holds the only copy of its content and `handle_prune` is what reclaims it.
    Never raises: losing the count would only mean the old retry behaviour.
    """
    from app.core.database import new_session
    from app.models import CollectedItem

    completed_hashes = completed_hashes or set()
    hashes = [
        row.content_hash for row in staged
        if getattr(row, "content_hash", None) and row.content_hash not in completed_hashes
    ]
    if not hashes:
        return 0
    session = new_session()
    try:
        async with session.begin():
            return await CollectedItem.objects.record_attempts(session, source_id, hashes)
    except Exception:  # noqa: BLE001 — a lost count only restores the old retry loop
        # Keep SQL, raw content and exception details out of this warning.
        safe_source_id = source_id if type(source_id) is int and 0 < source_id <= 2**63 - 1 else None
        logger.warning(
            "staged_attempt_count_failed source_id=%s error_code=storage_operation_failed",
            safe_source_id,
        )
        return 0
    finally:
        await session.close()


async def _llm_failure_code(source_id: int, since: Any) -> str | None:
    """Outcome of the newest failed text-analysis LLM attempt since `since`.

    Reads the immutable `llm_attempts` journal — the accounting evidence — so a
    failed run can show WHY on the source page («failed_transport» = the provider
    did not answer, «failed_output» = the answer was unusable) instead of a bare
    "they did not get processed". Read-only and bounded; diagnostics is a display
    hint, so any failure of this lookup never fails the run and yields no code.
    """
    from datetime import datetime, timezone

    if isinstance(since, datetime):
        since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    else:
        return None
    from app.models.llm.llm_attempt import LLMAttempt

    try:
        attempt = await (
            LLMAttempt.objects.filter(source_id=source_id, stage="text_analysis", created_at__gte=since)
            .order_by(LLMAttempt.id.desc())
            .limit(1)
            .first()
        )
        outcome = getattr(attempt, "outcome", None)
    except Exception:  # noqa: BLE001 — diagnostics must never fail the run
        return None
    if isinstance(outcome, str) and outcome and outcome != "succeeded" and outcome.isidentifier():
        return outcome
    return None


def _population_platform(task, task_payload: dict[str, Any] | None = None) -> str:
    """The platform the population search runs on.

    The discovery block declares no platform of its own, so the task's linked
    sources decide; VK is the only provider whose adapter can search people
    and communities, so its presence wins and an unlinked task defaults to it.
    """
    from app.services.collection.context import platform_name

    task_plan = (task_payload or {}).get("task_plan")
    if isinstance(task_plan, dict):
        platforms = _task_plan_platforms(task_plan)
        if platforms:
            # VK supports the current people/community discovery adapter. If
            # only another platform was requested, pass that platform through
            # so population reports unsupported without probing VK out of scope.
            return "vk" if "vk" in platforms else sorted(platforms)[0]
    names: list[str] = []
    for source in getattr(task, "sources", None) or []:
        name = platform_name(source)
        if name and name not in names:
            names.append(name)
    if "vk" in names:
        return "vk"
    return names[0] if names else "vk"


def _population_note(summary: dict[str, Any]) -> str:
    """One bounded operator sentence for the population step's evidence."""
    code = summary.get("error_code") or summary.get("unsupported_reason")
    if code:
        hint = summary.get("error_hint") or "причина неизвестна"
        return f"Поиск людей и сообществ не выполнен: {hint} ({code})"[:200]
    found = int(summary.get("found") or 0)
    if summary.get("dry_run"):
        return f"Поиск нашёл {found} кандидатов; сухой прогон ничего не сохраняет"[:200]
    return (
        f"Поиск нашёл {found} кандидатов, источников создано "
        f"{int(summary.get('created') or 0)}, переиспользовано "
        f"{int(summary.get('reused') or 0)}"
    )[:200]


async def _run_population_step(
    task,
    *,
    task_payload: dict[str, Any],
    dry_run: bool,
    job_id: int | None,
) -> dict[str, Any] | None:
    """Resolve the task's declared population into lanes before collection.

    The task's own intent says which entities to look for (a phrase plus the
    kinds and the city); every found entity becomes a task-owned wall lane, so
    the source read that follows picks the lanes up in the same run. Returns a
    bounded evidence summary, or None when the task declares no population.
    Never raises: a blocked or failed search is a NAMED result — one
    `collection_lane_runs` row with the code and a bounded note — and the rest
    of the collect run continues with the sources that are already readable.
    """
    from datetime import datetime, timezone

    from app.services.collection import lane_runs
    from app.services.collection.intent import CollectionIntentError, parse_collection_intent
    from app.services.collection.population import (
        CANDIDATE_SEARCH_LANE_TYPE,
        PopulationError,
        resolve_population,
    )

    task_id = _log_id(getattr(task, "id", None))
    try:
        intent = parse_collection_intent(task_payload)
    except CollectionIntentError as error:
        # The per-source stages report the same malformed block; the population
        # step just declines to guess a population from it.
        logger.warning(
            "collect_population_intent_invalid task_id=%s error_code=%s",
            task_id,
            str(error),
        )
        return None
    discovery = intent.discovery
    if not discovery.queries or not (discovery.communities or discovery.people):
        return None

    platform_key = _population_platform(task, task_payload)
    started_at = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "ran": True,
        "dry_run": dry_run,
        "platform": platform_key,
        "queries": list(discovery.queries)[:10],
        "found": 0,
        "created": 0,
        "reused": 0,
        "linked": 0,
        "source_ids": [],
        "would_create": 0,
        "refused": [],
        "warnings": [],
        "city": None,
        "per_phrase": [],
        "error_code": None,
        "error_hint": None,
        "unsupported_reason": None,
        "note": "",
    }

    async def _record(**values: Any) -> int | None:
        try:
            return await lane_runs.record_lane_run(
                tenant_id=int(task.tenant_id),
                job_id=job_id,
                task_id=task_id,
                lane_id=f"population:{platform_key}"[:100],
                lane_type=CANDIDATE_SEARCH_LANE_TYPE,
                limit_count=intent.candidates.limit,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                **values,
            )
        except Exception as record_error:  # noqa: BLE001 — evidence never fails the run
            logger.warning(
                "population_evidence_failed task_id=%s error_code=%s",
                task_id,
                _error_kind(record_error),
            )
            return None

    try:
        result = await resolve_population(
            task=task,
            intent=intent,
            platform=platform_key,
            dry_run=dry_run,
            job_id=job_id,
        )
    except PopulationError as error:
        code = error.code
        is_unsupported = code == "population_search_unsupported"
        row_id = await _record(
            query=error.query or _bounded_query(discovery.queries),
            fetched=0,
            # An unsupported search is a capability answer, not an API failure.
            error_code=None if is_unsupported else code,
            unsupported_reason=code if is_unsupported else None,
        )
        summary.update(
            error_code=None if is_unsupported else code,
            error_hint=error.hint,
            unsupported_reason=code if is_unsupported else None,
            lane_run_id=row_id,
            note=_population_note({"error_code": code, "error_hint": error.hint}),
        )
        return summary
    except Exception as error:  # noqa: BLE001 — a population failure must not fail the run
        code = _error_kind(error)
        row_id = await _record(
            query=_bounded_query(discovery.queries),
            fetched=0,
            error_code=code,
        )
        summary.update(
            error_code=code,
            lane_run_id=row_id,
            note=_population_note({"error_code": code, "error_hint": None}),
        )
        return summary

    summary.update(
        city=(
            {"name": result.city_name, "id": result.city_id}
            if result.city_id is not None or result.city_name is not None
            else None
        ),
        found=len(result.entities),
        per_phrase=[
            {"query": phrase.query, "communities": phrase.communities, "people": phrase.people}
            for phrase in result.per_phrase[:10]
        ],
        created=len(result.created_source_ids),
        reused=len(result.reused_source_ids),
        linked=len(result.linked_source_ids),
        # `linked_source_ids` is the task's complete m2m list. Discovery must
        # contribute only the identities matched by this population request.
        source_ids=list(dict.fromkeys((*result.created_source_ids, *result.reused_source_ids)))[:500],
        would_create=len(result.would_create),
        refused=list(result.refused[:5]),
        warnings=list(result.warnings[:10]),
    )
    if result.dry_run:
        # A rehearsal stores nothing; the note carries the find count so the
        # evidence says what a real run would have created.
        summary["note"] = _population_note({"dry_run": True, "found": len(result.entities)})
    else:
        summary["note"] = _population_note(summary)
    row_id = await _record(query=_bounded_query(discovery.queries), fetched=summary["found"])
    if row_id:
        summary["lane_run_id"] = row_id
    return summary


def _bounded_query(queries: tuple[str, ...]) -> str | None:
    """One bounded query value for the evidence row of a multi-phrase step."""
    if not queries:
        return None
    if len(queries) == 1:
        return queries[0]
    return " | ".join(queries)[:500]


async def _run_mention_lanes_step(
    task,
    *,
    task_payload: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any] | None:
    """Ensure the task's own mention phrases exist as task-owned search lanes.

    The task's «Что искать» phrases (`payload["collection"].queries`) become
    SEARCH lanes linked to the task — the same rows `cli collection lanes`
    makes by hand — so the run that follows reads them without a manual step.
    The rules stay with the reviewed `ensure_query_lanes`: an existing lane is
    reused, another task's lane is skipped with a bounded note, nothing is
    ever deleted, and a dry run creates nothing and only reports the phrases
    without a lane. Never raises: a blocked ensure is a bounded note and the
    collection continues with the sources already linked.
    """
    from app.services.collection.intent import CollectionIntentError, parse_collection_intent
    from app.services.collection.lane_migration import LaneMigrationError, ensure_query_lanes
    from app.services.collection.mention_lanes import mention_lane_queries, mention_note
    from app.services.collection.planner import query_source_spec

    task_id = _log_id(getattr(task, "id", None))
    if task_id is None:
        return None
    try:
        intent = parse_collection_intent(task_payload)
    except CollectionIntentError as error:
        # The per-source stages report the same malformed block; the lane step
        # declines to guess lanes from it.
        logger.warning(
            "collect_mention_intent_invalid task_id=%s error_code=%s",
            task_id,
            str(error),
        )
        return None
    phrases = mention_lane_queries(intent)
    if not phrases:
        return None
    platform_keys = _task_plan_platforms(task_payload.get("task_plan", {}))
    if not platform_keys:
        platform_keys = {_population_platform(task, task_payload)}
    summary: dict[str, Any] = {
        "ran": True,
        "dry_run": dry_run,
        "platform": next(iter(platform_keys)) if len(platform_keys) == 1 else None,
        "platforms": sorted(platform_keys),
        "match": intent.queries_match,
        "queries": list(phrases)[:10],
        "total": len(phrases),
        "created": 0,
        "reused": 0,
        "linked": 0,
        "refused": [],
        "missing": [],
        "error_code": None,
        "error_hint": None,
        "note": "",
    }
    try:
        created = reused = linked = 0
        refused: list[str] = []
        missing: list[str] = []
        source_ids: list[int] = []
        for platform_key in sorted(platform_keys):
            specs = [query_source_spec(task_id=int(task.id), platform=platform_key, query=phrase) for phrase in phrases]
            result = await ensure_query_lanes(task, specs, platform=platform_key, dry_run=dry_run)
            created += len(result.created_source_ids)
            reused += len(result.reused_source_ids)
            linked += len(result.linked_source_ids)
            source_ids.extend((*result.created_source_ids, *result.reused_source_ids))
            refused.extend(result.refused[: max(0, 5 - len(refused))])
            missing.extend(result.missing[: max(0, 10 - len(missing))])
    except LaneMigrationError as error:
        summary.update(error_code="lane_migration_error", error_hint=" ".join(str(error).split())[:160])
        summary["note"] = mention_note(summary)
        return summary
    except Exception as error:  # noqa: BLE001 — lane provisioning must not fail the run
        summary.update(error_code=_error_kind(error))
        summary["note"] = mention_note(summary)
        return summary
    summary.update(
        created=created,
        reused=reused,
        linked=linked,
        source_ids=list(dict.fromkeys(source_ids))[:500],
        refused=refused,
        missing=missing,
    )
    summary["note"] = mention_note(summary)
    return summary


def _lane_identity(source: Any) -> tuple[str, str, str | None] | None:
    """The planner lane a source reports its per-run evidence under.

    One classification, the planner's own: wall types (group/channel/page/
    user/chat) report `wall_posts`, a SEARCH source reports `post_search`
    with its phrase; the collector's special non-post types (album, market,
    broadcast, ...) model no post lane and report none.
    """
    from app.services.collection.planner import (
        SEARCH_SOURCE_TYPE,
        WALL_SOURCE_TYPES,
        _enum_value,
        _query_value,
    )

    # The classification reads the source defensively: evidence must never
    # fail a run, and a source without a readable type simply has no lane.
    source_type = _enum_value(getattr(source, "source_type", None)) or ""
    source_type = str(source_type).lower()
    platform = getattr(source, "platform", None)
    platform = str(_enum_value(getattr(platform, "platform_type", platform)) or "").lower()
    source_id = int(getattr(source, "id", 0) or 0)
    external_id = str(getattr(source, "external_id", "") or "")
    params = dict(getattr(source, "params", None) or {})
    if source_type == SEARCH_SOURCE_TYPE:
        query = _query_value(params.get("query") or external_id) or None
        return f"search:{platform}:source:{source_id}", "post_search", query
    if source_type in WALL_SOURCE_TYPES:
        return f"wall:{platform}:{source_id}", "wall_posts", None
    return None


async def _record_lane_evidence(
    *,
    source: Any,
    per: dict[str, Any],
    task_id: int | None,
    job_id: int | None,
    limit_count: int | None,
) -> None:
    """One `collection_lane_runs` row for one collected source, beside
    the per-source counters. Fetched and bound count parent posts; selection
    outcomes count selected records, including comments. A failure carries
    the same bounded code/hint mapping the run already
    reports — never raw provider text. Evidence never fails the run and
    writes no content.
    """
    from app.services.collection import lane_runs
    from app.services.monitoring.selection_store import source_limit

    tenant_id = getattr(source, "tenant_id", None)
    identity = _lane_identity(source)
    if identity is None or tenant_id is None:
        return
    lane_id, lane_type, query = identity
    parent_count = per.get("parent_count")
    fetched = int(parent_count if type(parent_count) is int else per.get("items") or 0)
    applied_limit = per.get("limit_count")
    has_applied_limit = type(applied_limit) is int and applied_limit > 0
    if not has_applied_limit:
        applied_limit = source_limit(source, limit_count)
    applied_limit_reached = per.get("limit_reached") is True if has_applied_limit else False
    try:
        await lane_runs.record_lane_run(
            tenant_id=int(tenant_id),
            job_id=job_id,
            task_id=task_id,
            lane_id=lane_id,
            lane_type=lane_type,
            query=query,
            fetched=fetched,
            selected=int(per.get("selected") or 0),
            needs_review=int(per.get("needs_review") or 0),
            rejected=int(per.get("rejected") or 0),
            limit_count=applied_limit,
            # This is measured against parent posts before comment expansion.
            # Legacy collectors and empty/error rows have no measured hit.
            limit_reached=applied_limit_reached,
            error_code=per.get("error_code") or None,
            unsupported_reason=per.get("error_hint") or per.get("hint") or None,
        )
    except Exception as e:  # noqa: BLE001 — evidence never fails the run
        logger.warning(
            "lane_evidence_failed source_id=%s task_id=%s error_code=%s",
            _log_id(getattr(source, "id", None)),
            task_id,
            _error_kind(e),
        )


async def handle_collect(payload: dict[str, Any], *, stage_only: bool = False) -> dict[str, Any]:
    """
    Collect content from sources.

    Sources come from the task's m2m `sources` (task.sources); empty = all active.
    Per-task overrides live in the task payload:
        tracked_users:  list[str] — provider:external_id authors whose items are
                        eligible; a filter over each source's own content, never
                        a collection target (docs/design/task-selection.md)

    Before the sources are resolved the runtime provisions the task's own
    collection intent: the population search (see `_run_population_step`) and
    the mention phrases of `payload["collection"]`, each of which becomes a
    task-owned SEARCH lane of the task (see `_run_mention_lanes_step`). Every
    collected source then writes one `collection_lane_runs` evidence row
    beside its per-source counters (`_record_lane_evidence`).
    """
    from app.services.collection.context import build_collection_context, platform_name
    from app.services.collection.intent import CollectionIntentError, parse_collection_intent
    from app.services.monitoring.collector import ContentCollector
    from app.services.social.rate_limit import SourceRateLimited

    snapshot = _effective_replay_snapshot(payload)
    task = None if snapshot else await _load_task(payload.get("agent_task_id"))
    task_payload = dict(snapshot["task_payload"]) if snapshot else _task_payload(task)
    has_task_context = task is not None or snapshot is not None
    # A rehearsal writes no raw rows, calls no model and leaves the collection
    # cursor where it was. A task carries the flag (an admin's toggle); a
    # taskless CLI run may pass it in the payload.
    if snapshot is not None:
        dry_run = bool(snapshot.get("dry_run"))
    elif task is None:
        dry_run = bool(payload.get("dry_run"))
    else:
        dry_run = bool(task_payload.get("dry_run")) or bool(getattr(task, "dry_run", False))
    # Optional per-run overrides merged into each source's params (the collector
    # reads force_refresh/cli_dates/incremental_mode from source.params).
    # Web task rows may contain historical force flags. They are never runtime
    # overrides; only a direct taskless CLI invocation can request one explicitly.
    force_refresh = bool(payload.get("force_refresh")) if task is None and snapshot is None else False
    # The content window is resolved once and applied at every stage (collect +
    # analyze) — see `_resolve_content_window`.
    window = _resolve_content_window(task_payload, payload, None)
    cli_dates = window or None
    # Legacy task force flags are ignored. A taskless CLI diagnostic may set
    # this override, but durable receipts still prevent repeat analysis.
    force_reanalyze = bool(payload.get("force_reanalyze")) if task is None and snapshot is None else False
    # The job's own id, used to tie staged raw content back to this run so it
    # can be retired once an analysis has consumed it.
    run_id = payload.get("job_id")
    # Whether this task wants its freshly collected content analysed inline.
    # Default False: a `collect` task parks the raw batch in `collected_items`
    # for a separate `analyze` task to drain (the workspace ships collect and
    # analyze as two tasks). A unified web run uses `stage_only` and starts its
    # analysis pass after this collection has staged the raw rows.
    analyze_inline = False if stage_only else task_payload.get("analyze_inline")
    if analyze_inline is None:
        analyze_inline = payload.get("analyze_inline", False)

    # Population step: the task's own intent may declare a search (a phrase
    # plus the kinds to find and the city) whose results become task-owned
    # wall lanes. It runs BEFORE the sources are read, so the found walls join
    # this same run; a blocked search is named evidence and never fails the
    # rest of the collection.
    population = None
    if task is not None and _log_id(getattr(task, "id", None)) is not None:
        population = await _run_population_step(
            task,
            task_payload=task_payload,
            dry_run=dry_run,
            job_id=run_id,
        )
        if population and (population.get("created") or population.get("reused")):
            # The lanes exist now — read the task's sources again so this run
            # picks them up.
            task = await _load_task(payload.get("agent_task_id")) or task

    # Mention lanes: the task's own «Что искать» phrases become task-owned
    # SEARCH lanes before the sources are resolved, so they join this same
    # run — no hand-made search source needed (`cli collection lanes`). A dry
    # run creates nothing and only reports the phrases without a lane.
    mention = None
    if task is not None and _log_id(getattr(task, "id", None)) is not None:
        mention = await _run_mention_lanes_step(task, task_payload=task_payload, dry_run=dry_run)
        if mention and (mention.get("created") or mention.get("linked")):
            # The lanes exist now — read the task's sources again so this run
            # picks them up.
            task = await _load_task(payload.get("agent_task_id")) or task

    discovered_source_ids = list(
        dict.fromkeys(
            (*(population or {}).get("source_ids", []), *(mention or {}).get("source_ids", []))
        )
    )
    sources = await _resolve_sources(None if snapshot else task, payload, discovered_source_ids=discovered_source_ids)

    collector = ContentCollector()

    def _apply_params(source) -> None:
        params = dict(source.params or {})
        params.pop("force_refresh", None)
        params.pop("cli_dates", None)
        if task is None and force_refresh:
            params["force_refresh"] = True
        if cli_dates:
            params["cli_dates"] = cli_dates
        source.params = params

    stats = {
        "sources": 0,
        "collected": 0,
        "empty": 0,
        "error": 0,
        "items": 0,
        "excluded": 0,
        "collected_sources": [],
        "empty_sources": [],
        "error_sources": [],
        "excluded_sources": [],
        "error_messages": [],
        "dry_run": dry_run,
        # Sources whose platform rate limit did not clear within the bounded
        # retry budget: deferred to the next run, neither failed nor empty.
        "rate_limited": 0,
        "rate_limited_sources": [],
        # Items the platforms actually handed over, and how many of those were
        # never seen before. Platforms do not remember what we fetched, so
        # `items` repeats itself on every re-run — `new_items` is the number that
        # answers "did this run find anything".
        "new_items": 0,
        "duplicate_count": 0,
        # Per-source outcome, keyed by id so the UI can show it on that source's
        # own page. The aggregate `items` above is a single number for the whole
        # run, which is what left "what did *this* source yield?" unanswerable.
        "per_source": [],
    }
    if population is not None:
        # Named evidence for the population step: what it found, what it
        # linked, and — when the search is blocked — the bounded reason.
        stats["population"] = population
    if mention is not None:
        # Named evidence for the mention-lane step: created/reused lanes and,
        # in a dry run, how many phrases still have no lane.
        stats["mention_lanes"] = mention
    from app.jobs import progress as job_progress
    if stage_only:
        await job_progress.begin_phase("collection")

    # `docs/design/task-selection.md`: whose items are eligible is the task's
    # `tracked_users` filter applied over what each source returns. Selection is
    # not a reason to read someone else's wall, and it cannot decide what is
    # collected — the task's `payload["collection"]` owns that.
    try:
        selection_scope = selection.build_scope(task_payload)
    except selection.SelectionError as error:
        # A collect task never analyses here (the staged path enforces the
        # selection), so a broken value cannot make it stage the wrong wall.
        # Log the bounded code and collect normally.
        logger.warning(
            "collect_selection_invalid task_id=%s error_code=%s",
            _log_id(getattr(task, "id", None)),
            str(error),
        )
        selection_scope = selection.SelectionScope()

    # The task's collection limit, as every per-source evidence row reports
    # it. A malformed block is already reported per source; the rows keep
    # their counts and stay without a limit.
    run_limit: int | None = None
    if has_task_context:
        try:
            run_limit = int(parse_collection_intent(task_payload).limit)
        except CollectionIntentError:
            run_limit = None
    evidence_task_id = _log_id(getattr(task, "id", None))

    from app.services.monitoring.selection_store import source_limit

    def _limit_metadata(source: Any, result: Any = None) -> dict[str, Any]:
        result = result if isinstance(result, dict) else {}
        applied_limit = result.get("limit_count")
        if type(applied_limit) is int and applied_limit > 0:
            reached = result.get("limit_reached") is True
        else:
            applied_limit = source_limit(source, run_limit)
            reached = False
        return {"limit_count": applied_limit, "limit_reached": reached}

    # One handle invocation is one deduplication scope. Separate jobs start
    # empty, preserving independent-run and source-scoped history behavior.
    seen_items: set[tuple[str, str, str]] | None = set() if has_task_context else None

    async def _evidence(source: Any, per: dict[str, Any]) -> None:
        await _record_lane_evidence(
            source=source,
            per=per,
            task_id=evidence_task_id,
            job_id=run_id,
            limit_count=run_limit,
        )

    for source in sources:
        await job_progress.step(sources, stats["per_source"])
        stats["sources"] += 1
        _apply_params(source)
        try:
            collection_context = build_collection_context(
                task_payload,
                platform=platform_name(source),
                source_id=source.id,
            )
        except CollectionIntentError as error:
            logger.warning(
                "collect_collection_intent_invalid task_id=%s source_id=%s error_code=%s",
                _log_id(getattr(task, "id", None)),
                _log_id(source.id),
                str(error),
            )
            stats["error"] += 1
            stats["error_sources"].append(source.name)
            stats["error_messages"].append(f"{source.name}: {error}")
            stats["per_source"].append(
                {
                    "source_id": source.id,
                    "name": source.name,
                    "items": 0,
                    "new_items": 0,
                    "outcome": "error",
                    "analyzed": 0,
                    "duplicate_count": 0,
                    **_limit_metadata(source),
                    "error_code": "collection_intent_invalid",
                    "error_hint": str(error),
                }
            )
            await _evidence(source, stats["per_source"][-1])
            continue
        # A tracked author is a filter on what a source returns, not a
        # collection target: the source's own wall is always read.
        try:
            analysis_errors = 0
            result = await collector.collect_from_source(
                source,
                analyze=bool(analyze_inline) and not dry_run,
                force_reanalyze=force_reanalyze,
                run_id=run_id,
                dry_run=dry_run,
                selection_scope=selection_scope,
                task_id=getattr(task, "id", None),
                # A taskless CLI run keeps the standalone legacy source cap;
                # the local context below is only for the run summary.
                collection_context=collection_context if has_task_context else None,
                seen_items=seen_items,
            )
            items = (result or {}).get("content_count", 0)
            new_items = (result or {}).get("new_items", 0)
            duplicate_count = (result or {}).get("duplicate_count", 0)
            if type(duplicate_count) is not int or duplicate_count < 0:
                duplicate_count = 0
            stats["duplicate_count"] += duplicate_count
            if result and result.get("content_count", 0) > 0:
                stats["collected"] += 1
                stats["items"] += result["content_count"]
                stats["new_items"] += result.get("new_items", 0)
                stats["collected_sources"].append(source.name)
                outcome = "collected"
            else:
                stats["empty"] += 1
                stats["empty_sources"].append(source.name)
                outcome = "empty"
            reported_analysis_errors = (result or {}).get("analysis_errors")
            analysis_errors = (
                reported_analysis_errors
                if type(reported_analysis_errors) is int and reported_analysis_errors > 0
                else 0
            )
            if analysis_errors:
                # The handler counts affected parent sources once.
                stats["error"] += 1
                stats["error_sources"].append(source.name)
            # `analytics_count` is what the collect run *stored*, not what a later
            # `analyze` task will do — the loop already analyses inline.
            stats["per_source"].append(
                {
                    "source_id": source.id,
                    "name": source.name,
                    "items": int(items or 0),
                    "new_items": int(new_items or 0),
                    "duplicate_count": duplicate_count,
                    "outcome": outcome,
                    "analyzed": int((result or {}).get("analytics_count", 0) or 0),
                    # Raw rows parked for a later analysis (0 when analysed inline).
                    "staged": int((result or {}).get("staged", 0) or 0),
                    # Collection-time outcomes: selected rows are analysable,
                    # needs_review waits for an operator, rejected text is not stored.
                    "selected": int((result or {}).get("selected", 0) or 0),
                    "needs_review": int((result or {}).get("needs_review", 0) or 0),
                    "rejected": int((result or {}).get("rejected", 0) or 0),
                    **_limit_metadata(source, result),
                    **(
                        {"parent_count": (result or {}).get("parent_count")}
                        if type((result or {}).get("parent_count")) is int
                        else {}
                    ),
                    # Parsed task intent and the provider's honest capabilities
                    # for this source. D2-D4 consume the same context.
                    "collection": (result or {}).get("collection") or collection_context.summary(),
                    # Comment lane evidence: parents read, comments fetched and
                    # every cap hit. Absent when the task collects posts only.
                    **(
                        {"comments": (result or {}).get("comments")}
                        if (result or {}).get("comments") is not None
                        else {}
                    ),
                    # Engagement evidence: candidates discovered per lane and how
                    # many were stored. Candidates are never text rows.
                    **(
                        {"candidates": (result or {}).get("candidates")}
                        if (result or {}).get("candidates") is not None
                        else {}
                    ),
                    # Profile evidence: identities enriched with a declared city,
                    # how many stayed unknown, and why the gate could not run.
                    **(
                        {"profiles": (result or {}).get("profiles")}
                        if (result or {}).get("profiles") is not None
                        else {}
                    ),
                    # This run's "seen" ledger. Written on every collect, whether
                    # or not the analysis succeeded — that is what stops the next
                    # collection from re-reporting the same wall as new.
                    "content_hashes": list((result or {}).get("content_hashes") or []),
                    "dry_run": bool((result or {}).get("dry_run")),
                    **(
                        {"preview": (result or {}).get("preview")}
                        if (result or {}).get("dry_run")
                        else {}
                    ),
                    **(
                        {"selection": (result or {}).get("selection")}
                        if (result or {}).get("selection")
                        else {}
                    ),
                }
            )
            if analysis_errors:
                per = stats["per_source"][-1]
                per["error"] = True
                per["analysis_errors"] = analysis_errors
        except SourceRateLimited as e:
            logger.warning("collect deferred for source %s: platform rate limit", source.id)
            stats["rate_limited"] += 1
            stats["rate_limited_sources"].append(source.name)
            stats["per_source"].append(
                {
                    "source_id": source.id,
                    "name": source.name,
                    "items": 0,
                    "new_items": 0,
                    "outcome": "rate_limited",
                    "analyzed": 0,
                    "duplicate_count": 0,
                    **_limit_metadata(source),
                    "retry_after": math.ceil(e.retry_after) if e.retry_after else None,
                    "hint": e.hint,
                    "error_code": "rate_limited",
                }
            )
        except Exception as e:
            logger.error(f"collect failed for source {source.id}: {e}", exc_info=True)
            stats["error"] += 1
            stats["error_sources"].append(source.name)
            stats["error_messages"].append(f"{source.name}: {e}")
            error_code, error_hint = _provider_error(e)
            stats["per_source"].append(
                {
                    "source_id": source.id,
                    "name": source.name,
                    "items": 0,
                    "new_items": 0,
                    # `auth_required` is its own outcome: it is not a broken
                    # source, it is a missing authorization, and the fix is one
                    # click on a different page. Flattening it into "error" is
                    # what made this invisible for so long.
                    "outcome": "auth_required" if isinstance(e, AuthorizationRequired) else "error",
                    "analyzed": 0,
                    "duplicate_count": 0,
                    **_limit_metadata(source),
                    "auth_hint": getattr(e, "hint", "") if isinstance(e, AuthorizationRequired) else "",
                    # Bounded cause for the admin diagnostics: the numeric
                    # platform code and an allowlisted hint, never raw text.
                    "error_code": error_code,
                    "error_hint": error_hint,
                }
            )
        # One lane-run row per collected source, over the outcome recorded
        # above — added evidence; the per-source stats stay exactly as they are.
        await _evidence(source, stats["per_source"][-1])
    return stats


async def handle_digest(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a digest and publish it to configured channels.

    Payload:
        period: 'day' | 'week' | 'month' (default 'day')
        agent_task_id: int | None — set when triggered by a task (idempotency)
        scenario_id: int | None — explicit scenario override
        group_by: str | None — grouping axis override (themes|sources|entities|sentiment|content_type|intent)
        time_breakdown: bool — enable per-date sub-entries within each group

    Hybrid flow: step 1 builds the algorithmic brief from the task's own sources
    grouped by the `group_by` axis, step 2 asks the LLM for a narrative over
    that brief, step 3 publishes the result to every tenant channel with
    `is_digest_target=True` (plus env-configured channels).

    # TODO(future): per-source digest. If task.sources is non-empty — filter the
    # aggregation by them (source_ids support is wired into ReportAggregator).
    # Empty sources list = the whole workspace (current behaviour).
    """
    from app.services.digest.builder import build_and_publish

    task = await _load_task(payload.get("agent_task_id"))
    task_payload = _task_payload(task)

    period = task_payload.get("period", payload.get("period", "day"))
    if period not in ("day", "week", "month"):
        return {"status": "failed", "error": f"Invalid period: {period}"}
    # The task's own sources scope the aggregation; None = whole workspace (the
    # ReportAggregator reads through the tenant guard either way).
    source_ids = [s.id for s in (task.sources or [])] if task is not None else None

    # The group_by axis tells the aggregator how to group the brief.
    scenario_id = task_payload.get("scenario_id") or payload.get("scenario_id")
    if not scenario_id and task is not None:
        scenario_id = getattr(task, "agent_scenario_id", None)
    group_by = task_payload.get("group_by") or payload.get("group_by") or "themes"
    time_breakdown = bool(task_payload.get("time_breakdown") or payload.get("time_breakdown"))

    # `--force-refresh` re-sends the digest even when it was already sent for
    # this task+period (skips the idempotency check).
    force = bool(task_payload.get("force_refresh") or payload.get("force_refresh"))
    return await build_and_publish(
        period=period,
        agent_task_id=payload.get("agent_task_id"),
        force=force,
        source_ids=source_ids,
        group_by=group_by,
        time_breakdown=time_breakdown,
        scenario_id=scenario_id,
    )


async def handle_prune(payload: dict[str, Any]) -> dict[str, Any]:
    """Retention: trim old finished jobs (default: older than 7 days).

    Analysis results older than `DATA_RETENTION_MONTHS` calendar months (by
    last write) are deleted in the current workspace, except rows a bot action
    still references; unanalysed raw content never outlives that same cutoff.

    Also sweeps raw content nobody ever analysed. `collected_items` is a
    write-ahead copy, so a collection whose analysis keeps failing (or a run
    with no job behind it, whose `run_id` is NULL) would keep its rows for
    ever; this is the ceiling on that. The sweep the table's own docstrings
    promise was never wired to anything — without it, "a failed analysis keeps
    the content" quietly becomes "keeps it until the disk fills".
    """
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings
    from app.core.database import new_session
    from app.models import AIAnalytics, CollectedItem
    from app.utils.retention import months_before

    now = datetime.now(timezone.utc)
    if payload.get("mode") == "job_history":
        # The active payload configures built-in retention independently of
        # cron; an explicit run also applies that policy immediately.
        from app.jobs.lifecycle import retention_policy

        policy = retention_policy(payload)
        if policy is None:
            raise ValueError("Некорректный срок хранения истории заданий")
        from app.jobs.lifecycle import cleanup_terminal_job_history
        from app.core.tenant_context import current_tenant_id

        deleted_jobs = await cleanup_terminal_job_history(tenant_id=current_tenant_id())
        return {
            "status": "policy_saved",
            "success_hours": policy[0],
            "failure_hours": policy[1],
            "deleted": deleted_jobs,
        }

    retention_months = settings.DATA_RETENTION_MONTHS
    retention_cutoff = months_before(now, retention_months)
    # Job history retention is a built-in lifecycle policy. Legacy prune tasks
    # continue to manage raw and analytics data, but cannot bypass that policy.
    deleted = 0
    days = max(1, int(payload.get("days", 7)))

    # Unanalysed raw content: same age budget as the job history. Raw text is
    # kept only while an analysis might still want it, so `staged_days` follows
    # `days` unless the task says otherwise.
    staged_days = min(int(payload.get("staged_days", days)), (now - retention_cutoff).days)
    staged_deleted = 0
    session = new_session()
    try:
        async with session.begin():
            staged_deleted = await CollectedItem.objects.delete_older_than(session, staged_days)
    except Exception as e:  # noqa: BLE001 — a sweep failure must not fail the prune
        logger.warning(
            "staged_retention_failed job_id=%s error_code=%s",
            _log_id(payload.get("job_id")),
            _error_kind(e),
        )
    finally:
        await session.close()

    # Analysis results: the configured data retention, not the job-history age.
    analytics_deleted = 0
    analytics_retention_failed = False
    try:
        analytics_deleted = await AIAnalytics.objects.delete_expired(retention_cutoff)
    except Exception as e:  # noqa: BLE001 — reported in the result, never silently "0 deleted"
        analytics_retention_failed = True
        logger.warning(
            "analytics_retention_failed job_id=%s error_code=%s",
            _log_id(payload.get("job_id")),
            _error_kind(e),
        )

    result = {
        "deleted": deleted,
        "staged_deleted": staged_deleted,
        "staged_days": staged_days,
        "analytics_deleted": analytics_deleted,
        "retention_months": retention_months,
    }
    if analytics_retention_failed:
        result["analytics_retention_failed"] = True
    return result


async def handle_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze collected content and create bot actions if triggers match.

    Sources come from the task's m2m `sources` (task.sources); empty = all active.
    Scenario comes from the task's `agent_scenario` (fallback: tenant default).
    Payload may carry:
        scenario_id: int | None — explicit scenario override
        dry_run: bool — report the selection without calling a model

    Flow:
    1. Get sources and their scenarios
    2. For each source, run TriggerEvaluator.should_analyze (pre-filter)
    3. Run TriggerEvaluator.should_act on already-stored analysis result
    4. If action needed, check guards and create BotAction (dry_run=True by default)
    """
    from app.models import AgentScenario, AIAnalytics, BotAction, CollectedItem
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.services.ai.trigger_evaluator import trigger_evaluator
    from app.services.social.guards import guards_checker
    from app.types import BotActionStatus

    snapshot = _effective_replay_snapshot(payload)
    task = _replay_task_view(snapshot) if snapshot else await _load_task(payload.get("agent_task_id"))
    task_payload = dict(snapshot["task_payload"]) if snapshot else _task_payload(task)
    unified_run = bool(task_payload.get("collect_before_analyze"))
    collection_stats = await handle_collect(payload, stage_only=True) if unified_run else None
    collection_by_source = {
        row["source_id"]: row
        for row in (collection_stats or {}).get("per_source", [])
        if isinstance(row, dict) and type(row.get("source_id")) is int
    }
    from app.jobs import progress as job_progress

    if unified_run:
        await job_progress.begin_phase("analysis")
    scenario_id = (
        snapshot.get("scenario_id") if snapshot else task_payload.get("scenario_id") or payload.get("scenario_id")
    )
    if snapshot is not None:
        dry_run = bool(snapshot.get("dry_run"))
    elif task is None:
        dry_run = bool(payload.get("dry_run"))
    else:
        dry_run = bool(task_payload.get("dry_run")) or bool(getattr(task, "dry_run", False))
    force_reanalyze = bool(payload.get("force_reanalyze")) if task is None and snapshot is None else False
    keep_raw = bool(payload.get("keep_raw")) if task is None and snapshot is None else False
    # The content window gates which staged rows this run drains — the same
    # window that gated collection, so analysis never touches content outside
    # the task's configured period.
    window = _resolve_content_window(task_payload, payload, None)
    if "staged_window" in payload:
        from app.services.ai.analysis_retry import parse_staged_window

        # A detail-page retry must never widen its stored timestamp range to
        # CLI dates (or all history) if its payload is malformed.
        window = parse_staged_window(payload["staged_window"])

    stats: dict[str, Any] = {
        "sources": 0,
        "analyzed": 0,
        "actions_created": 0,
        "skipped": 0,
        "error": 0,
        "error_sources": [],
        "staged_errors": 0,
        "per_source": [],
        "dry_run": dry_run,
    }
    if collection_stats is not None:
        # Keep collection diagnostics alongside analysis results so the single
        # web job reports partial collection even when backlog analysis worked.
        stats["collection"] = collection_stats
        stats["collection_errors"] = int(collection_stats.get("error", 0) or 0)
        stats["collection_rate_limited"] = int(collection_stats.get("rate_limited", 0) or 0)
        stats["error"] += stats["collection_errors"]
        stats["error_sources"].extend(collection_stats.get("error_sources") or [])
        # Collector exceptions may contain provider response details. The
        # analysis job exposes bounded codes and source outcomes instead.
        collection_stats.pop("error_messages", None)
        collection_stats.pop("per_source", None)
        collection_error_source_ids = {
            source_id
            for source_id, row in collection_by_source.items()
            if row.get("error") is True or row.get("outcome") in {"error", "auth_required"}
        }

    if collection_stats is None:
        collection_error_source_ids = set()

    def record_error(per: dict[str, Any], *, staged: bool = False) -> None:
        # Count affected sources, not exceptions: staging and later processing
        # can both fail for one source. Never persist raw exception text here.
        if not per.get("error"):
            if per["source_id"] not in collection_error_source_ids:
                stats["error"] += 1
                stats["error_sources"].append(per["name"])
        per["error"] = True
        if staged and not per.get("staged_error"):
            stats["staged_errors"] += 1
            per["staged_error"] = True

    sources = await _resolve_sources(None if snapshot else task, payload)

    for source in sources:
        await job_progress.step(sources, stats["per_source"])
        # Per-source counters, so the run-now modal can link to each source and
        # the source page can show what the agent analysed *here*. `analyzed` and
        # `actions_created` below stay run-wide; these are this source's share.
        per: dict[str, Any] = {
            "source_id": source.id,
            "name": source.name,
            "analyzed": 0,
            "actions": 0,
            "analytics_ids": [],
            "dry_run": dry_run,
        }
        progress_attempted = False
        try:
            stats["sources"] += 1

            # Resolve scenario — the task's own scenario, else an explicit
            # override, else the scenario of a task that explicitly links this
            # source, else the tenant default. A source carries no scenario.
            if task is not None and getattr(task, "agent_scenario", None):
                scenario = task.agent_scenario
            elif scenario_id:
                scenario = await AgentScenario.objects.get(id=scenario_id)
            elif task is None:
                # A staging-triggered analyze has no task of its own; if a task
                # pinned a scenario on this source, that task owns the analysis
                # and its model must win over the workspace default.
                scenario = await AgentTaskManager().get_source_scenario(source.id)
            else:
                scenario = None

            if not scenario or not scenario.is_active:
                # Fallback: tenant's default scenario
                scenario = await AgentScenario.objects.get_default_scenario(tenant_id=source.tenant_id)

            if not scenario or not scenario.is_active:
                stats["skipped"] += 1
                continue

            # Drain whatever the collect run staged for this source.
            #
            # Every collection writes its raw batch first; this is where a run
            # with `analyze_inline: false` gets that work paid for. The rows are
            # retired by the hashes the analysis actually stored, so a partial
            # result keeps the unanalysed rows and a failed analysis leaves
            # every one of them for the next attempt — the only copy of the
            # content must never be the thing we drop.
            fresh = []
            staged = []
            analytics = None
            # Failure-code horizon: only LLM attempts of THIS run may label it.
            # It is taken before the staging read, because a failure there is
            # part of this run too — and the handler below reports the code of
            # that failure.
            from datetime import datetime, timezone

            attempt_journal_since = datetime.now(timezone.utc)
            try:
                try:
                    raw_total = await CollectedItem.objects.count_for_source(
                        source.id, start_date=window.get("start_date"), end_date=window.get("end_date")
                    )
                except Exception as count_error:  # noqa: BLE001 — optional progress read
                    raw_total = None
                    logger.warning(
                        "analyze_staged_count_failed source_id=%s error_code=%s",
                        _log_id(source.id), _error_kind(count_error),
                    )
                staged = await CollectedItem.objects.for_source(
                    source.id,
                    limit=ANALYZE_STAGE_BATCH,
                    start_date=window.get("start_date"),
                    end_date=window.get("end_date"),
                )
                per.update({"raw_total": raw_total, "raw_selected": len(staged), "raw_processed": 0})
                await job_progress.update_current(sources, stats["per_source"], per)
                progress_attempted = True
                covered = set()
                if staged:
                    from app.services.ai.analyzer import AIAnalyzer

                    # New rows were selected before staging. Legacy rows (NULL
                    # status, staged before this stage) still get the old
                    # deterministic pass so an upgrade does not analyse text
                    # the task never selected. New selected rows are never
                    # re-filtered: the collection decision is authoritative.
                    staged_scope = selection.build_scope(task_payload)
                    analyzed_items = [row.as_agent_item() for row in staged]
                    status_items = [
                        item
                        for row, item in zip(staged, analyzed_items)
                        if getattr(row, "selection_status", None) == "selected"
                    ]
                    legacy_items = [
                        item
                        for row, item in zip(staged, analyzed_items)
                        if getattr(row, "selection_status", None) is None
                    ]
                    legacy_selected = (
                        selection.select_items(legacy_items, staged_scope) if legacy_items else None
                    )
                    items = status_items + (legacy_selected.eligible if legacy_selected is not None else [])
                    reasons = dict(legacy_selected.reasons) if legacy_selected is not None else {}
                    if status_items:
                        reasons["stored_selected"] = len(status_items)
                    per["selection"] = {
                        "eligible": len(items),
                        "rejected": len(legacy_selected.rejected) if legacy_selected is not None else 0,
                        "reasons": reasons,
                        "unknown_tracked_users": (
                            list(legacy_selected.unknown_tracked_users) if legacy_selected is not None else []
                        ),
                    }
                    await job_progress.update_current(sources, stats["per_source"], per)
                    if dry_run:
                        # A rehearsal reports what would be sent to the model,
                        # calls none, and leaves every raw row staged.
                        per["dry_run"] = True
                        continue
                    if not items:
                        # Everything staged is outside the task's selection. It
                        # stays staged for retention and is not a paid attempt.
                        stats["skipped"] += 1
                        if per["selection"]["unknown_tracked_users"]:
                            logger.info(
                                "analyze_selection_none_unknown_users source_id=%s count=%s",
                                _log_id(source.id),
                                len(per["selection"]["unknown_tracked_users"]),
                            )
                        continue
                    analytics = None
                    # Selection already happened at collection for new rows;
                    # rejected text is not stored and cannot be retired here.
                    analyzer = AIAnalyzer()
                    fresh = await analyzer.analyze_content(
                        items,
                        source,
                        agent_scenario=scenario,
                        force_reanalyze=bool(force_reanalyze),
                        # The trigger belongs to the task, so it reaches the prompt
                        # from here rather than off the shared scenario.
                        trigger_config=(task.trigger_config if task is not None else None) or None,
                        # Task-specific TARGET parameters: brands, competitors, hashtags.
                        # These are injected into the prompt instruction, not the response schema.
                        task_payload=task_payload,
                        billing_execution={
                            "source_id": source.id,
                            "job_id": payload.get("job_id"),
                            "agent_task_id": task.id if task is not None else None,
                            "initiated_by_user_id": payload.get("_trusted_initiated_by_user_id"),
                        },
                    )
                    # These are only rows persisted by this fresh analyzer call.
                    # Capture before trigger filtering, which may discard all of
                    # them while the detail page still needs to link to results.
                    per["analytics_ids"] = _fresh_analytics_ids(fresh)
                    # A client can report a failure without raising, including
                    # a failed sibling alongside useful saved analysis. Keep
                    # legacy/malformed diagnostics unknown, not coerced to zero.
                    reported_errors = getattr(analyzer, "reported_errors", None)
                    if type(reported_errors) is int and reported_errors > 0:
                        record_error(per, staged=True)
                        failure_code = await _llm_failure_code(source.id, attempt_journal_since)
                        if failure_code:
                            per["error_code"] = failure_code
                    # Retire by what the analysis stored, not by the run the rows
                    # came from: rows staged by an API/CLI run carry no run id at
                    # all, and a partial analysis must leave the rest alone.
                    completed_hashes = getattr(analyzer, "completed_hashes", set())
                    deleted = 0 if keep_raw else await _retire_staged(
                        fresh,
                        source.id,
                        source.tenant_id,
                        completed_hashes=completed_hashes,
                        analysis_fingerprint=getattr(analyzer, "current_fingerprint", None),
                    )
                    # Only rows from this exact batch can count as processed.
                    # `fresh` is persisted coverage; intersect its hashes with
                    # the selected batch to avoid crediting unrelated rows.
                    from app.services.ai.dedup import analysed_hashes

                    covered = analysed_hashes(fresh) | completed_hashes
                    per["raw_processed"] = sum(
                        1
                        for item in items
                        if item.get("content_hash") in covered
                    )
                    await job_progress.update_current(sources, stats["per_source"], per)
                    if deleted:
                        logger.info(f"Analysed {len(staged)} staged item(s) for source {source.id}, retired {deleted}")
                    elif not completed_hashes:
                        logger.warning(
                            f"Analysis of {len(staged)} staged item(s) for source {source.id} produced "
                            "no result; keeping them staged for a retry"
                        )
                    counted = await _count_failed_staged(
                        source.id, [row for row, item in zip(staged, analyzed_items) if item in items], covered
                    )
                    if counted:
                        logger.warning(
                            f"{counted} staged item(s) of source {source.id} went unanalysed; "
                            "counting the attempt so they stop being retried eventually"
                        )

                # Trigger evaluation is a continuation of this run's analysis,
                # not a replay mechanism for recent history. Receipt-only hits
                # deliberately have no analytics object to trigger from.
                if staged:
                    if not fresh:
                        stats["skipped"] += 1
                        continue
                    analytics = fresh
            except Exception as e:  # noqa: BLE001 — a staging problem must not abort the run
                from app.jobs.progress import ProgressStopped

                if isinstance(e, ProgressStopped):
                    raise
                record_error(per, staged=True)
                failure_code = await _llm_failure_code(source.id, attempt_journal_since)
                if failure_code:
                    per["error_code"] = failure_code
                logger.warning(
                    "analyze_staged_processing_failed source_id=%s error_code=%s",
                    _log_id(source.id),
                    _error_kind(e),
                )

            # Get recent analytics for this source
            if analytics is None and not (unified_run and not staged):
                analytics = (
                    await AIAnalytics.objects.filter(source_id=source.id)
                    .order_by(AIAnalytics.created_at.desc()).limit(10)
                )

            if not analytics:
                stats["skipped"] += 1
                continue

            # Prepare content for trigger evaluation
            content = []
            for a in analytics:
                if a.summary_data:
                    result = a.response_payload or {}
                    recent_posts = a.summary_data.get("recent_posts", [])
                    content.append(
                        {
                            "text": a.summary_data.get("summary", ""),
                            "analytics_id": a.id,
                            "result": result,
                            "recent_posts": recent_posts,
                        }
                    )

            # Pre-filter: should_analyze (rule, not LLM)
            filtered_content = await trigger_evaluator.should_analyze(content, task)

            if not filtered_content:
                stats["skipped"] += 1
                continue

            stats["analyzed"] += len(filtered_content)
            per["analyzed"] += len(filtered_content)

            # Post-filter: should_act on the already-stored analysis result
            #
            # A task with no `action_type` analyses and stops there. This is the
            # path that used to read the action off the scenario, so a run with no
            # task at all (an ad-hoc one) now creates nothing — it has no
            # configured action to take.
            action_type = _resolve_action_type(task)
            if action_type is None:
                continue

            for item in filtered_content:
                should_act = await trigger_evaluator.should_act(item.get("result") or {}, task)

                if not should_act:
                    continue

                # Extract target post from recent_posts (first available)
                recent_posts = item.get("recent_posts", [])
                target_post = recent_posts[0] if recent_posts else {}

                # Create BotAction (dry_run=True by default)
                action_payload = {
                    "action_type": action_type.name,
                    "text": (item.get("result") or {}).get("response", ""),
                }
                # Add platform-specific target fields
                if target_post.get("owner_id"):
                    action_payload["owner_id"] = target_post["owner_id"]
                if target_post.get("post_id"):
                    action_payload["post_id"] = target_post["post_id"]
                if target_post.get("external_id"):
                    action_payload["external_id"] = target_post["external_id"]

                # Check the task's rate/cooldown guards. Recipient lists were
                # retired, so only the budget is enforced here.
                allowed, reason = await guards_checker.check(task)
                if not allowed:
                    logger.info(f"Action blocked by guards: {reason}")
                    continue

                # Idempotency: skip if PENDING action already exists for this analytics+scenario
                analytics_id = item.get("analytics_id")
                existing = await BotAction.objects.filter(
                    analytics_id=analytics_id,
                    agent_task_id=task.id if task is not None else None,
                    agent_scenario_id=scenario.id,
                    status=BotActionStatus.PENDING,
                ).first()
                if existing:
                    logger.info(
                        f"BotAction already exists for analytics {analytics_id}, task {task.id if task else None}"
                    )
                    continue

                action = await BotAction.objects.create(
                    agent_scenario_id=scenario.id,
                    agent_task_id=task.id if task is not None else None,
                    source_id=source.id,
                    analytics_id=analytics_id,
                    action_type=action_type,
                    status=BotActionStatus.PENDING,
                    payload=action_payload,
                    dry_run=True,
                )
                stats["actions_created"] += 1
                per["actions"] += 1
                logger.info(f"Created BotAction#{action.id} for source {source.id}")

        except Exception as e:
            from app.jobs.progress import ProgressStopped

            if isinstance(e, ProgressStopped):
                raise
            logger.error(
                "analyze_source_failed source_id=%s error_code=%s",
                _log_id(source.id),
                _error_kind(e),
            )
            record_error(per)
        finally:
            # `finally`, not a line at the end of the body: this loop `continue`s
            # out early in several places (no scenario, no analytics, nothing to
            # analyse) and every one of them is still an outcome the user should
            # be able to reach from the modal.
            stats["per_source"].append(per)
            collection_row = collection_by_source.get(per["source_id"])
            if collection_row is not None:
                per["collection"] = {
                    key: collection_row.get(key)
                    for key in (
                        "outcome",
                        "items",
                        "new_items",
                        "error",
                        "rate_limited",
                        "content_hashes",
                        "dry_run",
                        "preview",
                        "error_code",
                        "error_hint",
                        "auth_hint",
                        "selection",
                    )
                    if key in collection_row
                }
                if collection_row.get("error") is True or collection_row.get("outcome") in {
                    "error", "auth_required", "rate_limited"
                }:
                    per["collection_error"] = True
                    if collection_row.get("error") is True or collection_row.get("outcome") in {
                        "error", "auth_required"
                    }:
                        per["error"] = True
            if not progress_attempted and "raw_total" not in per:
                # Unknown is retained as unknown; inline collection or an
                # earlier scenario failure cannot claim a staged denominator.
                per["raw_total"] = None
            await job_progress.update_current(sources, stats["per_source"], per)

    return stats


async def handle_learn(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract durable facts from recent chat into provenance-tagged memory.

    Payload:
        min_messages: int — new user turns required before an LLM call (default 8)
        window: int — max messages to scan per run (default 200)
    """
    from app.agent.identity import memory_actor_scope
    from app.agent.learning import run_learn
    from app.models import User
    from app.models.managers.tenant_manager import tenant_users

    memberships = await tenant_users.filter(tenant_id=current_tenant_id(), is_active=True).exclude(user_id=None)
    results = []
    seen: set[int] = set()
    for membership in memberships:
        if membership.user_id in seen:
            continue
        if not membership.is_owner and not getattr(membership, "ai_chat_enabled", False):
            continue
        user = await User.objects.get(id=membership.user_id, is_active=True)
        if user is None:
            continue
        seen.add(user.id)
        with memory_actor_scope(user.id, membership.is_owner):
            results.append(
                await run_learn(
                    min_messages=int(payload.get("min_messages", 8)),
                    window=int(payload.get("window", 200)),
                )
            )
    return _aggregate_memory_job_results(results)


async def handle_reflect(payload: dict[str, Any]) -> dict[str, Any]:
    """Weekly memory hygiene + prompt-evolution proposals (never applied silently).

    Payload:
        dedup: bool — apply delete/update ops proposed by the LLM (default True)
    """
    from app.agent.identity import memory_actor_scope
    from app.agent.learning import run_reflect
    from app.models import User
    from app.models.managers.tenant_manager import tenant_users

    memberships = await tenant_users.filter(tenant_id=current_tenant_id(), is_active=True).exclude(user_id=None)
    results = []
    seen: set[int] = set()
    for membership in memberships:
        if membership.user_id in seen:
            continue
        if not membership.is_owner and not getattr(membership, "ai_chat_enabled", False):
            continue
        user = await User.objects.get(id=membership.user_id, is_active=True)
        if user is None:
            continue
        seen.add(user.id)
        with memory_actor_scope(user.id, membership.is_owner):
            results.append(await run_reflect(dedup=bool(payload.get("dedup", True))))
    return _aggregate_memory_job_results(results)


def _aggregate_memory_job_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Bounded summary for per-actor learning/reflection executions."""
    if not results:
        return {"status": "skipped", "reason": "no eligible personal chats", "actors": 0}
    failed = sum(result.get("status") == "failed" for result in results)
    ok = sum(result.get("status") == "ok" for result in results)
    skipped = len(results) - failed - ok
    costs = [result.get("llm_cost") for result in results]
    known_cost = (
        None if any(not isinstance(cost, (int, float)) or isinstance(cost, bool) for cost in costs) else sum(costs)
    )
    return {
        "status": "failed" if failed else "ok" if ok else "skipped",
        "actors": len(results),
        "succeeded": ok,
        "skipped": skipped,
        "failed": failed,
        "llm_cost": known_cost,
    }


HANDLERS = {
    "collect": handle_collect,
    "digest": handle_digest,
    "prune": handle_prune,
    "analyze": handle_analyze,
    "learn": handle_learn,
    "reflect": handle_reflect,
}
