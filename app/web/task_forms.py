"""Task form parsing, normalization, and source-selection policies."""

from __future__ import annotations

from .task_shared import (
    AgentActionType,
    AgentScenario,
    AgentTask,
    AgentTaskManager,
    Any,
    BotTriggerType,
    CITY_MODES,
    CITY_PREFER,
    COLLECTION_JOB_TYPES,
    CONFIRM_MODES,
    CollectionIntent,
    CollectionIntentError,
    JobType,
    RedirectResponse,
    Request,
    Response,
    SelectionError,
    Source,
    add_flash,
    apply_collection,
    collection_error_message,
    logger,
    parse_collection_intent,
    re,
)


def _split_names(raw: str) -> list[str]:
    """Split a comma/space separated username string into a clean list.

    Tokenisation only: case and order are preserved and a leading `@` is
    removed, because the stored identity, not the typed spelling, is what the
    runtime compares. Validation belongs to the selection writer.
    """
    return [n.strip().lstrip("@") for n in raw.replace(",", " ").split() if n.strip()]


def _split_phrases(raw: str) -> list[str]:
    """Split a comma separated phrase string, keeping spaces inside a phrase."""
    return [" ".join(value.split()) for value in (raw or "").split(",") if value.strip()]


def _web_job_type_choices(*, allow_retention: bool = False) -> list[tuple[str, str]]:
    """Task types shown in the web UI; collection is part of social analysis."""
    return [
        (value, "📊 Анализ соцсетей" if value == "analyze" else "Хранение истории Jobs" if value == "prune" else label)
        for value, label in JobType.choices()
        if value != "collect" and (allow_retention or value != "prune")
    ]


def _can_manage_job_retention(request: Request) -> bool:
    """Only active workspace administrators and owners manage retention policy."""
    perms = getattr(request.state, "web_perms", None)
    return bool(perms and (perms.is_owner or perms.workspace_role in {"owner", "admin"}))


def _normalize_web_job_type(job_type: str) -> str:
    """Map legacy web submissions to the unified social analysis task."""
    return "analyze" if job_type in ("collect", "analyze") else job_type


# The analysis targets a task payload carries (param_registry.PAYLOAD_PARAMS):
# the scenario's analysis types say *what kind* of target matters, the task
# says *which* brands/hashtags/… to aim at.
TARGET_KEYS = ("brands", "competitors", "hashtags", "influencer_names", "topic_list")

# The target inputs the editor renders: `type` is the scenario analysis type
# that asks for the key (param_registry.PAYLOAD_PARAMS). Shared by the edit
# page, the add modal and their JS so the server-rendered fallback and the
# Alpine-enhanced visibility never describe different fields. `keywords_list`
# is deliberately absent: the payload key is written by `apply_selection`, and
# the input lives in the selection block as «Фразы в тексте поста» — calling
# it a target too gave the form two «Ключевые слова» boxes.
TARGET_FIELD_DEFS = (
    {
        "key": "brands",
        "type": "brand_mentions",
        "label": "Бренды",
        "ph": "Coca-Cola, Sprite",
        "hint": "Какие бренды упоминать в контенте",
    },
    {
        "key": "competitors",
        "type": "competitor",
        "label": "Конкуренты",
        "ph": "brand-a, brand-b",
        "hint": "Чьи упоминания считать конкурентными",
    },
    {
        "key": "hashtags",
        "type": "hashtag_analysis",
        "label": "Хэштеги",
        "ph": "#бренд, #акция",
        "hint": "Хэштеги для отслеживания",
    },
    {
        "key": "influencer_names",
        "type": "influencer",
        "label": "Авторы",
        "ph": "author1, author2",
        "hint": "Авторы и инфлюенсеры для анализа",
    },
    {
        "key": "topic_list",
        "type": "topics",
        "label": "Темы",
        "ph": "доставка, цены",
        "hint": "Темы, которые нужно выделить",
    },
)

