"""Tools: view and execute bot actions."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool


@tool(
    name="actions_log",
    description="Показать лог действий бота (bot_actions): последние N действий с их статусами.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Количество действий (по умолчанию 10)"},
        },
        "required": [],
    },
)
async def actions_log(limit: int = 10) -> dict[str, Any]:
    from app.models import BotAction

    actions = await BotAction.objects.order_by(BotAction.created_at.desc()).limit(limit)

    return {
        "actions": [
            {
                "id": a.id,
                "action_type": a.action_type.name,
                "status": a.status.name,
                "dry_run": a.dry_run,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "agent_scenario_id": a.agent_scenario_id,
                "source_id": a.source_id,
            }
            for a in actions
        ]
    }


@tool(
    name="action_send",
    description=(
        "Отправить действие бота (bot_action) по ID. "
        "Действие должно быть в статусе PENDING. "
        "Если dry_run=True, действие не публикуется, а возвращается payload."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action_id": {"type": "integer", "description": "ID действия из actions_log"},
            "dry_run": {"type": "boolean", "description": "Если True, не публиковать (по умолчанию True)"},
        },
        "required": ["action_id"],
    },
    confirm=True,
)
async def action_send(action_id: int, dry_run: bool = True) -> dict[str, Any]:
    from app.models import BotAction, AgentScenario, Platform, Source
    from app.models.managers.bot_action_manager import BotActionManager
    from app.services.social.guards import extract_target_user, guards_checker
    from app.types import BotActionStatus

    manager = BotActionManager()

    action = await BotAction.objects.get(id=action_id)
    if not action:
        return {"success": False, "error": f"Action {action_id} not found"}

    if action.status != BotActionStatus.PENDING:
        return {"success": False, "error": f"Action is not pending: {action.status.name}"}

    scenario = await AgentScenario.objects.get(id=action.agent_scenario_id)
    if not scenario:
        return {"success": False, "error": "Scenario not found"}

    # Check guards (target user from payload feeds blacklist/whitelist)
    allowed, reason = await guards_checker.check(scenario, target_user=extract_target_user(action.payload))
    if not allowed:
        return {"success": False, "error": f"Guards blocked: {reason}"}

    # Execute action based on platform
    source = await Source.objects.get(id=action.source_id)
    if not source:
        return {"success": False, "error": "Source not found"}

    platform = await Platform.objects.get(id=source.platform_id)

    result = await _execute_action(source, platform, action, dry_run)

    if result.get("success"):
        if dry_run:
            # Dry run: approve with system user (agent)
            await manager.approve_action(action_id=action_id, user_id=None)
            new_status = BotActionStatus.APPROVED
        else:
            # Real execution: mark as executed
            await manager.mark_executed(action_id=action_id, result=result)
            new_status = BotActionStatus.EXECUTED
    else:
        await manager.mark_failed(action_id=action_id, error=result.get("error", "Unknown error"))
        new_status = BotActionStatus.FAILED

    return {
        "success": result.get("success", False),
        "action_id": action.id,
        "status": new_status.name,
        "dry_run": dry_run,
        "payload": action.payload,
        "result": result,
    }


async def _execute_action(source, platform, action, dry_run: bool) -> dict[str, Any]:
    """Execute a bot action on the source platform."""
    from app.types import BotActionType, PlatformType

    action_type = action.action_type
    payload = action.payload or {}

    if platform.platform_type == PlatformType.VK:
        from app.services.social.vk_client import VKClient

        client = VKClient(platform=platform)
        if action_type == BotActionType.COMMENT:
            return await client.post_comment(
                owner_id=payload.get("owner_id", 0),
                post_id=payload.get("post_id", 0),
                message=payload.get("text", ""),
                dry_run=dry_run,
            )
        else:
            return {"success": False, "error": f"Unsupported action type for VK: {action_type.name}"}

    elif platform.platform_type == PlatformType.TELEGRAM:
        from app.services.social.tg_client import TelegramClient

        client = TelegramClient(platform=platform)
        if action_type in (BotActionType.COMMENT, BotActionType.REPLY, BotActionType.DIRECT_MESSAGE):
            return await client.send_message(
                chat_id=payload.get("chat_id", source.external_id),
                text=payload.get("text", ""),
                dry_run=dry_run,
            )
        else:
            return {"success": False, "error": f"Unsupported action type for Telegram: {action_type.name}"}

    else:
        return {"success": False, "error": f"Unsupported platform: {platform}"}
