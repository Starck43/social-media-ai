"""Background job handlers. Heavy work runs here (worker process)."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _load_task(task_id: int | None):
    """Load the AgentTask (with its m2m sources) that triggered this job, if any."""
    if not task_id:
        return None
    from app.models import AgentTask

    return await (
        AgentTask.objects.filter(id=task_id)
        .prefetch_related("sources", "agent_scenario")
        .first()
    )


async def _resolve_sources(task, payload: dict[str, Any] | None = None) -> list:
    """Sources a job should operate on.

    Sources come from the task's m2m `sources`; an empty set means all active
    sources. When no task triggered the job, fall back to the legacy
    `payload["source_ids"]` if present, else all active sources.
    """
    from app.models import Source

    if task is not None:
        if task.sources:
            return list(task.sources)
        return list(await Source.objects.filter(is_active=True))

    source_ids = (payload or {}).get("source_ids") or []
    if source_ids:
        return list(await Source.objects.filter(id__in=source_ids))
    return list(await Source.objects.filter(is_active=True))


def _task_payload(task) -> dict[str, Any]:
    """Payload dict of the task (or empty if there is no task)."""
    return dict(task.payload or {}) if task is not None else {}


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

    sources = await _resolve_sources(task, payload)

    collector = ContentCollector()

    def _is_excluded(source) -> bool:
        external = (source.external_id or "").lstrip("@")
        return external in excluded_users

    stats = {"sources": 0, "collected": 0, "failed": 0, "items": 0, "excluded": 0}
    for source in sources:
        if _is_excluded(source):
            stats["excluded"] += 1
            continue
        stats["sources"] += 1
        try:
            if monitored_users:
                result = await collector.collect_monitored_users(source, analyze=True)
                if result and result.get("total_items", 0) > 0:
                    stats["collected"] += 1
                    stats["items"] += result["total_items"]
                else:
                    stats["failed"] += 1
            else:
                result = await collector.collect_from_source(source)
                if result and result.get("content_count", 0) > 0:
                    stats["collected"] += 1
                    stats["items"] += result["content_count"]
                else:
                    stats["failed"] += 1
        except Exception as e:
            logger.error(f"collect failed for source {source.id}: {e}", exc_info=True)
            stats["failed"] += 1
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
    if period not in ("day", "week"):
        return {"status": "failed", "error": f"Invalid period: {period}"}
    return await build_and_publish(period=period, agent_task_id=payload.get("agent_task_id"))


async def handle_prune(payload: dict[str, Any]) -> dict[str, Any]:
    """Retention: trim old finished jobs (default: older than 7 days)."""
    from datetime import datetime, timedelta, timezone

    from app.models import Job

    days = int(payload.get("days", 7))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = await Job.objects.filter(status__in=["done", "failed"]).filter(Job.created_at < cutoff).delete()
    return {"deleted": deleted}


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
    from app.models import AgentScenario, AIAnalytics, BotAction
    from app.services.ai.trigger_evaluator import trigger_evaluator
    from app.services.social.guards import extract_target_user, guards_checker
    from app.types import BotActionStatus

    task = await _load_task(payload.get("agent_task_id"))
    task_payload = _task_payload(task)
    scenario_id = task_payload.get("scenario_id") or payload.get("scenario_id")
    excluded_users = task_payload.get("excluded_users") or []

    stats = {"sources": 0, "analyzed": 0, "actions_created": 0, "skipped": 0}

    sources = await _resolve_sources(task, payload)

    for source in sources:
        if (source.external_id or "").lstrip("@") in excluded_users:
            stats["skipped"] += 1
            continue
        try:
            stats["sources"] += 1

            # Resolve scenario — prefer the task's own scenario, then an explicit
            # override, then the source's scenario, then the tenant default.
            if task is not None and getattr(task, "agent_scenario", None):
                scenario = task.agent_scenario
            elif scenario_id:
                scenario = await AgentScenario.objects.get(id=scenario_id)
            elif source.agent_scenario_id:
                scenario = await AgentScenario.objects.get(id=source.agent_scenario_id)
            else:
                scenario = None

            if not scenario or not scenario.is_active:
                # Fallback: tenant's default scenario
                scenario = await AgentScenario.objects.get_default_scenario(tenant_id=source.tenant_id)

            if not scenario or not scenario.is_active:
                stats["skipped"] += 1
                continue

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
                logger.info(f"Created BotAction#{action.id} for source {source.id}")

        except Exception as e:
            logger.error(f"analyze failed for source {source.id}: {e}", exc_info=True)
            stats["skipped"] += 1

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
