"""Tasks `/app/tasks` — create and manage scheduled agent tasks.

Flow:
- sources are linked via the m2m `agent_task_sources` table (not payload)
- scenario is set via `agent_scenario_id` FK (not payload)
- payload keeps flat keys: period, monitored_users, excluded_users, ...
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.models import AgentScenario, AgentTask, Job, Source
from app.models.managers.agent_task_manager import AgentTaskManager
from app.tasks.cron import cron_to_human
from app.jobs.recovery_policy import outcome_unconfirmed
from app.types import AgentActionType, BotTriggerType, JobType

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


def _split_names(raw: str) -> list[str]:
	"""Split a comma/space separated username string into a clean list."""
	return [n.strip().lstrip("@") for n in raw.replace(",", " ").split() if n.strip()]


# The analysis targets a task payload carries (param_registry.PAYLOAD_PARAMS):
# the scenario's analysis types say *what kind* of target matters, the task
# says *which* brands/hashtags/… to aim at.
TARGET_KEYS = ("brands", "competitors", "hashtags", "influencer_names", "keywords_list", "topic_list")


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
		job_type, monitored_users, excluded_users, start_date, end_date, force_refresh, force_reanalyze,
		digest_period=None, digest_group_by=None, digest_time_breakdown=False,
) -> dict:
	"""The flat payload keys a task of this type needs.

	For collect/analyze a `cli_dates.start_date` is mandatory: without it a
	fresh source has no lower bound and the first run drains the whole history
	from the first post. `force_refresh` (collect: overwrite the window — and,
	per docs/CLI.md, re-run the analysis over it) and `force_reanalyze`
	(analyze: re-run the model on stored rows) are the operator's own choices.

	For digest: `period` (day/week/month), `group_by` axis, and
	`time_breakdown` flag are stored in payload so the scheduled run uses
	the operator's preference.
	"""
	payload: dict = {}
	if monitored_users:
		payload["monitored_users"] = _split_names(monitored_users)
	if excluded_users:
		payload["excluded_users"] = _split_names(excluded_users)
	if AgentTaskManager.requires_content_dates(job_type):
		start = AgentTaskManager.parse_date(start_date)
		if start is None:
			raise ValueError("Укажите дату начала сбора контента — иначе первый запуск вытянет всё с первого поста")
		end = AgentTaskManager.parse_date(end_date)
		payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=bool(force_refresh)))
		# One flag per type, written explicitly so a value left behind by a
		# job-type switch never survives the save. `force_refresh` on collect is
		# the documented full cycle (docs/CLI.md: re-fetch the period *and* re-run
		# the analysis over it); `analyze` keeps its own `force_reanalyze`.
		if job_type == "collect":
			payload["force_reanalyze"] = bool(force_refresh)
		else:
			payload["force_reanalyze"] = bool(force_reanalyze)
	if job_type == "digest":
		if digest_period and digest_period in ("day", "week", "month"):
			payload["period"] = digest_period
		if digest_group_by:
			payload["group_by"] = digest_group_by
		if digest_time_breakdown:
			payload["time_breakdown"] = True
	return payload


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
		blacklist: str,
		whitelist: str,
		rate_limit_per_hour: str,
		cooldown_seconds: str,
) -> dict:
	"""The model columns (not payload) an analyze task carries.

	No-op for every other job type — the fields are only rendered for
	`analyze`. Explicitly (re)setting blacklist/whitelist/guards lets the
	operator clear them: an update that omits the key keeps the old value,
	which is what the "merge into payload" rule is *not* meant to apply to.
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
	fields["blacklist"] = _split_names(blacklist) or None
	fields["whitelist"] = _split_names(whitelist) or None
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
	cli_dates = payload.get("cli_dates") or {}
	trigger_config = task.trigger_config if isinstance(task.trigger_config, dict) else {}
	edit: dict[str, Any] = {
		"id": task.id,
		"name": task.name,
		"job_type": task.job_type,
		"cron_expr": task.cron_expr,
		"is_active": task.id in effective_active,
		"source_ids": source_ids,
		"scenario_id": task.agent_scenario_id,
		"monitored_users": ", ".join(payload.get("monitored_users") or []),
		"excluded_users": ", ".join(payload.get("excluded_users") or []),
		"start_date": cli_dates.get("start_date") or "",
		"end_date": cli_dates.get("end_date") or "",
		"force_refresh": bool(payload.get("force_refresh")),
		"force_reanalyze": bool(payload.get("force_reanalyze")),
		"digest_period": payload.get("period") or "day",
		"digest_group_by": payload.get("group_by") or "themes",
		"digest_time_breakdown": bool(payload.get("time_breakdown")),
		"trigger_type": getattr(task, "trigger_type", None).name if getattr(task, "trigger_type", None) else "",
		"trigger_keywords": ", ".join(trigger_config.get("keywords") or []),
		"trigger_match": trigger_config.get("match", "any"),
		"trigger_usernames": ", ".join(trigger_config.get("usernames") or []),
		"trigger_threshold": trigger_config.get("threshold", 0.5),
		"trigger_direction": trigger_config.get("direction", "below"),
		"trigger_baseline_hours": trigger_config.get("baseline_period_hours", 24),
		"trigger_spike_multiplier": trigger_config.get("spike_multiplier", 3.0),
		"action_type": getattr(task, "action_type", None).name if getattr(task, "action_type", None) else "",
		"blacklist": ", ".join(task.blacklist or []),
		"whitelist": ", ".join(task.whitelist or []),
		"rate_limit_per_hour": task.rate_limit_per_hour or "",
		"cooldown_seconds": task.cooldown_seconds or "",
	}
	# The analysis targets live in payload under their own keys (brands, …) and
	# are edited as comma separated strings, like monitored_users above.
	for key in TARGET_KEYS:
		edit[key] = ", ".join(payload.get(key) or [])
	return edit


