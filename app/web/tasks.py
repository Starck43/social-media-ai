"""Tasks `/app/tasks` — create and manage scheduled agent tasks.

Flow:
- sources are linked via the m2m `agent_task_sources` table (not payload)
- scenario is set via `agent_scenario_id` FK (not payload)
- payload keeps flat keys: period, tracked_users, keywords_list, ...
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.models import AgentScenario, AgentTask, Job, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.tasks.cron import cron_to_human
from app.jobs.progress import read_snapshot
from app.web.task_progress import project_run, sidebar_sources
from app.jobs.recovery_policy import CANCELLED_BEFORE_START_ERROR, OPERATOR_CANCELLED_ERROR, outcome_unconfirmed
from app.types import AgentActionType, BotTriggerType, JobType
from app.services.collection.intent import (
	COLLECTION_JOB_TYPES,
	CITY_MODES,
	CITY_PREFER,
	CONFIRM_MODES,
	CollectionIntent,
	CollectionIntentError,
	apply_collection,
	collection_error_message,
	parse_collection_intent,
)
from app.services.monitoring.selection import SelectionError

from .deps import (
	action_tenant_id,
	add_flash,
	ensure_csrf,
	guard_web,
	perms_can,
	plural,
	render,
	safe_next,
	tenant_filter_context,
)

router = APIRouter(prefix="/tasks")
logger = logging.getLogger(__name__)


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
		raise ValueError(
			"Проверьте «Отслеживаемые авторы»: укажите платформу и id, например vk:123456"
		) from None
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
		job_type, start_date, end_date,
		digest_period=None, digest_group_by=None, digest_time_breakdown=False,
		retention_success_hours=24, retention_failure_hours=24, window_type="fixed",
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
		goal, platforms, source_scope, source_ids, scenario_id, job_type, cron_expr, payload,
		window_type, rolling_days, trigger_timezone="",
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
	if trigger["type"] == "schedule" and trigger_timezone.strip():
		trigger["timezone"] = trigger_timezone.strip()
	raw = {
		"version": 1,
		"goal": goal.strip() or None,
		"platforms": list(dict.fromkeys(platforms or ["vk"])),
		"source_scope": source_scope,
		"source_ids": selected_ids,
		"collection": collection_input,
		"window": window,
		"scenario_id": scenario_id,
		"trigger": trigger,
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
		"source_scope": (payload.get("task_plan") or {}).get(
			"source_scope", "selected" if source_ids else "all"
		),
		"platforms": (payload.get("task_plan") or {}).get("platforms") or ["vk"],
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
		"digest_group_by": payload.get("group_by") or "themes",
		"digest_time_breakdown": bool(payload.get("time_breakdown")),
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
	stolen = sorted(
		sid for sid, s in sources.items() if s.owner_task_id is not None and s.owner_task_id != task_id
	)
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


async def run_task_now(task: "AgentTask") -> dict | None:
	"""Run the task's job right now, in this process — it never sits in the queue.

	"Собрать сейчас" has to feel immediate, and a queued job is not: it waits for a
	worker, and if the worker is down or busy the user just sees a spinner that
	times out. So the job row is written and executed in the same call — the row
	still exists (it is the audit trail the result modal and `/app/jobs` read),
	but it is stamped `running` immediately, so the worker never picks it up.

	Returns the dispatcher's outcome dict plus `job_id` (the callers redirect to
	`/app/tasks?job_id=…` so the modal can render it).
	"""
	from app.jobs.dispatcher import run_task_directly

	return await run_task_directly(task)


async def queue_task_now(task: "AgentTask", initiated_by_user_id: int | None = None) -> dict[str, Any]:
	"""Enqueue the task's job and execute it in this process (no worker needed).

	"Сохранить и выполнить" used to call `run_task_now`, which executes the
	handler synchronously and only then returns. For a long job that is minutes
	of an open spinner with no way to tell a slow run from a hung one — an
	`analyze` task draining a backlog took over ten minutes, and the operator's
	natural reaction was to click again, which collided with the row the first
	click had already written.

	Queueing returns as soon as the job row exists, so the page reloads at once
	and the run-status modal (which already polls `/app/tasks/job/<id>/status`)
	follows the job to completion. The job executes in a background asyncio task
	in the same process (uvicorn event loop), so manual runs work even when
	`app.runtime` worker is not running (dev: uvicorn alone). A competing real
	worker may claim the job first (atomic claim) — either way the job runs.

	Returns the same `{job_id, status}` shape so both callers read alike.
	"""
	from app.jobs.dispatcher import launch_job_background
	from app.jobs.enqueue import enqueue_task_run
	from app.jobs.source_activity import SourceJobBusy
	from app.models.managers.agent_task_manager import AgentTaskManager

	linked_sources = await AgentTaskManager().get_sources(task.id)
	blocked = await _can_activate(
		task.tenant_id, task.job_type, linked_sources, task.agent_scenario_id, payload=task.payload
	)
	if blocked is not None:
		return {"status": "blocked", "reason": blocked}

	if initiated_by_user_id is None:
		from app.core.permissions import get_current_user
		actor = get_current_user()
		initiated_by_user_id = getattr(actor, "id", None)

	try:
		job = await enqueue_task_run(
			task,
			extra_payload={"force_refresh": False, "force_reanalyze": False},
			initiated_by_user_id=initiated_by_user_id,
			supersede_unfinished=True,
		)
	except SourceJobBusy as busy:
		return {"job_id": busy.job.id, "status": busy.job.status, "already_running": True}

	# Execute in this process's event loop so the job runs without a worker.
	launch_job_background(job.id, allow_retry=False)
	return {"job_id": job.id, "status": job.status}


@router.get("")
@router.get("/")
async def tasks_list(request: Request):
	tenant_id = request.state.tenant_id
	user = getattr(request.state, "web_user", None)
	is_superuser = False
	filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

	if is_superuser:
		from app.core.tenant_context import tenant_scope

		with tenant_scope(bypass=True):
			query = AgentTask.objects.prefetch_related("sources", "agent_scenario", "tenant")
			if filter_tenant_id is not None:
				query = query.filter(tenant_id=filter_tenant_id)
			tasks = await query.order_by(AgentTask.created_at.desc())
	else:
		tasks = await (
			AgentTask.objects.filter(tenant_id=tenant_id)
			.prefetch_related("sources", "agent_scenario")
			.order_by(AgentTask.created_at.desc())
		)
	if not _can_manage_job_retention(request):
		tasks = [task for task in tasks if task.job_type != "prune"]

	sources = await Source.objects.filter(tenant_id=tenant_id, owner_task_id=None).order_by(Source.name)

	scenarios = await AgentScenario.objects.filter(tenant_id=tenant_id, is_active=True).order_by(AgentScenario.name)
	permitted_source_ids = {source.id for source in sources}
	prefill_source_ids = []
	for raw_source_id in request.query_params.getlist("source_id") + request.query_params.getlist("source_ids"):
		for candidate in raw_source_id.split(","):
			if candidate.isdigit() and int(candidate) in permitted_source_ids:
				prefill_source_ids.append(int(candidate))
	prefill_source_ids = list(dict.fromkeys(prefill_source_ids))
	raw_scenario_id = request.query_params.get("scenario_id", "")
	prefill_scenario_id = (
		int(raw_scenario_id)
		if raw_scenario_id.isdigit() and any(scenario.id == int(raw_scenario_id) for scenario in scenarios)
		else None
	)
	open_add_modal = request.query_params.get("create") == "1" and perms_can(request, "agenttask", "create")
	# Which payload target keys each scenario wants (param_registry.PAYLOAD_PARAMS):
	# drives the conditional target inputs in the add modal, as on the edit page.
	scenario_analysis = {str(s.id): list(s.analysis_types or []) for s in scenarios}

	# Effective activity: a task bound to a deactivated scenario or with no
	# active source to operate on is not "active", however its flag is set.
	from app.models.managers.agent_task_manager import AgentTaskManager

	active_source_ids = {s.id for s in sources if s.is_active}
	effective = await AgentTaskManager().effective_active_map(tasks, active_source_ids)
	effective_active = {tid for tid, ok in effective.items() if ok}

	# Collect running/pending jobs per task so the UI can disable the "run now"
	# button and point the user to the existing modal instead of creating a
	# duplicate job. The status rides along: a pending job has not been claimed
	# by any worker yet, and the row says "queued", not "running".
	from app.models.job import Job

	task_ids = [t.id for t in tasks]
	running_jobs: dict[int, dict[str, Any]] = {}  # task_id -> {id, status}
	if task_ids:
		for job in await Job.objects.filter(
			agent_task_id__in=task_ids,
			status__in=["pending", "running"],
		):
			running_jobs[job.agent_task_id] = {"id": job.id, "status": job.status}

	raw_job_id = request.query_params.get("job_id")
	job_id = int(raw_job_id) if raw_job_id and raw_job_id.isdigit() else None

	raw_task_id = request.query_params.get("task_id")
	open_task_id = int(raw_task_id) if raw_task_id and raw_task_id.isdigit() else None
	if open_task_id is not None:
		# The editor moved from a modal to its own page; the shared `?task_id=`
		# link must land there, not open a modal the page no longer carries.
		can_edit = perms_can(request, "agenttask", "update")
		return RedirectResponse(
			f"/app/tasks/{open_task_id}/edit" if can_edit else "/app/tasks",
			status_code=303,
		)

	return render(
		request,
		"web/tasks.html",
		section="tasks",
		tasks=tasks,
		sources=sources,
		source_groups=_group_sources(sources),
		collection_defaults=_collection_edit({}),
		target_fields=TARGET_FIELD_DEFS,
		scenarios=scenarios,
		scenario_analysis=scenario_analysis,
		prefill_source_ids=prefill_source_ids,
		prefill_scenario_id=prefill_scenario_id,
		prefill_job_type=(
			"prune"
			if request.query_params.get("job_type") == "prune" and _can_manage_job_retention(request)
			else "analyze"
		),
		prefill_retention=request.query_params.get("job_type") == "prune" and _can_manage_job_retention(request),
		open_add_modal=open_add_modal,
		effective_active=effective_active,
		unconfirmed_tasks={task.id for task in tasks if outcome_unconfirmed(task.last_error)},
		running_jobs=running_jobs,
		job_types=_web_job_type_choices(allow_retention=_can_manage_job_retention(request)),
		trigger_types=BotTriggerType.choices(),
		action_types=AgentActionType.choices(),
		cron_to_human=cron_to_human,
		job_id=job_id,
		is_superuser=is_superuser,
		tenants=tenants,
		filter_tenant_id=filter_tenant_id,
	)


@router.post("")
async def task_create(
		request: Request,
		name: str = Form(...),
		job_type: str = Form(...),
		cron_custom: str = Form(...),
		source_ids: list[str] = Form([]),
		source_scope: str = Form("legacy"),
		task_goal: str = Form(""),
		platforms: list[str] = Form([]),
		window_type: str = Form("fixed"),
		rolling_days: int | None = Form(default=None),
		trigger_timezone: str = Form(""),
		scenario_id: int = Form(default=None),
		tracked_users: str = Form(""),
		keywords_match: str = Form("any"),
		brands: str = Form(""),
		competitors: str = Form(""),
		hashtags: str = Form(""),
		influencer_names: str = Form(""),
		topic_list: str = Form(""),
		keywords_list: str = Form(""),
		start_date: str = Form(""),
		end_date: str = Form(""),
		digest_period: str = Form(""),
		digest_group_by: str = Form(""),
		digest_time_breakdown: str = Form(""),
		retention_success_hours: int = Form(24),
		retention_failure_hours: int = Form(24),
		trigger_type: str = Form(""),
		trigger_keywords: str = Form(""),
		trigger_match: str = Form("any"),
		trigger_usernames: str = Form(""),
		trigger_threshold: str = Form(""),
		trigger_direction: str = Form("below"),
		trigger_baseline_hours: str = Form(""),
		trigger_spike_multiplier: str = Form(""),
		action_type: str = Form(""),
		rate_limit_per_hour: str = Form(""),
		cooldown_seconds: str = Form(""),
		dry_run: str = Form(""),
		run_now: str = Form(""),
		collection_configured: str = Form(""),
		collection_people: str = Form(""),
		collection_communities: str = Form(""),
		collection_discovery_queries: str = Form(""),
		collection_discovery_queries_match: str = Form("any"),
		collection_discovery_city: str = Form(""),
		collection_discovery_city_id: str = Form(""),
		collection_queries: str = Form(""),
		collection_queries_match: str = Form("any"),
		collection_content_types: list[str] = Form([]),
		collection_engagement_types: list[str] = Form([]),
		collection_limit: str = Form(""),
		collection_comments_max_per_post: str = Form(""),
		collection_comments_total: str = Form(""),
		collection_candidates_limit: str = Form(""),
		collection_candidates_confirm: str = Form("manual"),
		token: str = Form("", alias="_csrf"),
		tenant_id: int | None = Form(default=None),
		# Where to land after creating — the onboarding wizard posts here too, and
		# every failure below has to come back to the page that was filled in.
		next: str = Form(""),
):
	back = safe_next(next) or "/app/tasks"
	# `None` when the caller stayed on /app/tasks; the run-status modal only
	# exists there, so an external caller (onboarding) takes the plain flash.
	return_to = safe_next(next)

	if not ensure_csrf(request, token):
		add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
		return RedirectResponse(back, status_code=302)
	job_type = _normalize_web_job_type(job_type)
	if job_type == "prune" and not _can_manage_job_retention(request):
		add_flash(request, "error", "Настраивать очистку истории заданий могут только администратор и владелец пространства")
		return RedirectResponse(back, status_code=302)

	tenant_id = action_tenant_id(request, tenant_id)

	denied = guard_web(request, "agenttask", "create", back=back)
	if denied is not None:
		return denied

	# A rehearsal is an operator diagnostic: only an administrator or owner may
	# put a task into it, and the toggle never silently applies to a task the
	# person cannot manage.
	dry_run_flag = dry_run == "on"
	if dry_run_flag and not _can_manage_job_retention(request):
		add_flash(request, "warning", "Тестовый прогон доступен только администратору и владельцу пространства")
		dry_run_flag = False

	cron_expr = cron_custom.strip()

	# Before anything is written: a repeat of the same form (a second click on
	# "Создать и выполнить" while the first is still running) must not reach the
	# unique constraint as an IntegrityError.
	clash = await _duplicate_name(request, back, name.strip()[:100])
	if clash is not None:
		return clash

	from app.models.managers.agent_task_manager import AgentTaskManager

	tasks_mgr = AgentTaskManager()
	if not tasks_mgr.validate_cron(cron_expr):
		add_flash(request, "error", f"Некорректное cron-выражение: {cron_expr}")
		return RedirectResponse(back, status_code=302)
	if job_type == "prune" and cron_expr == "@once":
		add_flash(request, "error", "Настройка хранения истории должна быть повторяемой, например ежедневно.")
		return RedirectResponse(back, status_code=302)

	parsed_source_ids: list[int] = []
	if AgentTaskManager.requires_sources(job_type):
		for s in source_ids:
			s = s.strip()
			if s.isdigit():
				parsed_source_ids.append(int(s))

	try:
		payload = _payload_from_form(
			job_type,
			start_date,
			end_date,
			digest_period=digest_period,
			digest_group_by=digest_group_by,
			digest_time_breakdown=digest_time_breakdown == "on",
			retention_success_hours=retention_success_hours,
			retention_failure_hours=retention_failure_hours,
			window_type=window_type,
		)
		if job_type != "prune":
			apply_selection(payload, tracked_users, _split_phrases(keywords_list), keywords_match)
	except ValueError as e:
		add_flash(request, "error", str(e))
		return RedirectResponse(back, status_code=302)

	if job_type != "prune":
		_apply_targets(
			payload,
			{
				"brands": brands,
				"competitors": competitors,
				"hashtags": hashtags,
				"influencer_names": influencer_names,
				"topic_list": topic_list,
			},
		)

	# Collection intent is written only when the submitted form carried it: an
	# older client that posts the legacy fields alone keeps the task's stored
	# block instead of silently resetting it to posts.
	if collection_configured == "1":
		if job_type in ("collect", "analyze") and window_type == "rolling":
			payload.pop("cli_dates", None)
		try:
			collection_block = _collection_block(
				job_type,
				people=collection_people,
				communities=collection_communities,
				discovery_queries=collection_discovery_queries,
				discovery_queries_match=collection_discovery_queries_match,
				discovery_city=collection_discovery_city,
				discovery_city_id=collection_discovery_city_id,
				queries=collection_queries,
				queries_match=collection_queries_match,
				content_types=collection_content_types,
				engagement_types=collection_engagement_types,
				limit=collection_limit,
				comments_max_per_post=collection_comments_max_per_post,
				comments_total=collection_comments_total,
				candidates_limit=collection_candidates_limit,
				candidates_confirm=collection_candidates_confirm,
				date_window=payload.get("cli_dates"),
			)
		except ValueError as e:
			add_flash(request, "error", str(e))
			return RedirectResponse(back, status_code=302)
		if collection_block:
			payload["collection"] = collection_block
		else:
			payload.pop("collection", None)

	task_plan = None
	if job_type in ("collect", "analyze"):
		try:
			task_plan = _task_plan_from_form(
				task_goal, platforms, source_scope, parsed_source_ids, scenario_id,
				job_type, cron_expr, payload, window_type, rolling_days, trigger_timezone,
			)
			if task_plan is not None:
				from app.services.task_plans import apply_task_plan

				payload = apply_task_plan(payload, task_plan, job_type=job_type)
				parsed_source_ids = list(task_plan["source_ids"])
		except ValueError as error:
			add_flash(request, "error", f"Проверьте план задачи: {error}")
			return RedirectResponse(back, status_code=302)

	if job_type == "analyze":
		# A deactivated scenario may be chosen on purpose (it is on the task's
		# list and can be reactivated later), so the task is created and starts
		# inactive with the reason — the same contract the edit path has.
		scenario_block = await _unified_analysis_scenario_block(
			tenant_id, scenario_id, allow_inactive_selected=True
		)
		if scenario_block is not None:
			add_flash(request, "error", f"Для анализа соцсетей {scenario_block}")
			return RedirectResponse(back, status_code=302)

	# Warn about missing target params for the scenario's analysis types.
	# Non-blocking: the task is still created, but analysis will have nothing
	# specific to look for without these.
	if scenario_id and job_type != "prune":
		sc = await AgentScenario.objects.filter(id=scenario_id).first()
		if sc and sc.analysis_types:
			from app.services.ai.param_registry import missing_target_params

			if job_type == "analyze":
				missing = missing_target_params(sc.analysis_types, payload)
			else:
				missing = []
			if missing:
				add_flash(
					request,
					"warning",
					f"⚠️ Для сценария «{sc.name}» укажите в параметрах задачи: " + ", ".join(missing),
				)

	trigger_action_fields = _trigger_and_action_fields(
		job_type,
		trigger_type,
		trigger_keywords,
		trigger_match,
		trigger_usernames,
		trigger_threshold,
		trigger_direction,
		trigger_baseline_hours,
		trigger_spike_multiplier,
		action_type,
		rate_limit_per_hour,
		cooldown_seconds,
	)

	from app.core.tenant_context import tenant_scope
	from app.models.managers.tenant_manager import tenants
	from app.tasks.cron import next_run_at, resolve_tz

	# The workspace zone wins; the global setting is only the fallback. Must
	# agree with the runner, which re-advances the schedule in the same zone.
	tenant = await tenants.get(id=tenant_id)
	from app.services.task_plans import task_plan_timezone
	tz = task_plan_timezone(task_plan, resolve_tz(tenant))

	if cron_expr == "@once":
		next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
	else:
		next_run = next_run_at(cron_expr, tz)

	try:
		await _check_task_sources(parsed_source_ids, tenant_id)
	except ValueError as e:
		add_flash(request, "error", str(e))
		return RedirectResponse(back, status_code=302)

	# A ticked-but-off source activates with the save: the operator named the
	# source the task should run on, so it must not stay off and block the
	# activation check right below.
	if AgentTaskManager.requires_sources(job_type):
		await _activate_selected_sources(tenant_id, parsed_source_ids)

	# A task can only be created active when it has an active scenario and, for
	# a source-based type, at least one active source; otherwise it starts
	# inactive.
	activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id, payload=payload)
	if activation_blocked is not None:
		add_flash(
			request,
			"warning",
			f"Задача «{name}» создана неактивной: {activation_blocked}",
		)
	is_active = activation_blocked is None
	# New universal plans are saved as drafts until the creator establishes
	# payer consent. Existing non-plan tasks keep their prior activation path.
	needs_payer_consent = task_plan is not None and is_active

	from app.core.database import new_session

	session = new_session()
	try:
		with tenant_scope(tenant_id):
			async with session.begin():
				task = await AgentTask.objects.create(
					name=name.strip()[:100],
					job_type=job_type,
					cron_expr=cron_expr,
					payload=payload,
					agent_scenario_id=None if job_type == "prune" else scenario_id,
					is_active=False if needs_payer_consent else is_active,
					next_run_at=next_run,
					dry_run=dry_run_flag,
					session=session,
					**trigger_action_fields,
				)
				if parsed_source_ids:
					await _replace_task_sources(task.id, parsed_source_ids, tenant_id, session=session)
	except ValueError:
		# A source that vanished between the check and this write must not leave
		# an active task whose empty link set means "all active sources".
		add_flash(
			request,
			"error",
			"Источники задачи изменились во время сохранения — обновите страницу и повторите",
		)
		return RedirectResponse(back, status_code=302)
	finally:
		await session.close()

	if needs_payer_consent:
		from app.models.managers.personal_billing_manager import PersonalBillingError, PersonalBillingUnavailable
		from app.services.billing import consent_current_user_as_task_payer

		try:
			await consent_current_user_as_task_payer(task.id)
		except (PersonalBillingError, PersonalBillingUnavailable):
			add_flash(
				request,
				"warning",
				f"Задача «{name}» сохранена неактивной. Настройте личный AI-бюджет и подтвердите оплату для активации.",
			)
			return RedirectResponse(back, status_code=302)
		await AgentTask.objects.update_by_id(task.id, is_active=True)
		task = await AgentTask.objects.get(id=task.id, tenant_id=tenant_id)

	# "Создать и выполнить" only queues the job. It used to run the handler
	# inside this request, which for a long job (an `analyze` task draining a
	# backlog) meant minutes of a frozen page — and a second click, which then
	# collided with the row the first click had written. The run-status modal
	# follows the queued job instead.
	if run_now:
		outcome = await queue_task_now(task, getattr(request.state.web_user, "id", None))
		if outcome.get("already_running"):
			add_flash(request, "info", f"Задача «{name}» создана, но источник уже занят. Новый запуск не создан")
		else:
			add_flash(request, "success", f"Задача «{name}» создана и поставлена в очередь на выполнение")
		# The run-status modal lives on the tasks page; from onboarding there is
		# nothing to poll, so land on the caller's page with the flash instead.
		job_id = (outcome or {}).get("job_id")
		target = f"/app/tasks?job_id={job_id}" if job_id else "/app/tasks"
		return RedirectResponse(back if return_to else target, status_code=302)
	else:
		add_flash(request, "success", f"Задача «{name}» создана")
	return RedirectResponse(back, status_code=302)


@router.get("/{task_id}")
async def task_detail(request: Request, task_id: int):
	"""One task: what it does, what sources it touches, and its recent runs."""
	from contextlib import nullcontext

	from app.core.tenant_context import tenant_scope
	from app.models.agent_scenario import AgentScenario
	from app.models.agent_task import AgentTask
	from app.models.job import Job
	from app.models.source import Source

	user = getattr(request.state, "web_user", None)
	is_superuser = False
	filter_tenant_id, _tenants = await tenant_filter_context(request, is_superuser)

	source = None
	with tenant_scope(bypass=True) if is_superuser else nullcontext():
		source = (
			await AgentTask.objects.filter(id=task_id)
			.select_related("tenant", "agent_scenario")
			.prefetch_related("sources")
			.first()
		)
		if source is not None and not is_superuser and source.tenant_id != request.state.tenant_id:
			source = None
		if source is not None and source.job_type == "prune" and not _can_manage_job_retention(request):
			source = None
		if source is None:
			return render(
				request,
				"web/task_detail.html",
				section="tasks",
				task=None,
				filter_tenant_id=filter_tenant_id,
			)

		# Recent jobs for this task.
		recent_jobs = await (
			Job.objects.filter(agent_task_id=task_id, tenant_id=source.tenant_id)
			.order_by(Job.created_at.desc())
			.limit(10)
		)

		# Running/pending job for this task (UI guard against duplicate runs).
		# The status rides along: a pending job has not been claimed by any worker
		# yet, and the page says "queued", not "running".
		running_job_id: int | None = None
		running_job_status: str | None = None
		for job in recent_jobs:
			if job.status in ("pending", "running"):
				running_job_id = job.id
				running_job_status = job.status
				break
		if running_job_id is None:
			# The running job may be outside the recent-10 window.
			running_job = await Job.objects.filter(
				agent_task_id=task_id,
				tenant_id=source.tenant_id,
				status__in=["pending", "running"],
			).first()
			if running_job is not None:
				running_job_id = running_job.id
				running_job_status = running_job.status

		# Linked sources for display; effective activity also needs all active
		# tenant sources because an empty source set means "all active sources".
		tenant_sources = await Source.objects.filter(tenant_id=source.tenant_id)
		linked_ids = set(await _task_source_ids(task_id))
		linked_sources = sorted((item for item in tenant_sources if item.id in linked_ids), key=lambda item: item.name)
		active_source_ids = {item.id for item in tenant_sources if item.is_active}

		# Scenario.
		scenario = None
		if source.agent_scenario_id is not None:
			scenario = await AgentScenario.objects.get(id=source.agent_scenario_id, tenant_id=source.tenant_id)
		from app.models.managers.agent_task_manager import AgentTaskManager

		effective = await AgentTaskManager().effective_active_map([source], active_source_ids)
		effective_active = {task_pk for task_pk, active in effective.items() if active}

	# The same outcome classification the run-now modal uses, so a run reads
	# the same on this page and in the modal: a half-broken collect is not a
	# green "готово" in one place and "частично" in the other.
	job_outcomes = {
		job.id: _run_outcome(job.job_type, job.result or {}) for job in recent_jobs if job.status == "done"
	}
	run_scope = await _task_run_scope(source, include_sources=perms_can(request, "source", "view"))
	latest_job = next((item for item in recent_jobs if item.id == running_job_id), None)
	if latest_job is None:
		latest_job = running_job if running_job_id is not None else (recent_jobs[0] if recent_jobs else None)
	latest_run = _task_run_panel_data(request, source, latest_job, run_scope=run_scope)
	await _attach_task_llm_diagnostics(request, source, latest_job, latest_run)
	await _attach_task_lane_evidence(latest_run, latest_job)

	return render(
		request,
		"web/task_detail.html",
		section="tasks",
		task=source,
		filter_tenant_id=filter_tenant_id,
		recent_jobs=recent_jobs,
		running_job_id=running_job_id,
		running_job_status=running_job_status,
		job_outcomes=job_outcomes,
		effective_active=effective_active,
		latest_run=latest_run,
		sources=linked_sources,
		scenario=scenario,
		collection=_collection_edit(source.payload),
		cron_to_human=cron_to_human,
		JOB_TYPE_TITLES=JOB_TYPE_TITLES,
		perms_can=perms_can,
	)


async def _task_run_scope(task: Any, *, include_raw: bool = False, include_sources: bool = False) -> dict[str, Any] | None:
	"""Return task source scope and, when authorized, its current raw backlog."""
	if task.job_type not in ("collect", "analyze"):
		return None
	from app.core.tenant_context import tenant_scope
	from app.models.source import Source

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
		scope["inventory"] = [{"source_id": item.id, "name": item.name[:160]} for item in sorted(scoped, key=lambda item: (item.name, item.id))]
	if include_raw and task.job_type == "analyze":
		from app.models.collected_item import CollectedItem
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
		evidence.append({
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
		})
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
				steps.append({
					"source_id": row["source_id"],
					"name": row.get("name")[:80] if isinstance(row.get("name"), str) else "Источник",
					"state": "done",
					"note": _source_note(row),
					"error": failed,
				})
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


@router.get("/{task_id}/latest-progress")
async def task_latest_progress(request: Request, task_id: int):
	"""Refresh only the newest Job belonging to this tenant's requested task."""
	from fastapi.responses import HTMLResponse

	if not perms_can(request, "agenttask", "view"):
		return HTMLResponse("<p class=\"text-sm text-slate-600\">Нет доступа к задаче.</p>", status_code=403)
	tenant_id = getattr(request.state, "tenant_id", None)
	if type(tenant_id) is not int:
		return HTMLResponse("<p class=\"text-sm text-slate-600\">Задача не найдена.</p>", status_code=404)
	task = await AgentTask.objects.filter(id=task_id, tenant_id=tenant_id).first()
	if task is None:
		return HTMLResponse("<p class=\"text-sm text-slate-600\">Задача не найдена.</p>", status_code=404)
	job = await Job.objects.filter(agent_task_id=task.id, tenant_id=tenant_id, status__in=["pending", "running"]).order_by(Job.created_at.desc()).first()
	if job is None:
		job = await Job.objects.filter(agent_task_id=task.id, tenant_id=tenant_id).order_by(Job.created_at.desc()).first()
	run_scope = await _task_run_scope(task, include_sources=perms_can(request, "source", "view"))
	panel = _task_run_panel_data(request, task, job, run_scope=run_scope)
	await _attach_task_llm_diagnostics(request, task, job, panel)
	await _attach_task_lane_evidence(panel, job)
	return render(request, "web/task_run_progress.html", task=task, sidebar_oob=True, **panel)