# The task's selection keys, written only through `apply_selection`.
_SELECTION_KEYS = ("tracked_users", "keywords_list", "keywords_match")


def apply_selection(payload: dict, tracked_users: str, keywords: list[str], keywords_match: str) -> None:
    """Normalise the task's selection into `payload` in place.

    Selection belongs to the task, and every reader of it is the runtime's
    `selection` module, so the stored form is exactly what that module parses
    (`platform:external_id`). A malformed identity raises `SelectionError`:
    the task must fail to save, not save a filter that quietly selects nothing.
    """
    # The payload is being built here, not merged into what the task stored:
    # absent fields must not resurrect values the operator just cleared.
    existing = {key: value for key, value in payload.items() if key not in _SELECTION_KEYS}
    try:
        merged = AgentTaskManager.build_selection_payload(
            tracked_users=_split_names(tracked_users),
            keywords=list(dict.fromkeys(keywords)),
            keywords_match=keywords_match or "any",
            existing=existing,
        )
    except SelectionError:
        raise ValueError("Проверьте «Отслеживаемые авторы»: укажите платформу и id, например vk:123456") from None
    # The form owns its fields: clearing one clears it, so the payload is
    # replaced by the writer's result rather than merged key by key here.
    payload.clear()
    payload.update(merged)


def _split_targets(raw: str) -> list[str]:
    """Split a comma separated target string, keeping spaces inside a name.

    `_split_names` splits on spaces too — right for usernames, wrong for
    "Coca Cola" — so targets are commas only.
    """
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def _apply_targets(payload: dict, targets: dict[str, str]) -> None:
    """Write the form's target fields into the payload, authoritatively.

    The edit page always submits every target input (hidden ones keep their
    value), so a filled field sets its key and an emptied one drops it. Dropping
    rather than storing `[]` keeps a cleared target out of the payload
    altogether, where `extract_target_values` would still inject it into the
    prompt as an empty list.
    """
    for key in TARGET_KEYS:
        values = _split_targets(targets.get(key, ""))
        if values:
            payload[key] = values
        else:
            payload.pop(key, None)


# The collection intent is a task-owned block (see
# `app/services/collection/intent.py`); the form only splits strings and fills
# the fields, the parser stays the single validator. A hidden
# `collection_configured` marker field tells the handlers the form actually
# carried the block: a save from an older client (or an API caller posting only
# the legacy fields) must not silently rewrite it.


def _split_intent_phrases(raw: str) -> list[str]:
    """Split the collection phrase fields; one phrase per comma or line."""
    return [" ".join(part.split()) for part in re.split(r"[,\n]", raw or "") if part.strip()]


