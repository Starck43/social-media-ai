"""Background job handlers. Heavy work runs here (worker process)."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_collect(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Collect content from sources.

    Payload:
        source_ids: list[int] — specific sources; empty/missing = all active
    """
    from app.models import Source
    from app.services.monitoring.collector import ContentCollector

    source_ids = payload.get("source_ids") or []
    collector = ContentCollector()

    if source_ids:
        sources = []
        for sid in source_ids:
            source = await Source.objects.get(id=sid)
            if source:
                sources.append(source)
    else:
        sources = await Source.objects.filter(is_active=True)

    stats = {"sources": len(sources), "collected": 0, "failed": 0, "items": 0}
    for source in sources:
        try:
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
    """
    from app.services.digest.builder import build_and_publish

    period = payload.get("period", "day")
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

    Payload:
        source_ids: list[int] — specific sources; empty/missing = all active
        scenario_id: int | None — specific scenario; empty/missing = auto-resolve

    Flow:
    1. Get sources and their scenarios
    2. For each source, run TriggerEvaluator.should_analyze (pre-filter)
    3. Run TriggerEvaluator.should_act on already-stored analysis result
    4. If action needed, check guards and create BotAction (dry_run=True by default)
    """
    from app.models import AgentScenario, AIAnalytics, BotAction, Source
    from app.services.ai.trigger_evaluator import trigger_evaluator
    from app.services.social.guards import extract_target_user, guards_checker
    from app.types import BotActionStatus

    source_ids = payload.get("source_ids") or []
    scenario_id = payload.get("scenario_id")

    stats = {"sources": 0, "analyzed": 0, "actions_created": 0, "skipped": 0}

    # Get sources
    if source_ids:
        sources = []
        for sid in source_ids:
            source = await Source.objects.get(id=sid)
            if source:
                sources.append(source)
    else:
        sources = await Source.objects.filter(is_active=True)

    stats["sources"] = len(sources)

    for source in sources:
        try:
            # Resolve scenario — fallback to tenant default if source has none
            if scenario_id:
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