@router.get("/{task_id}/edit")
async def task_edit_page(request: Request, task_id: int):
	"""The task editor as a page, not a modal.

	The edit form outgrew the viewport — a modal's height is bounded by the
	screen, this form already scrolls — so the editor lives at its own URI
	(`/app/tasks/{id}/edit`) like every other editor. The row's name button
	links here, and the old `?task_id=` parameter on the list page redirects
	here. The superuser branch mirrors `task_detail`: the task is read under
	bypass and its own tenant decides the page's sources and scenarios.
	"""
	from contextlib import nullcontext

	from app.core.tenant_context import tenant_scope

	user = getattr(request.state, "web_user", None)
	is_superuser = False
	filter_tenant_id, _ = await tenant_filter_context(request, is_superuser)

	denied = guard_web(request, "agenttask", "update", back="/app/tasks")
	if denied is not None:
		return denied

	with tenant_scope(bypass=True) if is_superuser else nullcontext():
		# `sources` is a lazy m2m and the row comes back detached — without a
		# prefetch touching it raises DetachedInstanceError (see `_task_source_ids`).
		task = (
			await AgentTask.objects.filter(id=task_id)
			.select_related("agent_scenario")
			.prefetch_related("sources")
			.first()
		)
		if task is not None and not is_superuser and task.tenant_id != request.state.tenant_id:
			task = None
		if task is None:
			add_flash(request, "error", "Задача не найдена")
			return RedirectResponse("/app/tasks", status_code=302)
		if task.job_type == "prune" and not _can_manage_job_retention(request):
			add_flash(request, "error", "Просматривать настройку хранения заданий могут только администратор и владелец пространства")
			return RedirectResponse("/app/tasks", status_code=302)
		sources = await Source.objects.filter(tenant_id=task.tenant_id).order_by(Source.name)
		# A query lane owns a task-scoped Source (`owner_task_id`): the owning
		# task may show it as a linked row, but it is never a pickable option
		# and another task never sees it at all.
		sources = [source for source in sources if source.owner_task_id in (None, task.id)]
		scenarios = await AgentScenario.objects.filter(tenant_id=task.tenant_id, is_active=True).order_by(
			AgentScenario.name
		)
		# The dropdown lists active scenarios, but the task's own scenario must
		# show even when it is inactive: it was set (via admin/CLI) and the
		# editor must render what the task is bound to, not silently read as
		# empty. Without this a deactivated scenario made the field look blank.
		if task.agent_scenario_id is not None and not any(s.id == task.agent_scenario_id for s in scenarios):
			bound = await AgentScenario.objects.get(id=task.agent_scenario_id, tenant_id=task.tenant_id)
			if bound is not None:
				scenarios = [bound, *scenarios]

	# Resolve the m2m into a plain list: the row is detached here, and
	# `_edit_payload` must not hit a lazy loader on it (DetachedInstanceError).
	source_ids_for_task: list[int] = []
	if task.sources is not None:
		try:
			source_ids_for_task = [s.id for s in task.sources]
		except Exception:
			# Fallback: read via the manager when the relationship is already
			# garbage-collected or otherwise inaccessible.
			source_ids_for_task = await AgentTaskManager().get_sources(task_id)

	active_source_ids = {s.id for s in sources if s.is_active}
	effective = await AgentTaskManager().effective_active_map([task], active_source_ids)
	effective_active = {tid for tid, ok in effective.items() if ok}

	edit = _edit_payload(task, effective_active, source_ids_for_task)
	if not (task.payload or {}).get("task_plan") and source_ids_for_task:
		edit["platforms"] = list(
			dict.fromkeys(
				getattr(getattr(source, "platform", None), "platform_type", None).db_value
					if hasattr(getattr(getattr(source, "platform", None), "platform_type", None), "db_value")
					else str(getattr(getattr(source, "platform", None), "platform_type", "vk"))
				for source in (task.sources or [])
				if source.id in source_ids_for_task
			)
		) or ["vk"]

	# Which payload targets each scenario's analysis types want — the form
	# renders the matching inputs (see `param_registry.PAYLOAD_PARAMS`).
	scenario_analysis = {str(s.id): list(s.analysis_types or []) for s in scenarios}

	return render(
		request,
		"web/task_edit.html",
		section="tasks",
		task=task,
		edit=edit,
		sources=sources,
		source_groups=_group_sources([source for source in sources if source.owner_task_id is None]),
		task_sources=[source for source in sources if source.owner_task_id is not None],
		target_fields=TARGET_FIELD_DEFS,
		scenarios=scenarios,
		scenario_analysis=scenario_analysis,
		job_types=_web_job_type_choices(allow_retention=_can_manage_job_retention(request)),
		trigger_types=BotTriggerType.choices(),
		action_types=AgentActionType.choices(),
		can_dry_run=_can_manage_job_retention(request),
		cron_to_human=cron_to_human,
		is_superuser=is_superuser,
		filter_tenant_id=filter_tenant_id,
	)


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
	from app.models.agent_task import AgentTask

	task = await AgentTask.objects.filter(id=task_id).prefetch_related("sources").first()
	return [source.id for source in task.sources] if task else []