def _collection_block(
    job_type: str,
    *,
    people: str,
    communities: str,
    discovery_queries: str,
    discovery_queries_match: str,
    discovery_city: str,
    discovery_city_id: str,
    queries: str,
    queries_match: str,
    content_types: list[str],
    engagement_types: list[str],
    limit: str,
    comments_max_per_post: str,
    comments_total: str,
    candidates_limit: str,
    candidates_confirm: str,
    date_window: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build and validate `payload["collection"]` from the form fields.

    Raises `ValueError` with a bounded message when the parser rejects a known
    value: a bad save must not stage a population the operator did not ask for.
    Returns an empty dict for every job type that does not collect, so the
    caller can drop a stale block when the task type changes. The block is
    written through the shared writer (`apply_collection`), so the web form
    stores exactly the shape the CLI, API and chat tools store.
    """
    if job_type not in COLLECTION_JOB_TYPES:
        return {}
    if not content_types:
        raise ValueError("Что собирать: отметьте записи, комментарии или оба типа")

    raw: dict[str, Any] = {
        "content_types": [value.strip().lower() for value in content_types if value.strip()],
    }
    if engagement_types:
        raw["engagement_types"] = [value.strip().lower() for value in engagement_types if value.strip()]
    if limit.strip():
        raw["limit"] = limit.strip()
    if queries.strip():
        raw["queries"] = _split_intent_phrases(queries)
    raw["queries_match"] = (queries_match or "").strip()
    if people == "on" or communities == "on" or discovery_queries.strip() or discovery_city.strip():
        discovery: dict[str, Any] = {
            "people": people == "on",
            "communities": communities == "on",
            "queries": _split_intent_phrases(discovery_queries),
            "queries_match": (discovery_queries_match or "").strip(),
        }
        if discovery_city.strip():
            city_block: dict[str, Any] = {"name": discovery_city.strip()}
            city_id = (discovery_city_id or "").strip()
            if city_id:
                city_block["id"] = city_id
            discovery["city"] = city_block
        raw["discovery"] = discovery
    if comments_max_per_post.strip() or comments_total.strip():
        raw["comments"] = {}
        if comments_max_per_post.strip():
            raw["comments"]["max_per_post"] = comments_max_per_post.strip()
        if comments_total.strip():
            raw["comments"]["total"] = comments_total.strip()
    if candidates_limit.strip() or (candidates_confirm or "").strip():
        raw["candidates"] = {}
        if candidates_limit.strip():
            raw["candidates"]["limit"] = candidates_limit.strip()
        if (candidates_confirm or "").strip():
            raw["candidates"]["confirm"] = candidates_confirm.strip()
    if date_window:
        raw["date_window"] = {
            "start": date_window.get("start_date"),
            "end": date_window.get("end_date"),
        }

    try:
        written = apply_collection({}, raw, job_type=job_type)
    except CollectionIntentError as exc:
        raise ValueError(collection_error_message(str(exc))) from None
    return written["collection"]


def _collection_edit(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Flat form values for the collection block, with the parser's defaults.

    A stored block the parser rejects (hand-edited payload) is shown as defaults
    and logged: the editor must open, and the next save re-validates what it
    writes instead of preserving an unreadable value.
    """
    try:
        intent = parse_collection_intent(payload)
    except CollectionIntentError as exc:
        logger.warning("task_collection_intent_unreadable error_code=%s", str(exc))
        intent = CollectionIntent()
    return {
        "start_date": intent.start_date.isoformat() if intent.start_date else "",
        "end_date": intent.end_date.isoformat() if intent.end_date else "",
        "collection_city": intent.city.name if intent.city else "",
        "collection_city_mode": intent.city.mode if intent.city else CITY_PREFER,
        "collection_queries_match": intent.queries_match,
        "collection_discovery_queries_match": intent.discovery.queries_match,
        "collection_discovery_city": intent.discovery.city.name if intent.discovery.city else "",
        "collection_discovery_city_id": (
            intent.discovery.city.id if intent.discovery.city and intent.discovery.city.id is not None else ""
        ),
        "collection_people": intent.discovery.people,
        "collection_communities": intent.discovery.communities,
        "collection_discovery_queries": ", ".join(intent.discovery.queries),
        "collection_queries": ", ".join(intent.queries),
        "collection_content_types": list(intent.content_types),
        "collection_engagement_types": list(intent.engagement_types),
        "collection_limit": intent.limit,
        "collection_comments_max_per_post": intent.comments.max_per_post,
        "collection_comments_total": intent.comments.total,
        "collection_candidates_limit": intent.candidates.limit,
        "collection_candidates_confirm": intent.candidates.confirm,
        "collection_city_modes": list(CITY_MODES),
        "collection_confirm_modes": list(CONFIRM_MODES),
    }


def _group_sources(sources: list[Any]) -> list[dict[str, Any]]:
    """Task-editor catalogue grouped by source type, stable order.

    Task-scoped search sources (a query lane's own `Source`) are flags on the
    row, not pickable here: they belong to exactly one task and are rendered
    with that task only (see the callers' `owner_task_id` filter).
    """
    groups: dict[str, list[Any]] = {}
    for source in sorted(sources, key=lambda item: (item.source_type.label, item.name)):
        groups.setdefault(source.source_type.label, []).append(source)
    return [{"label": label, "sources": items} for label, items in groups.items()]


def _build_trigger_config(
    trigger_type: str,
    keywords: str,
    match: str,
    usernames: str,
    threshold: str,
    direction: str,
    baseline_hours: str,
    spike_multiplier: str,
) -> dict:
    """The `trigger_config` dict the selected trigger type's own fields describe.

    Only the active type's keys are collected — the other types' fields are
    submitted anyway (they share the modal), so a hidden keyword list must not
    leak into a SENTIMENT_THRESHOLD rule.
    """
    if trigger_type == "KEYWORD_MATCH":
        cfg: dict = {"match": match if match in ("any", "all") else "any"}
        keywords_list = _split_names(keywords)
        if keywords_list:
            cfg["keywords"] = keywords_list
        return cfg
    if trigger_type == "USER_MENTION":
        usernames_list = _split_names(usernames)
        return {"usernames": usernames_list} if usernames_list else {}
    if trigger_type == "SENTIMENT_THRESHOLD":
        cfg = {"direction": direction if direction in ("below", "above") else "below"}
        try:
            cfg["threshold"] = float(threshold) if threshold else 0.5
        except ValueError:
            cfg["threshold"] = 0.5
        return cfg
    if trigger_type == "ACTIVITY_SPIKE":
        cfg = {}
        try:
            cfg["baseline_period_hours"] = int(baseline_hours) if baseline_hours else 24
        except ValueError:
            cfg["baseline_period_hours"] = 24
        try:
            cfg["spike_multiplier"] = float(spike_multiplier) if spike_multiplier else 3.0
        except ValueError:
            cfg["spike_multiplier"] = 3.0
        return cfg
    return {}


def _payload_from_form(
    job_type,
    start_date,
    end_date,
    digest_period=None,
    digest_group_by=None,
    digest_time_breakdown=False,
    retention_success_hours=24,
    retention_failure_hours=24,
    window_type="fixed",
) -> dict:
    """The flat payload keys a task of this type needs.

    For collect/analyze a `cli_dates.start_date` is mandatory: without it a
    fresh source has no lower bound and the first run drains the whole history
    from the first post. Web-launched jobs use the default skip-repeat policy.

    For digest: `period` (day/week/month), `group_by` axis, and
    `time_breakdown` flag are stored in payload so the scheduled run uses
    the operator's preference.
    """
    job_type = _normalize_web_job_type(job_type)
    if job_type == "prune":
        from app.jobs.lifecycle import retention_policy

        payload = {
            "mode": "job_history",
            "success_hours": retention_success_hours,
            "failure_hours": retention_failure_hours,
        }
        if retention_policy(payload) is None:
            raise ValueError("Срок хранения должен быть целым числом от 1 до 8760 часов")
        return payload
    payload: dict = {}
    if AgentTaskManager.requires_content_dates(job_type) and window_type != "rolling":
        start = AgentTaskManager.parse_date(start_date)
        if start is None:
            raise ValueError("Укажите дату начала сбора контента — иначе первый запуск вытянет всё с первого поста")
        end = AgentTaskManager.parse_date(end_date)
        payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=False))
        payload["force_reanalyze"] = False
        payload["collect_before_analyze"] = True
    elif AgentTaskManager.requires_content_dates(job_type) and window_type == "rolling":
        payload.pop("cli_dates", None)
    if job_type == "digest":
        payload.pop("task_plan", None)
        if digest_period and digest_period in ("day", "week", "month"):
            payload["period"] = digest_period
        if digest_group_by:
            payload["group_by"] = digest_group_by
        if digest_time_breakdown:
            payload["time_breakdown"] = True
    return payload


