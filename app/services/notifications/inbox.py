"""Personal inbox projection and server-owned, reauthorized destinations."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.tenant_context import tenant_scope
from app.models import NotificationReceipt
from app.models.managers.tenant_manager import TenantUserManager
from app.types import ActionType

from .attention import ENTITY_MODELS as WORKSPACE_ENTITY_MODELS
from .attention import positive_id, timestamp

ENTITY_MODELS = {**WORKSPACE_ENTITY_MODELS, "action": "botaction", "agentaction": "botaction", "botaction": "botaction"}


@dataclass(frozen=True)
class InboxEntry:
    receipt: object
    event: object
    destination: str


class PersonalInbox:
    def __init__(self, user):
        self.user = user
        self._memberships = None
        self._grants = {}

    async def destination(self, event):
        manager = TenantUserManager()
        if self._memberships is None:
            self._memberships = await manager.web_memberships(self.user.id)
        membership = next((m for m in self._memberships if m.tenant_id == event.tenant_id), None)
        if membership is None or membership.role_id is None:
            return None
        if event.tenant_id not in self._grants:
            self._grants[event.tenant_id] = await manager.member_grants(membership)
        if event.related_entity_type is None:
            return "/app/"
        model = ENTITY_MODELS.get(event.related_entity_type)
        if (
            not model
            or not positive_id(event.related_entity_id)
            or (model, ActionType.VIEW) not in self._grants[event.tenant_id]
        ):
            return None
        from app.models import AgentAction, AgentScenario, AgentTask, AIAnalytics, DigestRun, Platform, Source

        cls, root = {
            "botaction": (AgentAction, "/app/actions"),
            "agenttask": (AgentTask, "/app/tasks"),
            "source": (Source, "/app/sources"),
            "aianalytics": (AIAnalytics, "/app/analytics"),
            "agentscenario": (AgentScenario, "/app/scenarios"),
            "digestrun": (DigestRun, "/app/digests"),
            "platform": (Platform, ""),
        }[model]
        if not root:
            return None
        with tenant_scope(event.tenant_id):
            target = await cls.objects.get(id=event.related_entity_id)
        if target is None or getattr(target, "tenant_id", event.tenant_id) != event.tenant_id:
            return None
        return root if model == "digestrun" else f"{root}/{target.id}"

    async def entries(self, **filters):
        rows = await NotificationReceipt.objects.list_for_user(self.user.id, **filters)
        entries = []
        for receipt, event in rows:
            destination = await self.destination(event)
            if destination is not None:
                entries.append(InboxEntry(receipt, event, destination))
        return entries

    async def read(self, notification_id):
        entries = await self.entries(notification_id=notification_id)
        if not entries:
            return False
        await NotificationReceipt.objects.mark_read(self.user.id, [notification_id])
        return True

    async def read_window(self):
        entries = await self.entries(is_read=False, limit=100)
        return (
            await NotificationReceipt.objects.mark_read(self.user.id, [e.event.id for e in entries]) if entries else 0
        )

    async def snapshot(self, workspace_id, limit=30):
        entries = await self.entries(is_read=False, limit=100)
        limit = max(1, min(limit, 100))
        items = [
            dict(
                key=f"notification:user:{self.user.id}:{e.event.id}",
                kind="notification",
                scope="personal",
                user_id=self.user.id,
                workspace_id=e.event.tenant_id,
                title=e.event.title,
                message=e.event.message,
                severity=e.event.severity,
                state="unread",
                created_at=timestamp(e.event.created_at),
                destination=f"/app/notifications/open/{e.event.id}",
                action="read",
                id=e.event.id,
                read_id=e.event.id,
            )
            for e in entries
        ]
        errors = []
        connections = []
        try:
            from app.services.social.connections import statuses_needing_action

            for status in await statuses_needing_action(self.user.id):
                if (
                    status.platform not in {"vk", "telegram"}
                    or status.state not in {"reauth", "missing"}
                    or not status.needs_action
                    or not status.can_connect
                ):
                    continue
                connections.append(
                    dict(
                        key=f"connection:{workspace_id}:{self.user.id}:{status.platform}:{status.state}",
                        kind="connection",
                        scope="personal",
                        workspace_id=workspace_id,
                        title={"vk": "ВКонтакте", "telegram": "Telegram"}[status.platform]
                        + ": проверьте личное подключение",
                        message="Ваше личное подключение. Изменить его можете только вы.",
                        state=status.state,
                        created_at="",
                        destination="/app/settings?tab=connections",
                        action="open",
                        read_id=None,
                    )
                )
        except Exception:
            errors.append("connections")
        deliveries = []
        try:
            from .delivery_signal import bell_item, personal_signals

            # Read-time aggregate (P8-T11): no receipt, nothing to mark read.
            deliveries = [bell_item(self.user.id, signal) for signal in await personal_signals(self.user)]
        except Exception:
            errors.append("deliveries")
        items = [*connections, *deliveries, *items]
        return dict(
            context={"workspace_id": workspace_id, "user_id": self.user.id},
            notifications=items[:limit],
            count=len(items[:limit]),
            has_more=len(items) > limit,
            complete=not errors,
            unavailable=errors,
            task_events_authorized=False,
            windowed=True,
        )