@router.post("/{task_id}/toggle")
async def task_toggle(
		request: Request,
		task_id: int,
		token: str = Form("", alias="_csrf"),
		tenant_id: int | None = Form(default=None),
):
	if not ensure_csrf(request, token):
		add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
		return RedirectResponse("/app/tasks", status_code=302)

	tenant_id = action_tenant_id(request, tenant_id)

	denied = guard_web(request, "agenttask", "update", back="/app/tasks")
	if denied is not None:
		return denied

	task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
	if task is None:
		add_flash(request, "error", "Задача не найдена")
		return RedirectResponse("/app/tasks", status_code=302)
	if task.job_type == "prune" and not _can_manage_job_retention(request):
		add_flash(request, "error", "Управлять настройкой хранения заданий могут только администратор и владелец пространства")
		return RedirectResponse("/app/tasks", status_code=302)

	new_active = not task.is_active
	if new_active:
		from app.models.managers.agent_task_manager import AgentTaskManager

		linked = await AgentTaskManager().get_sources(task.id)
		blocked = await _can_activate(tenant_id, task.job_type, linked, task.agent_scenario_id, payload=task.payload)
		if blocked is not None:
			add_flash(request, "error", f"Задача «{task.name}» не активирована: {blocked}")
			return RedirectResponse("/app/tasks", status_code=302)
		if task.job_type == "analyze" and isinstance(task.payload, dict) and task.payload.get("collect_before_analyze"):
			scenario_block = await _unified_analysis_scenario_block(tenant_id, task.agent_scenario_id)
			if scenario_block is not None:
				add_flash(request, "error", f"Для запуска анализа соцсетей {scenario_block}")
				return RedirectResponse("/app/tasks", status_code=302)

	await AgentTask.objects.update_by_id(task.id, is_active=new_active)
	action = "активирована" if new_active else "деактивирована"
	add_flash(request, "success", f"Задача «{task.name}» {action}")
	return RedirectResponse("/app/tasks", status_code=302)