async def _check_task_sources(source_ids: list[int], tenant_id: int) -> None:
	"""Reject source ids that are missing or belong to another workspace.

	The m2m link table has no tenant column, so a raw POST could otherwise
	attach somebody else's source to this task and make the job read it.
	"""
	from app.models import Source

	if not source_ids:
		return
	sources = {s.id: s for s in await Source.objects.filter(id__in=source_ids)}
	foreign = sorted(sid for sid, s in sources.items() if s.tenant_id != tenant_id)
	if foreign:
		raise ValueError(f"Источник(и) {', '.join(map(str, foreign))} принадлежат другому воркспейсу")


async def _replace_task_sources(task_id: int, source_ids: list[int], tenant_id: int) -> None:
	"""Replace the task's m2m source links with the given set."""
	from app.models.managers.agent_task_manager import AgentTaskManager

	await _check_task_sources(source_ids, tenant_id)
	await AgentTaskManager().set_sources(task_id, source_ids)


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


async def _can_activate(tenant_id: int, job_type: str, source_ids: list[int], scenario_id: int | None) -> str | None:
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


async def queue_task_now(task: "AgentTask") -> dict[str, Any]:
	"""Queue the task's job for the worker instead of running it in this request.

	"Сохранить и выполнить" used to call `run_task_now`, which executes the
	handler synchronously and only then returns. For a long job that is minutes
	of an open spinner with no way to tell a slow run from a hung one — an
	`analyze` task draining a backlog took over ten minutes, and the operator's
	natural reaction was to click again, which collided with the row the first
	click had already written.

	Queueing returns as soon as the job row exists, so the page reloads at once
	and the run-status modal (which already polls `/app/tasks/job/<id>/status`)
	follows the job to completion. The job is a normal `pending` row here, so
	the worker picks it up — that is the difference from `run_task_now`, which
	deliberately stamps its row `running` to keep the worker away.

	Returns the same `{job_id, status}` shape so both callers read alike.
	"""
	from app.jobs.enqueue import enqueue_task_run

	job = await enqueue_task_run(task)
	return {"job_id": job.id, "status": job.status}