def _task_plan_from_form(
    goal,
    platforms,
    source_scope,
    source_ids,
    scenario_id,
    job_type,
    cron_expr,
    payload,
    window_type,
    rolling_days,
    trigger_timezone="",
    vk_access_raw="",
    existing_vk_access=None,
    compose_digest=False,
    digest_window_type="rolling",
    digest_rolling_days=7,
    digest_start_date="",
    digest_end_date="",
    digest_group_by="themes",
    digest_time_breakdown=False,
    include_collection=True,
) -> dict | None:
    """Build the additive canonical plan from the current task form."""
    if source_scope == "legacy":
        return None
    from app.services.task_plans import TaskPlanError, parse_task_plan, task_plan_block

    if source_scope not in {"selected", "discovery", "all"}:
        raise TaskPlanError("Выберите область источников задачи")
    selected_ids = source_ids if source_scope == "selected" else []
    collection_input = dict(payload.get("collection") or {})
    if window_type == "rolling":
        window = {"type": "rolling", "days": rolling_days}
        collection_input.pop("date_window", None)
    else:
        cli_dates = payload.get("cli_dates") if isinstance(payload.get("cli_dates"), dict) else {}
        window = {"type": "fixed", "start": cli_dates.get("start_date"), "end": cli_dates.get("end_date")}
    trigger = {"type": "once"} if cron_expr == "@once" else {"type": "schedule", "cron": cron_expr}
    selection_raw = vk_access_raw.strip() if isinstance(vk_access_raw, str) else ""
    if selection_raw:
        import json

        try:
            vk_access = json.loads(selection_raw)
        except (TypeError, ValueError):
            raise TaskPlanError("Некорректно выбрано подключение ВКонтакте") from None
    else:
        vk_access = existing_vk_access
        if vk_access is None:
            vk_access = (payload.get("task_plan") or {}).get("vk_access")
    if trigger["type"] == "schedule" and trigger_timezone.strip():
        trigger["timezone"] = trigger_timezone.strip()
    raw = {
        "version": 2 if compose_digest else 1,
        "goal": goal.strip() or None,
        "platforms": list(dict.fromkeys(platforms or ["vk"])),
        "source_scope": source_scope,
        "source_ids": selected_ids,
        "collection": collection_input,
        "window": window,
        "scenario_id": scenario_id,
        "trigger": trigger,
    }
    if vk_access is not None:
        raw["vk_access"] = vk_access
    if compose_digest:
        if job_type != "analyze":
            raise TaskPlanError("A digest pipeline requires an analysis task")
        if digest_window_type == "fixed":
            digest_window = {"type": "fixed", "start": digest_start_date or None, "end": digest_end_date or None}
        else:
            digest_window = {"type": "rolling", "days": digest_rolling_days}
        raw["pipeline"] = {"stages": (["collect"] if include_collection else []) + ["analyze", "digest"]}
        raw["digest"] = {
            "window": digest_window,
            "group_by": digest_group_by or "themes",
            "time_breakdown": digest_time_breakdown is True or digest_time_breakdown == "true",
        }
    return task_plan_block(parse_task_plan(raw))


