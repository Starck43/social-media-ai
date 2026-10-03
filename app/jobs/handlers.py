"""Background job handlers. Heavy work runs here (worker process)."""

import logging
from typing import Any

from app.services.social.credentials import AuthorizationRequired

logger = logging.getLogger(__name__)


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
        AgentTask.objects.filter(id=task_id)
        .prefetch_related("sources", "sources.platform", "agent_scenario")
        .first()
    )
    if task is not None:
        return task
    with tenant_scope(bypass=True):
        other = await AgentTask.objects.get(id=task_id)
    if other is not None:
        raise ValueError(f"Task {task_id} belongs to workspace {other.tenant_id}, not to the job's workspace")
    logger.warning(f"Job points at task {task_id}, which no longer exists; falling back to payload sources")
    return None


async def _resolve_sources(task, payload: dict[str, Any] | None = None) -> list:
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
        logger.info(f"_resolve_sources: task={task.id} has {len(task_sources)} sources: {[s.id for s in task_sources]}")
        if task_sources:
            return task_sources
        logger.info("_resolve_sources: task has no sources, falling back to all active")
        return list(await Source.objects.filter(is_active=True).select_related("platform"))

    source_ids = (payload or {}).get("source_ids") or []
    if source_ids:
        return list(await Source.objects.filter(id__in=source_ids).select_related("platform"))
    return list(await Source.objects.filter(is_active=True).select_related("platform"))


def _task_payload(task) -> dict[str, Any]:
    """Payload dict of the task (or empty if there is no task)."""
    return dict(task.payload or {}) if task is not None else {}


# How many staged raw items one analyze run takes per source. A collect run
# stages everything the platform returned, so this is the cap that keeps a
# single pass bounded and lets the remainder roll into the next run.
ANALYZE_STAGE_BATCH = 100


async def _retire_staged(analytics: Any, source_id: int) -> int:
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

    hashes = analysed_hashes(analytics)
    if not hashes:
        return 0
    session = new_session()
    try:
        async with session.begin():
            return await CollectedItem.objects.delete_hashes(session, source_id, hashes)
    except Exception as e:  # noqa: BLE001 — leftovers are swept by the retention pass
        logger.warning(f"Could not retire staged rows of source {source_id}: {e}")
        return 0
    finally:
        await session.close()