@router.get("")
@router.get("/")
async def tasks_list(request: Request):
	tenant_id = request.state.tenant_id
	user = getattr(request.state, "web_user", None)
	is_superuser = bool(user and user.is_superuser)
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

	sources = await Source.objects.filter(tenant_id=tenant_id).order_by(Source.name)

	scenarios = await AgentScenario.objects.filter(tenant_id=tenant_id, is_active=True).order_by(AgentScenario.name)
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
		scenarios=scenarios,
		scenario_analysis=scenario_analysis,
		effective_active=effective_active,
		unconfirmed_tasks={task.id for task in tasks if outcome_unconfirmed(task.last_error)},
		running_jobs=running_jobs,
		job_types=JobType.choices(),
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
		scenario_id: int = Form(default=None),
		monitored_users: str = Form(""),
		excluded_users: str = Form(""),
		brands: str = Form(""),
		competitors: str = Form(""),
		hashtags: str = Form(""),
		influencer_names: str = Form(""),
		keywords_list: str = Form(""),
		topic_list: str = Form(""),
		start_date: str = Form(""),
		end_date: str = Form(""),
		force_refresh: str = Form(""),
		force_reanalyze: str = Form(""),
		digest_period: str = Form(""),
		digest_group_by: str = Form(""),
		digest_time_breakdown: str = Form(""),
		trigger_type: str = Form(""),
		trigger_keywords: str = Form(""),
		trigger_match: str = Form("any"),
		trigger_usernames: str = Form(""),
		trigger_threshold: str = Form(""),
		trigger_direction: str = Form("below"),
		trigger_baseline_hours: str = Form(""),
		trigger_spike_multiplier: str = Form(""),
		action_type: str = Form(""),
		blacklist: str = Form(""),
		whitelist: str = Form(""),
		rate_limit_per_hour: str = Form(""),
		cooldown_seconds: str = Form(""),
		run_now: str = Form(""),
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

	tenant_id = action_tenant_id(request, tenant_id)

	denied = guard_web(request, "agenttask", "create", back=back)
	if denied is not None:
		return denied

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

	parsed_source_ids: list[int] = []
	for s in source_ids:
		s = s.strip()
		if s.isdigit():
			parsed_source_ids.append(int(s))

	try:
		payload = _payload_from_form(
			job_type,
			monitored_users,
			excluded_users,
			start_date,
			end_date,
			force_refresh,
			force_reanalyze,
			digest_period=digest_period,
			digest_group_by=digest_group_by,
			digest_time_breakdown=digest_time_breakdown == "on",
		)
	except ValueError as e:
		add_flash(request, "error", str(e))
		return RedirectResponse(back, status_code=302)

	_apply_targets(
		payload,
		{
			"brands": brands,
			"competitors": competitors,
			"hashtags": hashtags,
			"influencer_names": influencer_names,
			"keywords_list": keywords_list,
			"topic_list": topic_list,
		},
	)

	# Warn about missing target params for the scenario's analysis types.
	# Non-blocking: the task is still created, but analysis will have nothing
	# specific to look for without these.
	if scenario_id:
		sc = await AgentScenario.objects.filter(id=scenario_id).first()
		if sc and sc.analysis_types:
			from app.services.ai.param_registry import missing_target_params

			missing = missing_target_params(sc.analysis_types, payload)
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
		blacklist,
		whitelist,
		rate_limit_per_hour,
		cooldown_seconds,
	)

	from app.core.tenant_context import tenant_scope
	from app.models.managers.tenant_manager import tenants
	from app.tasks.cron import next_run_at, resolve_tz

	# The workspace zone wins; the global setting is only the fallback. Must
	# agree with the runner, which re-advances the schedule in the same zone.
	tenant = await tenants.get(id=tenant_id)
	tz = resolve_tz(tenant)

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
	activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id)
	if activation_blocked is not None:
		add_flash(
			request,
			"warning",
			f"Задача «{name}» создана неактивной: {activation_blocked}",
		)
	is_active = activation_blocked is None

	with tenant_scope(tenant_id):
		task = await AgentTask.objects.create(
			name=name.strip()[:100],
			job_type=job_type,
			cron_expr=cron_expr,
			payload=payload,
			agent_scenario_id=scenario_id,
			is_active=is_active,
			next_run_at=next_run,
			**trigger_action_fields,
		)
		if parsed_source_ids:
			await _replace_task_sources(task.id, parsed_source_ids, tenant_id)

	# "Создать и выполнить" only queues the job. It used to run the handler
	# inside this request, which for a long job (an `analyze` task draining a
	# backlog) meant minutes of a frozen page — and a second click, which then
	# collided with the row the first click had written. The run-status modal
	# follows the queued job instead.
	if run_now:
		outcome = await queue_task_now(task)
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
	is_superuser = bool(user and user.is_superuser)
	filter_tenant_id, _tenants = await tenant_filter_context(request, is_superuser)

	source = None
	with tenant_scope(bypass=True) if is_superuser else nullcontext():
		source = await AgentTask.objects.filter(id=task_id).select_related("tenant").first()
		if source is not None and not is_superuser and source.tenant_id != request.state.tenant_id:
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
		recent_jobs = await Job.objects.filter(agent_task_id=task_id).order_by(Job.created_at.desc()).limit(10)

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
				status__in=["pending", "running"],
			).first()
			if running_job is not None:
				running_job_id = running_job.id
				running_job_status = running_job.status

		# Linked sources.
		linked_sources = await Source.objects.filter(id__in=await _task_source_ids(task_id)).order_by(Source.name)

		# Scenario.
		scenario = None
		if source.agent_scenario_id is not None:
			scenario = await AgentScenario.objects.get(id=source.agent_scenario_id, tenant_id=source.tenant_id)

	# The same outcome classification the run-now modal uses, so a run reads
	# the same on this page and in the modal: a half-broken collect is not a
	# green "готово" in one place and "частично" in the other.
	job_outcomes = {
		job.id: _run_outcome(job.job_type, job.result or {}) for job in recent_jobs if job.status == "done"
	}

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
		sources=linked_sources,
		scenario=scenario,
		cron_to_human=cron_to_human,
		JOB_TYPE_TITLES=JOB_TYPE_TITLES,
		perms_can=perms_can,
	)


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
	is_superuser = bool(user and user.is_superuser)
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
		sources = await Source.objects.filter(tenant_id=task.tenant_id).order_by(Source.name)
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
		scenarios=scenarios,
		scenario_analysis=scenario_analysis,
		job_types=JobType.choices(),
		trigger_types=BotTriggerType.choices(),
		action_types=AgentActionType.choices(),
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

	new_active = not task.is_active
	if new_active:
		from app.models.managers.agent_task_manager import AgentTaskManager

		linked = await AgentTaskManager().get_sources(task.id)
		blocked = await _can_activate(tenant_id, task.job_type, linked, task.agent_scenario_id)
		if blocked is not None:
			add_flash(request, "error", f"Задача «{task.name}» не активирована: {blocked}")
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
):
	if not ensure_csrf(request, token):
		add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
		return RedirectResponse("/app/tasks", status_code=302)

	tenant_id = action_tenant_id(request, tenant_id)

	# Enqueues a job — the same right the sqladmin run-now action declares.
	denied = guard_web(request, "agenttask", "update", back="/app/tasks")
	if denied is not None:
		return denied

	task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
	if task is None:
		add_flash(request, "error", "Задача не найдена")
		return RedirectResponse("/app/tasks", status_code=302)

	# Prevent duplicate runs: if a job is already pending or running for this
	# task, redirect to it so the user can watch the same modal instead of
	# creating a second job that would duplicate the work.
	from app.models.job import Job

	existing = await Job.objects.filter(
		agent_task_id=task_id,
		status__in=["pending", "running"],
	).first()
	if existing is not None:
		add_flash(
			request,
			"info",
			f"Задача «{task.name}» уже выполняется (job #{existing.id})",
		)
		return RedirectResponse(f"/app/tasks?job_id={existing.id}", status_code=302)

	outcome = await queue_task_now(task)
	add_flash(request, "success", f"Задача «{task.name}» поставлена в очередь на выполнение")
	job_id = (outcome or {}).get("job_id")
	if not job_id:
		return RedirectResponse("/app/tasks", status_code=302)
	return RedirectResponse(f"/app/tasks?job_id={job_id}", status_code=302)