def _trigger_and_action_fields(
    job_type: str,
    trigger_type: str,
    trigger_keywords: str,
    trigger_match: str,
    trigger_usernames: str,
    trigger_threshold: str,
    trigger_direction: str,
    trigger_baseline_hours: str,
    trigger_spike_multiplier: str,
    action_type: str,
    rate_limit_per_hour: str,
    cooldown_seconds: str,
) -> dict:
    """The model columns (not payload) an analyze task carries.

    No-op for every other job type — the fields are only rendered for
    `analyze`. Explicitly (re)setting guards lets the operator clear them: an
    update that omits the key keeps the old value, which is what the "merge into
    payload" rule is *not* meant to apply to.
    """
    fields: dict = {}
    if job_type != "analyze":
        return fields

    trigger = BotTriggerType.get_by_name(trigger_type) if trigger_type else None
    fields["trigger_type"] = trigger
    fields["trigger_config"] = (
        _build_trigger_config(
            trigger_type,
            trigger_keywords,
            trigger_match,
            trigger_usernames,
            trigger_threshold,
            trigger_direction,
            trigger_baseline_hours,
            trigger_spike_multiplier,
        )
        or None
    )

    fields["action_type"] = AgentActionType.get_by_name(action_type) if action_type else None
    try:
        fields["rate_limit_per_hour"] = int(rate_limit_per_hour) if rate_limit_per_hour else None
        fields["cooldown_seconds"] = int(cooldown_seconds) if cooldown_seconds else None
    except ValueError:
        fields["rate_limit_per_hour"] = None
        fields["cooldown_seconds"] = None
    return fields