@router.post("/{task_id}/run-now")
async def task_run_now(
		request: Request,
		task_id: int,
		token: str = Form("", alias="_csrf"),
		tenant_id: int | None = Form(default=None),
		return_to_detail: str = Form(""),
):
	back = f"/app/tasks/{task_id}" if return_to_detail == "1" else "/app/tasks"
	if not ensure_csrf(request, token):
		add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
		return RedirectResponse(back, status_code=302)

	tenant_id = action_tenant_id(request, tenant_id)

	# Enqueues a job — the same right the sqladmin run-now action declares.
	denied = guard_web(request, "agenttask", "update", back=back)
	if denied is not None:
		return denied

	task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
	if task is None:
		add_flash(request, "error", "Задача не найдена")
		return RedirectResponse("/app/tasks", status_code=302)
	if task.job_type == "prune" and not _can_manage_job_retention(request):
		add_flash(request, "error", "Запускать настройку хранения заданий могут только администратор и владелец пространства")
		return RedirectResponse("/app/tasks", status_code=302)
	if task.job_type == "analyze" and isinstance(task.payload, dict) and task.payload.get("collect_before_analyze"):
		scenario_block = await _unified_analysis_scenario_block(tenant_id, task.agent_scenario_id)
		if scenario_block is not None:
			add_flash(request, "error", f"Для запуска анализа соцсетей {scenario_block}")
			return RedirectResponse("/app/tasks", status_code=302)

	# A manual run replaces the task's unfinished work: the admission boundary
	# deletes that task's pending/running jobs before inserting the new one. A
	# job pending for *another* task on the same sources is still reported as
	# busy — replacing it would cancel somebody else's run.
	outcome = await queue_task_now(task, getattr(request.state.web_user, "id", None))
	if outcome.get("status") == "blocked":
		add_flash(request, "error", f"Задача «{task.name}» не запущена: {outcome.get('reason')}")
		return RedirectResponse(back, status_code=302)
	if outcome.get("already_running"):
		add_flash(request, "info", "Источник уже занят сбором или анализом. Новый запуск не создан")
	else:
		add_flash(request, "success", f"Задача «{task.name}» поставлена в очередь на выполнение")
	job_id = (outcome or {}).get("job_id")
	if not job_id:
		return RedirectResponse(back, status_code=302)
	return RedirectResponse(back if return_to_detail == "1" else f"/app/tasks?job_id={job_id}", status_code=302)