@router.post("/{task_id}")
async def task_update(
		request: Request,
		task_id: int,
		name: str = Form(...),
		job_type: str = Form(...),
		cron_expr: str = Form(...),
		is_active: str = Form(""),
		source_ids: list[str] = Form([]),
		scenario_id: int = Form(default=None),
		monitored_users: str = Form(""),
		excluded_users: str = Form(""),
		brands: str = Form(""),
		competitors: str = Form(""),
		hashtags: str = Form(""),
		influencer_names: str = Form(""),
		keywords_list: str = Form(""),
		topic_list: str = Form(""),
		start_date: str = Form(""),
		end_date: str = Form(""),
		force_refresh: str = Form(""),
		force_reanalyze: str = Form(""),
		digest_period: str = Form(""),
		digest_group_by: str = Form(""),
		digest_time_breakdown: str = Form(""),
		trigger_type: str = Form(""),
		trigger_keywords: str = Form(""),
		trigger_match: str = Form("any"),
		trigger_usernames: str = Form(""),
		trigger_threshold: str = Form(""),
		trigger_direction: str = Form("below"),
		trigger_baseline_hours: str = Form(""),
		trigger_spike_multiplier: str = Form(""),
		action_type: str = Form(""),
		blacklist: str = Form(""),
		whitelist: str = Form(""),
		rate_limit_per_hour: str = Form(""),
		cooldown_seconds: str = Form(""),
		run_now: str = Form(""),
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

	cron_expr = cron_expr.strip()

	# Saving under a name another task already holds would fail the unique
	# constraint; `exclude_id` keeps a task from clashing with its own row.
	clash = await _duplicate_name(request, "/app/tasks", name.strip()[:100], exclude_id=task_id)
	if clash is not None:
		return clash

	from app.models.managers.agent_task_manager import AgentTaskManager

	tasks_mgr = AgentTaskManager()
	if not tasks_mgr.validate_cron(cron_expr):
		add_flash(request, "error", f"Некорректное cron-выражение: {cron_expr}")
		return RedirectResponse("/app/tasks", status_code=302)

	parsed_source_ids: list[int] = []
	for s in source_ids:
		s = s.strip()
		if s.isdigit():
			parsed_source_ids.append(int(s))

	try:
		new_payload = _payload_from_form(
			job_type,
			monitored_users,
			excluded_users,
			start_date,
			end_date,
			force_refresh,
			force_reanalyze,
			digest_period=digest_period,
			digest_group_by=digest_group_by,
			digest_time_breakdown=digest_time_breakdown == "on",
		)
	except ValueError as e:
		add_flash(request, "error", str(e))
		return RedirectResponse("/app/tasks", status_code=302)

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
		blacklist,
		whitelist,
		rate_limit_per_hour,
		cooldown_seconds,
	)

	# Merge into existing payload instead of replacing — keys like "period",
	# "days", "min_messages" set via CLI or agent tool must survive a web edit.
	payload = task.payload.copy() if isinstance(task.payload, dict) else {}
	payload.update(new_payload)
	# The form's target fields are authoritative for their own keys: filled
	# sets, emptied clears (see `_apply_targets`).
	_apply_targets(
		payload,
		{
			"brands": brands,
			"competitors": competitors,
			"hashtags": hashtags,
			"influencer_names": influencer_names,
			"keywords_list": keywords_list,
			"topic_list": topic_list,
		},
	)

	# Warn about missing target params for the scenario's analysis types —
	# the scenario this save binds, not the one the row held before the edit.
	if scenario_id:
		sc = await AgentScenario.objects.filter(id=scenario_id).first()
		if sc and sc.analysis_types:
			from app.services.ai.param_registry import missing_target_params

			missing = missing_target_params(sc.analysis_types, payload)
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
	tz = resolve_tz(tenant)

	if cron_expr == "@once":
		next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
	else:
		next_run = next_run_at(cron_expr, tz)

	try:
		await _check_task_sources(parsed_source_ids, tenant_id)
	except ValueError as e:
		add_flash(request, "error", str(e))
		return RedirectResponse("/app/tasks", status_code=302)

	# Same rule as create: a ticked-but-off source activates with the save, so
	# the activation check below sees the sources as the operator intends them.
	if AgentTaskManager.requires_sources(job_type):
		await _activate_selected_sources(tenant_id, parsed_source_ids)

	# Activation is allowed only when the task has an active scenario and, for a
	# source-based type, at least one active source; otherwise it stays inactive.
	requested_active = is_active == "on"
	activation_blocked = None
	if requested_active:
		activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id)
		if activation_blocked is not None:
			add_flash(
				request,
				"warning",
				f"Задача «{name}» не активирована: {activation_blocked}",
			)

	with tenant_scope(tenant_id):
		await AgentTask.objects.update_by_id(
			task.id,
			name=name.strip()[:100],
			job_type=job_type,
			cron_expr=cron_expr,
			payload=payload,
			agent_scenario_id=scenario_id,
			is_active=requested_active and activation_blocked is None,
			next_run_at=next_run,
			**trigger_action_fields,
		)
		await _replace_task_sources(task.id, parsed_source_ids, tenant_id)

	# Queued, not run in-request, for the same reason as the create branch: a long
	# job froze the page and invited the second click that hit the unique name.
	if run_now:
		updated = await AgentTask.objects.get(id=task.id, tenant_id=tenant_id)
		outcome = await queue_task_now(updated)
		add_flash(request, "success", f"Задача «{name}» обновлена и поставлена в очередь на выполнение")
		job_id = (outcome or {}).get("job_id")
		if not job_id:
			return RedirectResponse("/app/tasks", status_code=302)
		return RedirectResponse(f"/app/tasks?job_id={job_id}", status_code=302)
	else:
		add_flash(request, "success", f"Задача «{name}» обновлена")
	return RedirectResponse("/app/tasks", status_code=302)


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
	"analyze": "Анализ данных",
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
	"analyze": ("Проанализировано", "analyzed"),
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
		# Jobs recorded before `new_items` existed have no such key; fall back
		# to the total rather than calling a legacy run "no data".
		found = result.get("new_items", result.get("items", 0))
		return "no_data" if not _as_int(found) else "ok"
	if job_type == "analyze":
		# Only recorded nonnegative integer counters are known; never coerce
		# legacy/malformed values or add overlapping source/staged failures.
		if any(type(count) is int and count > 0 for count in (result.get("error"), result.get("staged_errors"))):
			return "partial"
		if _as_int(result.get("analyzed")):
			return "ok"
		# Skipped sources are the ones without an active scenario — a different
		# fact from "there was nothing to analyse", and a different fix.
		return "skipped" if _as_int(result.get("skipped")) else "no_data"
	if job_type == "prune":
		return "no_data" if not _as_int(result.get("deleted")) else "ok"
	return "ok"