def _edit_payload(task: AgentTask, effective_active: set[int], source_ids: list[int]) -> dict[str, Any]:
    """Everything the edit page binds, as the plain object Alpine edits.

    One builder, because the editor opened from two places (the row's name
    button and the `?task_id=` deep link, both landing on
    `/app/tasks/{id}/edit`) must fill identically. `is_active` is the
    *effective* flag — the one the list column shows — so reopening a task
    that is active on paper but blocked by a deactivated source does not
    silently activate it. The trigger/action, date and target fields are
    bound too, so editing a task shows what it is configured with instead of
    dropping the sections empty. `source_ids` is passed in by the caller,
    which has already resolved the lazy m2m on the detached row.
    """
    payload = task.payload if isinstance(task.payload, dict) else {}
    saved_plan = payload.get("task_plan") if isinstance(payload.get("task_plan"), dict) else {}
    digest_plan = saved_plan.get("digest") if isinstance(saved_plan.get("digest"), dict) else {}
    digest_window = digest_plan.get("window") if isinstance(digest_plan.get("window"), dict) else {}
    pipeline = saved_plan.get("pipeline") if isinstance(saved_plan.get("pipeline"), dict) else {}
    collection_edit = _collection_edit(payload)
    trigger_config = task.trigger_config if isinstance(task.trigger_config, dict) else {}
    edit: dict[str, Any] = {
        "id": task.id,
        "name": task.name,
        "job_type": _normalize_web_job_type(task.job_type),
        "cron_expr": task.cron_expr,
        "is_active": task.id in effective_active,
        "source_ids": source_ids,
        "trigger_timezone": ((payload.get("task_plan") or {}).get("trigger") or {}).get("timezone") or "",
        "task_goal": (payload.get("task_plan") or {}).get("goal") or "",
        "source_scope": (payload.get("task_plan") or {}).get("source_scope", "selected" if source_ids else "all"),
        "platforms": (payload.get("task_plan") or {}).get("platforms") or ["vk"],
        "vk_access_json": (
            __import__("json").dumps((payload.get("task_plan") or {}).get("vk_access"), separators=(",", ":"))
            if (payload.get("task_plan") or {}).get("vk_access")
            else ""
        ),
        "window_type": ((payload.get("task_plan") or {}).get("window") or {}).get(
            "type", "fixed" if payload.get("cli_dates") else "rolling"
        ),
        "rolling_days": ((payload.get("task_plan") or {}).get("window") or {}).get("days", 1),
        "scenario_id": task.agent_scenario_id,
        "tracked_users": ", ".join(payload.get("tracked_users") or []),
        "keywords_match": payload.get("keywords_match") or "any",
        "start_date": collection_edit["start_date"],
        "end_date": collection_edit["end_date"],
        "digest_period": payload.get("period") or "day",
        "compose_digest": bool(saved_plan.get("pipeline")),
        "include_collection": "collect" in pipeline.get("stages", []),
        "digest_window_type": digest_window.get("type", "rolling"),
        "digest_rolling_days": digest_window.get("days", 7),
        "digest_start_date": digest_window.get("start", "") or "",
        "digest_end_date": digest_window.get("end", "") or "",
        "digest_group_by": digest_plan.get("group_by", payload.get("group_by") or "themes"),
        "digest_time_breakdown": digest_plan.get("time_breakdown", bool(payload.get("time_breakdown"))),
        "retention_success_hours": payload.get("success_hours", 24),
        "retention_failure_hours": payload.get("failure_hours", 24),
        "trigger_type": getattr(task, "trigger_type", None).name if getattr(task, "trigger_type", None) else "",
        "trigger_keywords": ", ".join(trigger_config.get("keywords") or []),
        "trigger_match": trigger_config.get("match", "any"),
        "trigger_usernames": ", ".join(trigger_config.get("usernames") or []),
        "trigger_threshold": trigger_config.get("threshold", 0.5),
        "trigger_direction": trigger_config.get("direction", "below"),
        "trigger_baseline_hours": trigger_config.get("baseline_period_hours", 24),
        "trigger_spike_multiplier": trigger_config.get("spike_multiplier", 3.0),
        "action_type": getattr(task, "action_type", None).name if getattr(task, "action_type", None) else "",
        "rate_limit_per_hour": task.rate_limit_per_hour or "",
        "cooldown_seconds": task.cooldown_seconds or "",
        "dry_run": bool(getattr(task, "dry_run", False)),
    }
    # The analysis targets live in payload under their own keys (brands, …) and
    # are edited as comma separated strings, like the selection fields above.
    # The selection's own phrases are edited through the keywords field, so they
    # are appended here only when no other target uses them.
    edit["keywords_list"] = ", ".join(payload.get("keywords_list") or [])
    for key in TARGET_KEYS:
        edit[key] = ", ".join(payload.get(key) or [])
    edit.update({key: value for key, value in collection_edit.items() if key not in ("start_date", "end_date")})
    return edit


