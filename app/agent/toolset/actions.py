"""Tools: view and execute bot actions."""

from __future__ import annotations

from typing import Any, Optional

from app.agent.tools import tool
from app.core.permissions import require_permission


@tool(
    name="actions_log",
    required_permission="botaction.view",
    description="Показать лог действий бота (bot_actions): последние N действий с их статусами.",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Количество действий (по умолчанию 10)"},
        },
        "required": [],
    },
)
@require_permission("botaction", "view")
async def actions_log(limit: int = 10) -> dict[str, Any]:
    from app.models import BotAction

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return {"error": "Limit must be an integer from 1 to 100", "code": "invalid_arguments"}
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


async def _auto_actions_forced_dry_run() -> Optional[str]:
    """Why the ambient workspace may not publish an action, or None.

    Reads the ambient tenant scope: the agent loop and the web UI both run
    inside the workspace whose action this is, and the action's own tenant is
    not on the tool's arguments. Outside any workspace (an operator driving the
    CLI) the tier does not apply.
    """
    from app.core.tenant_context import current_tenant_id
    from app.models.managers.tenant_manager import tenants

    tenant_id = current_tenant_id()
    if tenant_id is None:
        return None
    tenant = await tenants.get(id=tenant_id)
    if tenant is None or tenant.has_feature("allow_auto_actions"):
        return None
    return (
        f"Тариф «{tenant.plan_label}» не включает автопубликацию — действие выполнено в режиме dry-run."
    )


@tool(
    name="action_send",
    description="Показать локальное превью PENDING-действия по ID. Публикация отключена; статус не меняется.",
    parameters={
        "type": "object",
        "properties": {
            "action_id": {"type": "integer", "minimum": 1, "description": "ID из actions_log"},
            "dry_run": {"type": "boolean", "enum": [True], "description": "Только True: локальное превью"},
        },
        "required": ["action_id"],
    },
    confirm=True,
    # Preview-only contract: the handler never publishes, so the right it needs
    # is the read right (`botaction.view`). The decorator must sit on this
    # handler — it used to decorate the `_auto_actions_forced_dry_run` helper
    # above, which registered that no-argument helper under the tool's name and
    # dispatched every «action_send» call to a function that only returns a
    # reason string. `botaction.view` is the bare model name, the shape
    # `has_permission_by_codename` splits — not the stored `social.`-prefixed
    # permission codename. Live publishing stays disabled behind a separate
    # contract; enabling it would need `botaction.update`, not this file.
    required_permission="botaction.view",
)
@require_permission("botaction", "view")
async def action_send(action_id: int, dry_run: bool = True) -> dict[str, Any]:
    """Read-only preview. Approval/publication requires a separate contract."""
    if dry_run is not True:
        return {
            "success": False, "error": "Live action sending is disabled", "code": "live_action_disabled",
            "dry_run": False,
        }
    if isinstance(action_id, bool) or not isinstance(action_id, int) or not 0 < action_id <= 2**31 - 1:
        return {"success": False, "error": "Invalid action ID", "code": "invalid_arguments", "dry_run": True}

    from app.models import AgentScenario, BotAction, Source
    from app.services.social.guards import extract_target_user, guards_checker
    from app.types import BotActionStatus

    action = await BotAction.objects.get(id=action_id)
    if action is None:
        return {"success": False, "error": "Action not found", "dry_run": True}
    if action.status != BotActionStatus.PENDING:
        return {"success": False, "error": "Action is not pending", "dry_run": True}
    if not isinstance(action.payload, dict):
        return {"success": False, "error": "Invalid action payload", "dry_run": True}
    scenario = await AgentScenario.objects.get(id=action.agent_scenario_id)
    source = await Source.objects.get(id=action.source_id)
    if scenario is None or source is None:
        return {"success": False, "error": "Action references are not available in this workspace", "dry_run": True}
    allowed, _reason = await guards_checker.check_for_action(action, target_user=extract_target_user(action.payload))
    if not allowed:
        return {"success": False, "error": "Action preview blocked by guards", "code": "guards_blocked", "dry_run": True}
    # Do not call providers, approve_action, mark_executed or mark_failed.
    # A preview is not an approval and cannot enable later publication by accident.
    return {
        "success": True, "action_id": action.id, "status": action.status.name,
        "dry_run": True, "preview_only": True, "payload": dict(action.payload),
        "result": {"success": True, "dry_run": True, "preview_only": True},
    }


async def _execute_action(source, platform, action, dry_run: bool) -> dict[str, Any]:
    """Execute a bot action on the source platform."""
    from app.types import AgentActionType, PlatformType

    action_type = action.action_type
    payload = action.payload or {}

    if platform.platform_type == PlatformType.VK:
        from app.services.social.vk_client import VKClient

        client = VKClient(platform=platform)
        if action_type == AgentActionType.COMMENT:
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
        if action_type in (AgentActionType.COMMENT, AgentActionType.REPLY, AgentActionType.DIRECT_MESSAGE):
            return await client.send_message(
                chat_id=payload.get("chat_id", source.external_id),
                text=payload.get("text", ""),
                dry_run=dry_run,
            )
        else:
            return {"success": False, "error": f"Unsupported action type for Telegram: {action_type.name}"}

    else:
        return {"success": False, "error": f"Unsupported platform: {platform}"}