@router.post("/{task_id}/retry-analysis")
async def task_retry_analysis(
		request: Request,
		task_id: int,
		token: str = Form("", alias="_csrf"),
):
	"""Reset exhausted rows and queue a retry within this analysis task's scope."""
	back = f"/app/tasks/{task_id}"
	if not ensure_csrf(request, token):
		add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
		return RedirectResponse(back, status_code=302)
	denied = guard_web(request, "agenttask", "update", back=back)
	if denied is not None:
		return denied
	tenant_id = getattr(request.state, "tenant_id", None)
	if type(tenant_id) is not int:
		add_flash(request, "error", "Задача не найдена")
		return RedirectResponse("/app/tasks", status_code=302)
	task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
	if task is None or task.job_type != "analyze":
		add_flash(request, "error", "Повторный анализ доступен только для задачи анализа соцсетей")
		return RedirectResponse(back, status_code=302)

	from app.jobs.enqueue import enqueue_task_run
	from app.jobs.dispatcher import launch_job_background
	from app.jobs.source_activity import SourceJobBusy

	try:
		job = await enqueue_task_run(
			task,
			initiated_by_user_id=getattr(request.state.web_user, "id", None),
			retry_exhausted=True,
		)
	except ValueError:
		add_flash(request, "error", "Проверьте период задачи: даты должны быть корректными и идти по порядку")
		return RedirectResponse(back, status_code=302)
	except SourceJobBusy:
		add_flash(request, "info", f"Задача «{task.name}» уже выполняется или ждёт в очереди")
		return RedirectResponse(back, status_code=302)
	if job is None:
		add_flash(request, "info", "В источниках этой задачи нет сырых материалов в настроенном периоде")
		return RedirectResponse(back, status_code=302)
	launch_job_background(job.id, allow_retry=False)
	add_flash(request, "success", f"Повторный анализ по задаче «{task.name}» поставлен в очередь")
	return RedirectResponse(back, status_code=302)