async def _check_task_sources(source_ids: list[int], tenant_id: int, task_id: int | None = None) -> None:
    """Reject source ids that are missing or belong to another workspace.

    The m2m link table has no tenant column, so a raw POST could otherwise
    attach somebody else's source to this task and make the job read it. A
    task-scoped query lane (`owner_task_id`) may only be linked to the task that
    owns it — it is not a pickable source for anyone else.

    A *missing* id is the stale-form case: the page was rendered before another
    tab (or another member) deleted that source. Refusing it by name is what
    keeps the save honest — without it the task update commits `is_active=True`
    and only the link write fails, leaving an active task with no links, which
    the runtime reads as "all active sources".
    """
    from app.models import Source

    if not source_ids:
        return
    sources = {s.id: s for s in await Source.objects.filter(id__in=source_ids)}
    missing = sorted(set(source_ids) - set(sources))
    if missing:
        raise ValueError(
            f"Источник(и) {', '.join(map(str, missing))} больше не существует — обновите страницу и выберите источники заново"
        )
    foreign = sorted(sid for sid, s in sources.items() if s.tenant_id != tenant_id)
    if foreign:
        raise ValueError(f"Источник(и) {', '.join(map(str, foreign))} принадлежат другому воркспейсу")
    stolen = sorted(sid for sid, s in sources.items() if s.owner_task_id is not None and s.owner_task_id != task_id)
    if stolen:
        raise ValueError(f"Источник(и) {', '.join(map(str, stolen))} принадлежат поиску другой задачи")


async def _replace_task_sources(
    task_id: int,
    source_ids: list[int],
    tenant_id: int,
    *,
    session: Any = None,
) -> None:
    """Replace the task's m2m source links with the given set.

    Pass `session` from a caller that also writes the task row: the links then
    commit or roll back with it, so a refused link write cannot leave a
    half-saved task behind.
    """
    from app.models.managers.agent_task_manager import AgentTaskManager

    await _check_task_sources(source_ids, tenant_id, task_id=task_id)
    await AgentTaskManager().set_sources(task_id, source_ids, session=session)