_OUTCOME_LABELS = {
	"ok": "Готово",
	"no_data": "Новых данных нет",
	"partial": "Выполнено частично",
	"skipped": "Пропущено",
}

_OUTCOME_NOTES = {
	"collect": {
		"no_data": "Все источники ответили, но новых записей не нашлось — всё уже собрано ранее.",
		"partial": "Часть источников не ответила или требует авторизации — подробности ниже.",
	},
	"analyze": {
		"no_data": "Нечего анализировать — новых данных нет.",
		"skipped": "Источники пропущены: вероятно, нет активного сценария.",
	},
	"prune": {
		"no_data": "Удалять нечего — старых записей нет.",
	},
}


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
		}
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
	if status == "failed":
		return {
			"status": "failed",
			"label": "Ошибка",
			"title": JOB_TYPE_TITLES.get(job_type, job_type),
			"error": (job.error or "Неизвестная ошибка")[:300],
		}

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
		headline = {"value": count, "label": word, "noun": _plural(count, "запись", "записи", "записей")}

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
	elif job_type == "analyze":
		stats = [
			_stat(result.get("sources", 0), "источников проверено"),
			_stat(result.get("actions_created", 0), "действий создано"),
			_stat(result.get("skipped", 0), "пропущено"),
		]
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
	if note:
		summary["outcome_note"] = note
	if headline is not None:
		summary["headline"] = headline
	if task_name:
		summary["task_name"] = task_name
	return summary


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
		outcome = entry.get("outcome") or "empty"
		if outcome == "error":
			note = "ошибка"
		elif outcome == "collected":
			items = int(entry.get("items") or 0)
			# Prefer the new count: "32 records" for a source that re-serves the
			# same 32 every hour reads as 32 new records, which is not what
			# happened. Older jobs have no counter — fall back to the total.
			if entry.get("new_items") is not None:
				new_items = int(entry["new_items"])
				if new_items:
					note = f"{new_items} {_plural(new_items, 'новая', 'новых', 'новых')}"
				else:
					note = "новых нет"
			else:
				note = f"{items} {_plural(items, 'запись', 'записи', 'записей')}"
		else:
			note = "без новых данных"
		link = {
			"source_id": source_id,
			"name": entry.get("name") or f"Источник {source_id}",
			"note": note,
			"error": outcome == "error",
		}
		# `analyze` records what the agent looked at rather than rows collected,
		# so the link carries that count instead of the collection wording.
		analyzed = entry.get("analyzed")
		if outcome != "error" and analyzed:
			count = int(analyzed)
			link["note"] = f"{count} {_plural(count, 'анализ', 'анализа', 'анализов')}"
		links.append(link)
	return links


@router.get("/job/{job_id}/status")
async def job_status(request: Request, job_id: int):
	"""Poll job status (run-now modal). Returns JSON; tenant-scoped."""
	from fastapi.responses import JSONResponse

	tenant_id = request.state.tenant_id
	job = await Job.objects.filter(id=job_id, tenant_id=tenant_id).first()
	if job is None:
		return JSONResponse({"status": "not_found", "label": "Задача не найдена"})
	task_name = None
	task = None
	if job.agent_task_id is not None:
		task = await AgentTask.objects.get(id=job.agent_task_id, tenant_id=tenant_id)
		task_name = task.name if task else None
	summary = _job_summary(job, task_name)
	# Mini-links to the sources this run touched; they need the DB, so they are
	# resolved here rather than in the pure `_job_summary`.
	summary["sources"] = _source_links(job)
	return JSONResponse(summary)