@router.post("/{task_id}")
async def task_update(
		request: Request,
		task_id: int,
		name: str = Form(...),
		job_type: str = Form(...),
		cron_expr: str = Form(...),
		is_active: str = Form(""),
		source_ids: list[str] = Form([]),
		source_scope: str = Form("legacy"),
		task_goal: str = Form(""),
		platforms: list[str] = Form([]),
		window_type: str = Form("fixed"),
		rolling_days: int | None = Form(default=None),
		trigger_timezone: str = Form(""),
		scenario_id: int = Form(default=None),
		tracked_users: str = Form(""),
		keywords_match: str = Form("any"),
		brands: str = Form(""),
		competitors: str = Form(""),
		hashtags: str = Form(""),
		influencer_names: str = Form(""),
		topic_list: str = Form(""),
		keywords_list: str = Form(""),
		start_date: str = Form(""),
		end_date: str = Form(""),
		digest_period: str = Form(""),
		digest_group_by: str = Form(""),
		digest_time_breakdown: str = Form(""),
		retention_success_hours: int = Form(24),
		retention_failure_hours: int = Form(24),
		trigger_type: str = Form(""),
		trigger_keywords: str = Form(""),
		trigger_match: str = Form("any"),
		trigger_usernames: str = Form(""),
		trigger_threshold: str = Form(""),
		trigger_direction: str = Form("below"),
		trigger_baseline_hours: str = Form(""),
		trigger_spike_multiplier: str = Form(""),
		action_type: str = Form(""),
		rate_limit_per_hour: str = Form(""),
		cooldown_seconds: str = Form(""),
		dry_run: str = Form(""),
		run_now: str = Form(""),
		return_to_detail: str = Form(""),
		collection_configured: str = Form(""),
		collection_people: str = Form(""),
		collection_communities: str = Form(""),
		collection_discovery_queries: str = Form(""),
		collection_discovery_queries_match: str = Form("any"),
		collection_discovery_city: str = Form(""),
		collection_discovery_city_id: str = Form(""),
		collection_queries: str = Form(""),
		collection_queries_match: str = Form("any"),
		collection_content_types: list[str] = Form([]),
		collection_engagement_types: list[str] = Form([]),
		collection_limit: str = Form(""),
		collection_comments_max_per_post: str = Form(""),
		collection_comments_total: str = Form(""),
		collection_candidates_limit: str = Form(""),
		collection_candidates_confirm: str = Form("manual"),
		token: str = Form("", alias="_csrf"),
		tenant_id: int | None = Form(default=None),
):
	back = f"/app/tasks/{task_id}/edit" if return_to_detail == "1" else "/app/tasks"
	if not ensure_csrf(request, token):
		add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
		return RedirectResponse(back, status_code=302)
	job_type = _normalize_web_job_type(job_type)
	if job_type == "prune" and not _can_manage_job_retention(request):
		add_flash(request, "error", "Настраивать очистку истории заданий могут только администратор и владелец пространства")
		return RedirectResponse(back, status_code=302)

	tenant_id = action_tenant_id(request, tenant_id)

	denied = guard_web(request, "agenttask", "update", back=back)
	if denied is not None:
		return denied

	dry_run_flag = dry_run == "on"
	if dry_run_flag and not _can_manage_job_retention(request):
		add_flash(request, "warning", "Тестовый прогон доступен только администратору и владельцу пространства")
		dry_run_flag = False

	task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
	if task is None:
		add_flash(request, "error", "Задача не найдена")
		return RedirectResponse(back, status_code=302)
	if task.job_type == "prune" and not _can_manage_job_retention(request):
		add_flash(request, "error", "Управлять настройкой хранения заданий могут только администратор и владелец пространства")
		return RedirectResponse("/app/tasks", status_code=302)

	cron_expr = cron_expr.strip()

	# Saving under a name another task already holds would fail the unique
	# constraint; `exclude_id` keeps a task from clashing with its own row.
	clash = await _duplicate_name(request, back, name.strip()[:100], exclude_id=task_id)
	if clash is not None:
		return clash

	from app.models.managers.agent_task_manager import AgentTaskManager

	tasks_mgr = AgentTaskManager()
	if not tasks_mgr.validate_cron(cron_expr):
		add_flash(request, "error", f"Некорректное cron-выражение: {cron_expr}")
		return RedirectResponse(back, status_code=302)
	if job_type == "prune" and cron_expr == "@once":
		add_flash(request, "error", "Настройка хранения истории должна быть повторяемой, например ежедневно.")
		return RedirectResponse(back, status_code=302)

	parsed_source_ids: list[int] = []
	if AgentTaskManager.requires_sources(job_type):
		for s in source_ids:
			s = s.strip()
			if s.isdigit():
				parsed_source_ids.append(int(s))

	try:
		new_payload = _payload_from_form(
			job_type,
			start_date,
			end_date,
			digest_period=digest_period,
			digest_group_by=digest_group_by,
			digest_time_breakdown=digest_time_breakdown == "on",
			retention_success_hours=retention_success_hours,
			retention_failure_hours=retention_failure_hours,
			window_type=window_type,
		)
		if job_type != "prune":
			apply_selection(new_payload, tracked_users, _split_phrases(keywords_list), keywords_match)
	except ValueError as e:
		add_flash(request, "error", str(e))
		return RedirectResponse(back, status_code=302)

	trigger_action_fields = _trigger_and_action_fields(
		job_type,
		trigger_type,
		trigger_keywords,
		trigger_match,
		trigger_usernames,
		trigger_threshold,
		trigger_direction,
		trigger_baseline_hours,
		trigger_spike_multiplier,
		action_type,
		rate_limit_per_hour,
		cooldown_seconds,
	)

	# Merge into existing payload instead of replacing — keys like "period",
	# "days", "min_messages" set via CLI or agent tool must survive a web edit.
	payload = new_payload if job_type == "prune" else (task.payload.copy() if isinstance(task.payload, dict) else {})
	if job_type != "prune":
		payload.update(new_payload)
	if job_type in ("collect", "analyze") and window_type == "rolling":
		payload.pop("cli_dates", None)
	requested_active = is_active == "on"
	if job_type == "analyze":
		scenario_block = await _unified_analysis_scenario_block(
			tenant_id, scenario_id, allow_inactive_selected=not requested_active and not run_now
		)
		if scenario_block is not None:
			add_flash(request, "error", f"Для анализа соцсетей {scenario_block}")
			return RedirectResponse(back, status_code=302)
	if job_type == "analyze":
		payload.pop("analyze_inline", None)
	# Editing through the web form removes legacy force keys. Manual web runs
	# also override stored legacy values in the one-off job payload above.
	payload.pop("force_refresh", None)
	payload.pop("force_reanalyze", None)
	# The form's target fields are authoritative for their own keys: filled
	# sets, emptied clears (see `_apply_targets`).
	if job_type != "prune":
		_apply_targets(
			payload,
			{
				"brands": brands,
				"competitors": competitors,
				"hashtags": hashtags,
				"influencer_names": influencer_names,
				"topic_list": topic_list,
			},
		)

	# Same contract as create: the form that carries the block is authoritative,
	# a legacy submission leaves the stored block untouched.
	if collection_configured == "1":
		try:
			collection_block = _collection_block(
				job_type,
				people=collection_people,
				communities=collection_communities,
				discovery_queries=collection_discovery_queries,
				discovery_queries_match=collection_discovery_queries_match,
				discovery_city=collection_discovery_city,
				discovery_city_id=collection_discovery_city_id,
				queries=collection_queries,
				queries_match=collection_queries_match,
				content_types=collection_content_types,
				engagement_types=collection_engagement_types,
				limit=collection_limit,
				comments_max_per_post=collection_comments_max_per_post,
				comments_total=collection_comments_total,
				candidates_limit=collection_candidates_limit,
				candidates_confirm=collection_candidates_confirm,
				date_window=payload.get("cli_dates"),
			)
		except ValueError as e:
			add_flash(request, "error", str(e))
			return RedirectResponse(back, status_code=302)
		if collection_block:
			payload["collection"] = collection_block
		else:
			payload.pop("collection", None)

	task_plan = None
	if job_type in ("collect", "analyze") and source_scope != "legacy":
		try:
			task_plan = _task_plan_from_form(
				task_goal, platforms, source_scope, parsed_source_ids, scenario_id,
				job_type, cron_expr, payload, window_type, rolling_days, trigger_timezone,
			)
			if task_plan is not None:
				from app.services.task_plans import apply_task_plan

				payload = apply_task_plan(payload, task_plan, job_type=job_type)
				parsed_source_ids = list(task_plan["source_ids"])
		except ValueError as error:
			add_flash(request, "error", f"Проверьте план задачи: {error}")
			return RedirectResponse(back, status_code=302)

	if job_type == "digest":
		payload.pop("task_plan", None)

	# Warn about missing target params for the scenario's analysis types —
	# the scenario this save binds, not the one the row held before the edit.
	if scenario_id and job_type != "prune":
		sc = await AgentScenario.objects.filter(id=scenario_id).first()
		if sc and sc.analysis_types:
			from app.services.ai.param_registry import missing_target_params

			if job_type == "analyze":
				missing = missing_target_params(sc.analysis_types, payload)
			else:
				missing = []
			if missing:
				add_flash(
					request,
					"warning",
					f"⚠️ Для сценария «{sc.name}» укажите в параметрах задачи: " + ", ".join(missing),
				)

	from app.core.tenant_context import tenant_scope
	from app.models.managers.tenant_manager import tenants
	from app.tasks.cron import next_run_at, resolve_tz

	# Same rule as create: the workspace zone, so an edit does not silently
	# re-schedule the task in the global zone the runner would keep using.
	tenant = await tenants.get(id=tenant_id)
	from app.services.task_plans import task_plan_timezone
	stored_plan = (task.payload or {}).get("task_plan") if isinstance(task.payload, dict) else None
	tz = task_plan_timezone(task_plan or stored_plan, resolve_tz(tenant))

	if cron_expr == "@once":
		next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
	else:
		next_run = next_run_at(cron_expr, tz)

	try:
		await _check_task_sources(parsed_source_ids, tenant_id, task_id=task_id)
	except ValueError as e:
		add_flash(request, "error", str(e))
		return RedirectResponse(back, status_code=302)

	# Same rule as create: a ticked-but-off source activates with the save, so
	# the activation check below sees the sources as the operator intends them.
	if AgentTaskManager.requires_sources(job_type):
		await _activate_selected_sources(tenant_id, parsed_source_ids)

	# Activation is allowed only when the task has an active scenario and, for a
	# source-based type, at least one active source; otherwise it stays inactive.
	activation_blocked = None
	if requested_active:
		activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id, payload=payload)
		if activation_blocked is not None:
			add_flash(
				request,
				"warning",
				f"Задача «{name}» не активирована: {activation_blocked}",
			)

	# The row and its links are one unit: a source that vanished between the
	# check above and this write must abort the whole save, not leave the task
	# active and link-less (which the runtime reads as "all active sources").
	from app.core.database import new_session

	session = new_session()
	try:
		with tenant_scope(tenant_id):
			async with session.begin():
				await AgentTask.objects.update_by_id(
					task.id,
					name=name.strip()[:100],
					job_type=job_type,
					cron_expr=cron_expr,
					payload=payload,
					agent_scenario_id=None if job_type == "prune" else scenario_id,
					is_active=requested_active and activation_blocked is None,
					next_run_at=next_run,
					dry_run=dry_run_flag,
					**trigger_action_fields,
					session=session,
				)
				await _replace_task_sources(task.id, parsed_source_ids, tenant_id, session=session)
	except ValueError:
		add_flash(
			request,
			"error",
			"Источники задачи изменились во время сохранения — обновите страницу и повторите",
		)
		return RedirectResponse(back, status_code=302)
	finally:
		await session.close()

	# Queued, not run in-request, for the same reason as the create branch: a long
	# job froze the page and invited the second click that hit the unique name.
	if run_now:
		updated = await AgentTask.objects.get(id=task.id, tenant_id=tenant_id)
		outcome = await queue_task_now(updated, getattr(request.state.web_user, "id", None))
		if outcome.get("already_running"):
			add_flash(request, "info", f"Задача «{name}» обновлена, но источник уже занят. Новый запуск не создан")
		else:
			add_flash(request, "success", f"Задача «{name}» обновлена и поставлена в очередь на выполнение")
		job_id = (outcome or {}).get("job_id")
		if return_to_detail == "1":
			return RedirectResponse(f"/app/tasks/{task_id}", status_code=302)
		if not job_id:
			return RedirectResponse("/app/tasks", status_code=302)
		return RedirectResponse(f"/app/tasks?job_id={job_id}", status_code=302)
	else:
		add_flash(request, "success", f"Задача «{name}» обновлена")
	return RedirectResponse(f"/app/tasks/{task_id}" if return_to_detail == "1" else "/app/tasks", status_code=302)