async def _activate_selected_sources(tenant_id: int, source_ids: list[int]) -> None:
    """Flip a checked-but-deactivated source active.

    The operator ticked the source for a reason — it is the source this task
    should collect from — so a checked row that stays off would either keep the
    task unactivatable or make it run over "all active" and ignore exactly the
    source that was picked. Checked sources become active, inside the workspace
    scope. Runs *before* `_can_activate`, so a task whose only flaw was a
    deactivated source becomes activatable in the same save.
    """
    from app.core.tenant_context import tenant_scope
    from app.models import Source

    if not source_ids:
        return
    with tenant_scope(tenant_id):
        for source in await Source.objects.filter(id__in=source_ids):
            if not source.is_active:
                await Source.objects.update_by_id(source.id, is_active=True)


async def _can_activate(
    tenant_id: int,
    job_type: str,
    source_ids: list[int],
    scenario_id: int | None,
    *,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """Return why the task cannot be activated, or None if it can.

    A task is only activatable when its scenario (if one is chosen) is active
    and, for a source-based job type (collect/analyze), it has at least one
    active source to operate on: an explicit active linked source, or (with no
    linked sources, which works over "all active sources") any active source in
    the workspace. Workspace/memory-level types need no source.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.agent_task_manager import AgentTaskManager

    with tenant_scope(tenant_id):
        if scenario_id is not None:
            scenario = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            if scenario is None or not scenario.is_active:
                return "выбранный сценарий деактивирован"

        if not AgentTaskManager.requires_sources(job_type):
            return None
        plan = payload.get("task_plan") if isinstance(payload, dict) else None
        collection = plan.get("collection") if isinstance(plan, dict) else None
        discovery = collection.get("discovery") if isinstance(collection, dict) else None
        if (
            isinstance(plan, dict)
            and plan.get("source_scope") == "discovery"
            and isinstance(collection, dict)
            and (
                collection.get("queries")
                or (
                    isinstance(discovery, dict)
                    and discovery.get("queries")
                    and (discovery.get("people") or discovery.get("communities"))
                )
            )
        ):
            return None
        if source_ids:
            sources = await Source.objects.filter(id__in=source_ids)
            inactive = sorted(s.name for s in sources if not s.is_active)
            if inactive and not any(s.is_active for s in sources):
                names = ", ".join(inactive)
                return f"все привязанные источники деактивированы: {names}"
            return None

        active = await Source.objects.filter(is_active=True).values(Source.id).rows()
        if not active:
            return f"для типа «{job_type}» не выбран ни один источник, а в воркспейсе нет активных источников"
        return None


async def _unified_analysis_scenario_block(
    tenant_id: int, scenario_id: int | None, *, allow_inactive_selected: bool = False
) -> str | None:
    """Require methodology for web-created collect-and-analyze tasks."""
    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        if scenario_id is not None:
            scenario = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            if scenario is None:
                return "выбранный сценарий недоступен в этом workspace"
            return None if scenario.is_active or allow_inactive_selected else "выбранный сценарий деактивирован"
        default_scenario = await AgentScenario.objects.get_default_scenario(tenant_id=tenant_id)
        if default_scenario is not None:
            return None
    return "создайте активный сценарий и выберите его для анализа"


async def _duplicate_name(request: Request, back: str, name: str, exclude_id: int | None = None) -> Response | None:
    """Refuse a task name already taken in this workspace.

    `(tenant_id, name)` is unique, so the insert would raise `IntegrityError`
    and surface as a 500 — which is what a second click on "Сохранить и
    выполнить" produced: the first click had already created the row and was
    still running its job, so the retry collided with itself and the operator
    saw a stack trace instead of a message. Checked here so the collision is
    reported as what it is; the unique constraint stays the real guard, since a
    check and an insert cannot be made atomic from here.
    """
    query = AgentTask.objects.filter(name=name, tenant_id=request.state.tenant_id)
    if exclude_id is not None:
        query = query.exclude(id=exclude_id)
    existing = await query.first()
    if existing is None:
        return None
    add_flash(
        request,
        "error",
        f"Задача «{name}» уже существует. Выберите другое название или откройте её для повторного запуска.",
    )
    return RedirectResponse(back, status_code=302)

__all__ = [name for name in globals() if not name.startswith("__")]