async def handle_collect(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Collect content from sources.

    Sources come from the task's m2m `sources` (task.sources); empty = all active.
    Per-task overrides live in the task payload:
        monitored_users: list[str] — collect these users instead of source defaults
        excluded_users:  list[str] — skip these users
    """
    from app.services.monitoring.collector import ContentCollector

    task = await _load_task(payload.get("agent_task_id"))
    task_payload = _task_payload(task)
    monitored_users = task_payload.get("monitored_users") or []
    excluded_users = task_payload.get("excluded_users") or []
    # Optional per-run overrides merged into each source's params (the collector
    # reads force_refresh/cli_dates/incremental_mode from source.params).
    force_refresh = task_payload.get("force_refresh") or payload.get("force_refresh")
    cli_dates = task_payload.get("cli_dates") or payload.get("cli_dates")
    # Full-cycle refresh: re-analyze the whole selected period, overwriting rows
    # by (source, date). Only ever set by an explicit `--force-refresh` task run.
    force_reanalyze = task_payload.get("force_reanalyze") or payload.get("force_reanalyze")
    # The job's own id, used to tie staged raw content back to this run so it
    # can be retired once an analysis has consumed it.
    run_id = payload.get("job_id")
    # Whether this task wants its freshly collected content analysed right away.
    # Default False: a `collect` task parks the raw batch in `collected_items`
    # for a separate `analyze` task to drain (the workspace ships collect and
    # analyze as two tasks). Set `analyze_inline: true` to collect and analyse
    # in one pass — that case stages first and retires the rows the analysis
    # actually stored.
    analyze_inline = task_payload.get("analyze_inline")
    if analyze_inline is None:
        analyze_inline = payload.get("analyze_inline", False)

    sources = await _resolve_sources(task, payload)

    collector = ContentCollector()

    def _is_excluded(source) -> bool:
        external = (source.external_id or "").lstrip("@")
        return external in excluded_users

    def _apply_params(source) -> None:
        params = dict(source.params or {})
        if force_refresh is not None:
            params["force_refresh"] = bool(force_refresh)
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
        # Items the platforms actually handed over, and how many of those were
        # never seen before. Platforms do not remember what we fetched, so
        # `items` repeats itself on every re-run — `new_items` is the number that
        # answers "did this run find anything".
        "new_items": 0,
        # Per-source outcome, keyed by id so the UI can show it on that source's
        # own page. The aggregate `items` above is a single number for the whole
        # run, which is what left "what did *this* source yield?" unanswerable.
        "per_source": [],
    }
    for source in sources:
        if _is_excluded(source):
            stats["excluded"] += 1
            stats["excluded_sources"].append(source.name)
            continue
        stats["sources"] += 1
        _apply_params(source)
        try:
            if monitored_users:
                result = await collector.collect_monitored_users(
                    source,
                    # Same `analyze_inline` decision and same run id as the
                    # single-source branch below. This one hardcoded
                    # `analyze=True` and dropped `run_id`, so its raw copy could
                    # be neither written nor retired later.
                    analyze=bool(analyze_inline),
                    monitored_users=monitored_users,
                    force_reanalyze=force_reanalyze,
                    run_id=run_id,
                )
                items = (result or {}).get("total_items", 0)
                new_items = (result or {}).get("total_new_items", 0)
                if result and result.get("total_items", 0) > 0:
                    stats["collected"] += 1
                    stats["items"] += result["total_items"]
                    stats["new_items"] += result.get("total_new_items", 0)
                    stats["collected_sources"].append(source.name)
                    outcome = "collected"
                else:
                    stats["empty"] += 1
                    stats["empty_sources"].append(source.name)
                    outcome = "empty"
            else:
                result = await collector.collect_from_source(
                    source,
                    analyze=bool(analyze_inline),
                    force_reanalyze=force_reanalyze,
                    run_id=run_id,
                )
                items = (result or {}).get("content_count", 0)
                new_items = (result or {}).get("new_items", 0)
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
            # `analytics_count` is what the collect run *stored*, not what a later
            # `analyze` task will do — the loop already analyses inline.
            stats["per_source"].append(
                {
                    "source_id": source.id,
                    "name": source.name,
                    "items": int(items or 0),
                    "new_items": int(new_items or 0),
                    "outcome": outcome,
                    "analyzed": int((result or {}).get("analytics_count", 0) or 0),
                    # Raw rows parked for a later analysis (0 when analysed inline).
                    "staged": int((result or {}).get("staged", 0) or 0),
                    # This run's "seen" ledger. Written on every collect, whether
                    # or not the analysis succeeded — that is what stops the next
                    # collection from re-reporting the same wall as new.
                    "content_hashes": list((result or {}).get("content_hashes") or []),
                }
            )
        except Exception as e:
            logger.error(f"collect failed for source {source.id}: {e}", exc_info=True)
            stats["error"] += 1
            stats["error_sources"].append(source.name)
            stats["error_messages"].append(f"{source.name}: {e}")
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
                    "auth_hint": getattr(e, "hint", "") if isinstance(e, AuthorizationRequired) else "",
                }
            )
    return stats


async def handle_digest(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a digest and publish it to configured channels.

    Payload:
        period: 'day' | 'week' (default 'day')
        agent_task_id: int | None — set when triggered by a task (idempotency)

    # TODO(future): per-source digest. If task.sources is non-empty — filter the
    # aggregation by them (requires source_ids support in ReportAggregator).
    # Empty sources list = the whole workspace (current behaviour).
    """
    from app.services.digest.builder import build_and_publish

    task = await _load_task(payload.get("agent_task_id"))
    task_payload = _task_payload(task)

    period = task_payload.get("period", payload.get("period", "day"))
    if period not in ("day", "week", "month"):
        return {"status": "failed", "error": f"Invalid period: {period}"}
    # `--force-refresh` re-sends the digest even when it was already sent for
    # this task+period (skips the idempotency check).
    force = bool(task_payload.get("force_refresh") or payload.get("force_refresh"))
    return await build_and_publish(period=period, agent_task_id=payload.get("agent_task_id"), force=force)


async def handle_prune(payload: dict[str, Any]) -> dict[str, Any]:
    """Retention: trim old finished jobs (default: older than 7 days).

    Also sweeps raw content nobody ever analysed. `collected_items` is a
    write-ahead copy, so a collection whose analysis keeps failing (or a run
    with no job behind it, whose `run_id` is NULL) would keep its rows for
    ever; this is the ceiling on that. The sweep the table's own docstrings
    promise was never wired to anything — without it, "a failed analysis keeps
    the content" quietly becomes "keeps it until the disk fills".
    """
    from datetime import datetime, timedelta, timezone

    from app.core.database import new_session
    from app.models import CollectedItem, Job

    days = int(payload.get("days", 7))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = await Job.objects.filter(status__in=["done", "failed"]).filter(Job.created_at < cutoff).delete()

    # Unanalysed raw content: same age budget as the job history. Raw text is
    # kept only while an analysis might still want it, so `staged_days` follows
    # `days` unless the task says otherwise.
    staged_days = int(payload.get("staged_days", days))
    staged_deleted = 0
    session = new_session()
    try:
        async with session.begin():
            staged_deleted = await CollectedItem.objects.delete_older_than(session, staged_days)
    except Exception as e:  # noqa: BLE001 — a sweep failure must not fail the prune
        logger.warning(f"Could not sweep stale collected_items: {e}")
    finally:
        await session.close()

    return {"deleted": deleted, "staged_deleted": staged_deleted, "staged_days": staged_days}


async def handle_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze collected content and create bot actions if triggers match.

    Sources come from the task's m2m `sources` (task.sources); empty = all active.
    Scenario comes from the task's `agent_scenario` (fallback: tenant default).
    Payload may carry:
        scenario_id: int | None — explicit scenario override
        excluded_users: list[str] — users to skip

    Flow:
    1. Get sources and their scenarios
    2. For each source, run TriggerEvaluator.should_analyze (pre-filter)
    3. Run TriggerEvaluator.should_act on already-stored analysis result
    4. If action needed, check guards and create BotAction (dry_run=True by default)
    """
    from app.models import AgentScenario, AIAnalytics, BotAction, CollectedItem
    from app.services.ai.trigger_evaluator import trigger_evaluator
    from app.services.social.guards import extract_target_user, guards_checker
    from app.types import BotActionStatus

    task = await _load_task(payload.get("agent_task_id"))
    task_payload = _task_payload(task)
    scenario_id = task_payload.get("scenario_id") or payload.get("scenario_id")
    excluded_users = task_payload.get("excluded_users") or []

    stats: dict[str, Any] = {"sources": 0, "analyzed": 0, "actions_created": 0, "skipped": 0, "per_source": []}

    sources = await _resolve_sources(task, payload)

    for source in sources:
        if (source.external_id or "").lstrip("@") in excluded_users:
            stats["skipped"] += 1
            continue
        # Per-source counters, so the run-now modal can link to each source and
        # the source page can show what the agent analysed *here*. `analyzed` and
        # `actions_created` below stay run-wide; these are this source's share.
        per: dict[str, Any] = {"source_id": source.id, "name": source.name, "analyzed": 0, "actions": 0}
        try:
            stats["sources"] += 1

            # Resolve scenario — the task's own scenario, else an explicit
            # override, else the tenant default. A source carries no scenario.
            if task is not None and getattr(task, "agent_scenario", None):
                scenario = task.agent_scenario
            elif scenario_id:
                scenario = await AgentScenario.objects.get(id=scenario_id)
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
            try:
                staged = await CollectedItem.objects.for_source(source.id, limit=ANALYZE_STAGE_BATCH)
                if staged:
                    from app.services.ai.analyzer import AIAnalyzer

                    items = [row.as_agent_item() for row in staged]
                    fresh = await AIAnalyzer().analyze_content(items, source)
                    # Retire by what the analysis stored, not by the run the rows
                    # came from: rows staged by an API/CLI run carry no run id at
                    # all, and a partial analysis must leave the rest alone.
                    deleted = await _retire_staged(fresh, source.id)
                    if deleted:
                        logger.info(
                            f"Analysed {len(staged)} staged item(s) for source {source.id}, retired {deleted}"
                        )
                    else:
                        # Nothing was saved — keep every row and retry next run.
                        logger.warning(
                            f"Analysis of {len(staged)} staged item(s) for source {source.id} produced "
                            "no result; keeping them staged for a retry"
                        )
            except Exception as e:  # noqa: BLE001 — a staging problem must not abort the run
                logger.warning(f"Could not process staged items for source {source.id}: {e}", exc_info=True)

            # Get recent analytics for this source
            analytics = (
                await AIAnalytics.objects.filter(source_id=source.id).order_by(AIAnalytics.created_at.desc()).limit(10)
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
            filtered_content = await trigger_evaluator.should_analyze(content, scenario)

            if not filtered_content:
                stats["skipped"] += 1
                continue

            stats["analyzed"] += len(filtered_content)
            per["analyzed"] += len(filtered_content)

            # Post-filter: should_act on the already-stored analysis result
            for item in filtered_content:
                should_act = await trigger_evaluator.should_act(item.get("result") or {}, scenario)

                if not should_act:
                    continue

                # Extract target post from recent_posts (first available)
                recent_posts = item.get("recent_posts", [])
                target_post = recent_posts[0] if recent_posts else {}

                # Create BotAction (dry_run=True by default)
                action_payload = {
                    "action_type": scenario.action_type.name if scenario.action_type else "COMMENT",
                    "text": (item.get("result") or {}).get("response", ""),
                }
                # Add platform-specific target fields
                if target_post.get("owner_id"):
                    action_payload["owner_id"] = target_post["owner_id"]
                if target_post.get("post_id"):
                    action_payload["post_id"] = target_post["post_id"]
                if target_post.get("external_id"):
                    action_payload["external_id"] = target_post["external_id"]

                # Check guards (target user from the analysis feeds blacklist/whitelist)
                allowed, reason = await guards_checker.check(scenario, target_user=extract_target_user(action_payload))
                if not allowed:
                    logger.info(f"Action blocked by guards: {reason}")
                    continue

                # Idempotency: skip if PENDING action already exists for this analytics+scenario
                analytics_id = item.get("analytics_id")
                existing = await BotAction.objects.filter(
                    analytics_id=analytics_id,
                    agent_scenario_id=scenario.id,
                    status=BotActionStatus.PENDING,
                ).first()
                if existing:
                    logger.info(f"BotAction already exists for analytics {analytics_id}, scenario {scenario.id}")
                    continue

                action = await BotAction.objects.create(
                    agent_scenario_id=scenario.id,
                    source_id=source.id,
                    analytics_id=analytics_id,
                    action_type=scenario.action_type,
                    status=BotActionStatus.PENDING,
                    payload=action_payload,
                    dry_run=True,
                )
                stats["actions_created"] += 1
                per["actions"] += 1
                logger.info(f"Created BotAction#{action.id} for source {source.id}")

        except Exception as e:
            logger.error(f"analyze failed for source {source.id}: {e}", exc_info=True)
            stats["skipped"] += 1
        finally:
            # `finally`, not a line at the end of the body: this loop `continue`s
            # out early in several places (no scenario, no analytics, nothing to
            # analyse) and every one of them is still an outcome the user should
            # be able to reach from the modal.
            stats["per_source"].append(per)

    return stats


async def handle_learn(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract durable facts from recent chat into provenance-tagged memory.

    Payload:
        min_messages: int — new user turns required before an LLM call (default 8)
        window: int — max messages to scan per run (default 200)
    """
    from app.agent.learning import run_learn

    return await run_learn(
        min_messages=int(payload.get("min_messages", 8)),
        window=int(payload.get("window", 200)),
    )


async def handle_reflect(payload: dict[str, Any]) -> dict[str, Any]:
    """Weekly memory hygiene + prompt-evolution proposals (never applied silently).

    Payload:
        dedup: bool — apply delete/update ops proposed by the LLM (default True)
    """
    from app.agent.learning import run_reflect

    return await run_reflect(dedup=bool(payload.get("dedup", True)))


HANDLERS = {
    "collect": handle_collect,
    "digest": handle_digest,
    "prune": handle_prune,
    "analyze": handle_analyze,
    "learn": handle_learn,
    "reflect": handle_reflect,
}