@router.post("/{task_id}/delete")
async def task_delete(
		request: Request,
		task_id: int,
		token: str = Form("", alias="_csrf"),
		tenant_id: int | None = Form(default=None),
):
	if not ensure_csrf(request, token):
		add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
		return RedirectResponse("/app/tasks", status_code=302)

	tenant_id = action_tenant_id(request, tenant_id)

	denied = guard_web(request, "agenttask", "delete", back="/app/tasks")
	if denied is not None:
		return denied

	task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
	if task is None:
		add_flash(request, "error", "Задача не найдена")
		return RedirectResponse("/app/tasks", status_code=302)

	task_name = task.name
	await AgentTask.objects.delete(id=task_id)
	add_flash(request, "success", f"Задача «{task_name}» удалена")
	return RedirectResponse("/app/tasks", status_code=302)


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
				result.get("error"), result.get("staged_errors"),
				result.get("collection_errors"), result.get("collection_rate_limited"),
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
			"Записи этого запуска, ещё не прошедшие анализ, будут удалены; "
			"следующий сбор сможет получить их снова."
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
		steps.append({
			"source_id": item.get("source_id"),
			"name": item["name"],
			"state": state,
			"note": note,
			"error": state == "done" and error,
		})
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
					if discarded else "Записей этого запуска для удаления не нашлось."
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
				summary["heartbeat_unconfirmed"] = datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc) > timedelta(seconds=90)
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
		noun = _plural(count, "результат", "результата", "результатов") if job_type == "analyze" else _plural(count, "запись", "записи", "записей")
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
			stats.extend([
				_stat(collection.get("collected", 0), "источников собрали данные"),
				_stat(collection.get("new_items", 0), "новых записей"),
				_stat(collection.get("error", 0), "ошибок сбора"),
			])
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
		rows.append({
			"source_id": item["source_id"],
			"name": item.get("name"),
			"raw_total": item.get("raw_total"),
			"raw_processed": item.get("raw_processed"),
			"raw_selected": item.get("raw_selected"),
		})
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
		coverage.append({
			"source_id": row["source_id"], "name": name[:80],
			"total": total, "processed": processed, "percent": percent,
		})
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
			current = next((row for row in per_source if isinstance(row, dict) and row.get("source_id") == source_id), {})
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
					if type(row.get("raw_total")) is int and type(row.get("raw_processed")) is int
					and 0 <= row["raw_processed"] <= row["raw_total"] <= 10**9
				]
				current = {
					"raw_selected": sum(selected_rows) if selected_rows and all(type(v) is int and 0 <= v <= 10**9 for v in selected_rows) else None,
					"remaining": sum(remaining_rows) if len(final_rows) == len(per_source) and len(remaining_rows) == len(final_rows) else None,
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
	if remaining is None and type(total) is int and type(processed) is int and 0 <= total <= 10**9 and 0 <= processed <= total:
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
		"job_id": job_id, "attempt": attempt,
		"heartbeat_at": heartbeat_at,
		"progress_updated_at": updated_at,
		"selected": selected, "remaining": remaining,
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


@router.get("/job/{job_id}/status")
async def job_status(request: Request, job_id: int):
	"""Poll job status (run-now modal). Returns JSON; tenant-scoped."""
	from fastapi.responses import JSONResponse

	tenant_id = request.state.tenant_id
	if not (perms_can(request, "agenttask", "view") or perms_can(request, "source", "view")):
		return JSONResponse({"status": "not_found", "label": "Задача не найдена"}, status_code=404)
	job = await Job.objects.filter(id=job_id, tenant_id=tenant_id).first()
	if job is None:
		return JSONResponse({"status": "not_found", "label": "Задача не найдена"})
	task_name = None
	task = None
	if job.agent_task_id is not None:
		task = await AgentTask.objects.get(id=job.agent_task_id, tenant_id=tenant_id)
		task_name = task.name if task else None
		if task is not None and job.job_type == "collect":
			payload = task.payload if isinstance(task.payload, dict) else {}
			job._analysis_inline = bool(payload.get("analyze_inline", False))
	summary = _job_summary(job, task_name)
	summary.pop("hint", None)
	if summary.get("status") == "failed":
		summary["error"] = "Задача завершилась с ошибкой. Повторите запуск позже."
	# Normal JSON never carries diagnostics: the allowlisted detail is added
	# only for a workspace admin role or a platform admin.
	if _diagnostics_admin(request):
		summary["diagnostics"] = _diagnostics_view(job)
	else:
		summary.pop("stats", None)
	# Mini-links to the sources this run touched; they need the DB, so they are
	# resolved here rather than in the pure `_job_summary`.
	can_view_sources = perms_can(request, "source", "view")
	summary["sources"] = _source_links(job) if can_view_sources else []
	if not can_view_sources:
		summary.pop("raw_coverage", None)
		summary.pop("progress", None)
		if isinstance(summary.get("diagnostics"), dict):
			summary["diagnostics"].pop("selected", None)
			summary["diagnostics"].pop("remaining", None)
	if summary.get("status") in ("pending", "running"):
		summary["can_cancel"] = bool(perms_can(request, "agenttask", "update"))
	from .deps import templates

	view = project_run(job, task, authorized=can_view_sources)
	if view:
		view["state"] = "unconfirmed" if summary.get("heartbeat_unconfirmed") else summary["status"]
		summary["run_progress_html"] = str(templates.get_template("web/_run_progress.html").module.record_progress(view, compact=True))
		summary["task_url"] = f"/app/tasks/{task.id}" if task is not None else None
	return JSONResponse(summary)


@router.post("/job/{job_id}/cancel")
async def job_cancel_from_modal(
		request: Request,
		job_id: int,
		token: str = Form("", alias="_csrf"),
		content: str = Form(""),
):
	"""Cancel the run shown in the run-status modal (JSON; tenant-scoped).

	`pending` (never claimed) is stopped before start; `running` is cancelled
	only for the generation observed here, like the queue page. For a collect
	run `content` chooses whether the rows it already staged stay (`keep`) or
	go (`discard`). Cancellation fences the Job write; it does not recall an
	external effect that already happened.
	"""
	from fastapi.responses import JSONResponse

	def reply(ok: bool, message: str, status_code: int = 200, **extra: Any) -> JSONResponse:
		return JSONResponse({"ok": ok, "message": message, **extra}, status_code=status_code)

	if not ensure_csrf(request, token):
		return reply(False, "Сессия истекла — обновите страницу и попробуйте ещё раз.", 403)
	if not perms_can(request, "agenttask", "update"):
		return reply(False, "Недостаточно прав, чтобы отменить задачу.", 403)
	if content not in ("", "keep", "discard"):
		return reply(False, "Неизвестный вариант для собранных записей.", 400)
	tenant_id = getattr(request.state, "tenant_id", None)
	if type(tenant_id) is not int:
		return reply(False, "Задача не найдена.", 404)

	from app.core.tenant_context import tenant_scope
	from app.jobs.claim_outcomes import JobClaim

	with tenant_scope(tenant_id):
		# Two passes: a pending row may be claimed between the read and the write.
		for _ in range(2):
			job = await Job.objects.filter(id=job_id, tenant_id=tenant_id).first()
			if job is None:
				return reply(False, "Задача не найдена.", 404)
			if job.status == "pending":
				if _as_int(getattr(job, "attempts", 0)):
					return reply(False, "Задача ждёт повторной попытки — отменить её отсюда нельзя.", 409)
				if await Job.objects.cancel_pending(job.id):
					return reply(True, "Запуск отменён до начала.")
				continue
			if job.status == "running":
				try:
					observed = JobClaim.capture(job)
				except ValueError:
					return reply(False, "Отмена не подтверждена: данные запуска неполны.", 409)
				discard = (content == "discard") if job.job_type == "collect" else None
				if await Job.objects.cancel_running(observed, discard_collected=discard):
					return reply(True, "Задача остановлена.")
				continue
			return reply(False, "Задача уже завершилась — отменять нечего.", 409, status=job.status)
	return reply(False, "Состояние задачи изменилось — проверьте результат.", 409)
