"""Bounded read-time attention projection; no writes, sends or provider probes.

The projection uses WebPerms supplied by the fresh web request. ORM access stays
behind existing managers. Pure projection tests inject the repository and never
import the application or its database environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
EVENT_WINDOW = 100
TASK_WINDOW = 50
ENTITY_MODELS = {
    "task": "agenttask",
    "agenttask": "agenttask",
    "source": "source",
    "analysis": "aianalytics",
    "aianalytics": "aianalytics",
    "digest": "digestrun",
    "digestrun": "digestrun",
    "scenario": "agentscenario",
    "agentscenario": "agentscenario",
    "platform": "platform",
}
DESTINATIONS = {
    "agenttask": "/app/tasks",
    "source": "/app/sources",
    "aianalytics": "/app/analytics",
    "digestrun": "/app/digests",
    "agentscenario": "/app/scenarios",
    "platform": "",
}
EVENT_LABELS = {
    "report_ready": ("Результат готов", "Откройте раздел, чтобы посмотреть результат."),
    "api_error": ("Сообщение об ошибке", "Операция потребовала внимания. Проверьте доступный раздел."),
    "connection_error": (
        "Ошибка подключения",
        "Проверьте доступный раздел. Сообщение не раскрывает личные подключения.",
    ),
    "source_inactive": ("Источник неактивен", "Проверьте состояние доступного источника."),
    "rate_limit_warning": ("Предупреждение о лимите", "Проверьте настройки доступной интеграции."),
    "mood_change": ("Изменение настроения", "Откройте доступный результат анализа."),
    "trend_alert": ("Изменение тренда", "Откройте доступный результат анализа."),
    "topic_resumed": ("Тема возобновлена", "Откройте доступный результат анализа."),
    "keyword_mention": ("Новое упоминание", "Откройте доступный источник или результат."),
}


@dataclass(frozen=True)
class AttentionContext:
    workspace_id: int
    user_id: int

    def payload(self):
        return {"workspace_id": self.workspace_id, "user_id": self.user_id}


def positive_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def moment(value):
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def timestamp(value):
    parsed = moment(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed is not None else ""


def event_type(row):
    kind = row.notification_type
    return str(getattr(kind, "db_value", None) or getattr(kind, "name", None) or kind or "").lower()


def can(perms, model, action="view"):
    return perms is not None and perms.can(model, action)


class AttentionRepository:
    """Existing manager/session conventions with explicit workspace predicates."""

    async def events(self, context, perms):
        from app.models import Notification

        aliases = [alias for alias, model in ENTITY_MODELS.items() if can(perms, model)]
        criterion = Notification.related_entity_type.is_(None) | Notification.related_entity_type.in_(aliases)
        return (
            await Notification.objects.filter(criterion, tenant_id=context.workspace_id, is_read=False)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(EVENT_WINDOW)
        )

    async def task_failures(self, context):
        from app.models import AgentTask

        return (
            await AgentTask.objects.filter(tenant_id=context.workspace_id, last_status="failed")
            .order_by(AgentTask.last_run_at.desc(), AgentTask.id.desc())
            .limit(TASK_WINDOW)
        )

    async def entities(self, model, ids, context):
        from app.models import AgentScenario, AgentTask, AIAnalytics, DigestRun, Platform, Source

        cls = {
            "source": Source,
            "agenttask": AgentTask,
            "aianalytics": AIAnalytics,
            "agentscenario": AgentScenario,
            "digestrun": DigestRun,
            "platform": Platform,
        }[model]
        filters = {"id__in": sorted(ids)}
        if model != "platform":
            filters["tenant_id"] = context.workspace_id
        return await cls.objects.filter(**filters).limit(EVENT_WINDOW)

    async def connections(self, context):
        from app.services.social.connections import statuses_needing_action

        # Caller ID only; no workspace owner's credentials, reveal(), or probes.
        return await statuses_needing_action(context.user_id)

    async def event(self, context, notification_id):
        from app.models import Notification

        return await Notification.objects.filter(tenant_id=context.workspace_id, id=notification_id).first()

    async def mark_read(self, context, ids):
        from app.models import Notification

        return await Notification.objects.filter(tenant_id=context.workspace_id, id__in=ids, is_read=False).update(
            is_read=True
        )


class AttentionFeed:
    def __init__(self, context, perms, repository=None):
        self.context, self.perms = context, perms
        self.repo = repository or AttentionRepository()

    async def eligible_events(self, rows, *, include_read=False):
        """One ACL/entity predicate reused by list and every read mutation."""
        if not can(self.perms, "notification"):
            return []
        candidates, ids = [], {}
        for row in rows:
            if (
                row.tenant_id != self.context.workspace_id
                or (row.is_read and not include_read)
                or not positive_id(row.id)
            ):
                continue
            entity = row.related_entity_type
            if entity is None:
                # System/operator events never become anonymous workspace content.
                if event_type(row) in {"system_backup", "system_update"} and not self.perms.is_superuser_role:
                    continue
                candidates.append((row, None))
                continue
            model = ENTITY_MODELS.get(entity)
            if model is None or not can(self.perms, model) or not positive_id(row.related_entity_id):
                continue
            candidates.append((row, model))
            ids.setdefault(model, set()).add(row.related_entity_id)
        entities = {}
        for model, values in ids.items():
            entities[model] = {
                row.id: row
                for row in await self.repo.entities(model, values, self.context)
                if model == "platform" or row.tenant_id == self.context.workspace_id
            }
        result = []
        for row, model in candidates:
            if not can(self.perms, "notification") or (model and not can(self.perms, model)):
                continue
            target = entities.get(model, {}).get(row.related_entity_id) if model else None
            if model is not None and target is None:
                continue
            label, summary = EVENT_LABELS.get(
                event_type(row), ("Сообщение пространства", "Откройте доступный раздел для проверки.")
            )
            # Never return stored exception/title/message/provider content.
            if model in {"agenttask", "source", "agentscenario"}:
                label += ": " + str(target.name)[:100]
            item = dict(
                key=f"notification:{self.context.workspace_id}:{row.id}",
                kind="notification",
                scope="workspace",
                workspace_id=self.context.workspace_id,
                title=label,
                message=summary,
                state="unread",
                created_at=timestamp(row.created_at),
                destination=DESTINATIONS.get(model, ""),
                action="read" if can(self.perms, "notification", "update") else "none",
                id=row.id,
                read_id=row.id if can(self.perms, "notification", "update") else None,
            )
            result.append((row, model, target, item))
        return result

    def task_item(self, task):
        if task.tenant_id != self.context.workspace_id or task.last_status != "failed" or not positive_id(task.id):
            return None
        return dict(
            key=f"task:{self.context.workspace_id}:{task.id}:failed:{timestamp(task.last_run_at)}",
            kind="task",
            scope="workspace",
            workspace_id=self.context.workspace_id,
            title="Требует проверки: " + str(task.name)[:100],
            message="Последний запуск требует проверки. Прочтение сообщения не устраняет причину.",
            state="failed",
            created_at=timestamp(task.last_run_at),
            destination="/app/tasks",
            action="open",
            read_id=None,
        )

    async def snapshot(self, limit=30):
        limit = max(1, min(int(limit), 100))
        items, errors, task_signals = [], [], {}
        if can(self.perms, "agenttask"):
            try:
                for task in await self.repo.task_failures(self.context):
                    item = self.task_item(task)
                    if item:
                        task_signals[task.id] = (task, item)
            except Exception:
                errors.append("tasks")
                logger.warning("Attention task projection unavailable")
        try:
            platforms = {"vk": "ВКонтакте", "telegram": "Telegram"}
            for status in await self.repo.connections(self.context):
                if (
                    status.platform not in platforms
                    or status.state not in {"reauth", "missing"}
                    or not status.needs_action
                    or not status.can_connect
                ):
                    continue
                items.append(
                    dict(
                        key=f"connection:{self.context.workspace_id}:{self.context.user_id}:{status.platform}:{status.state}",
                        kind="connection",
                        scope="personal",
                        workspace_id=self.context.workspace_id,
                        title=platforms[status.platform]
                        + (": войдите заново" if status.state == "reauth" else ": подключите личный доступ"),
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
            logger.warning("Attention connection projection unavailable")
        events = []
        if can(self.perms, "notification"):
            try:
                events = await self.eligible_events(await self.repo.events(self.context, self.perms))
            except Exception:
                errors.append("messages")
                logger.warning("Attention event projection unavailable")
        for row, model, target, item in events:
            # Coalesce entity-level errors since the recorded failed run.
            # Notification has no job ID: do not claim exact execution identity.
            # Historical events stay events; they never establish current failure.
            signal = None
            if model == "agenttask" and event_type(row) == "api_error" and "tasks" not in errors:
                run, emitted = moment(target.last_run_at), moment(row.created_at)
                if target.last_status == "failed" and run is not None and emitted is not None and emitted >= run:
                    signal = task_signals.setdefault(target.id, (target, self.task_item(target)))[1]
            if signal is not None:
                if signal["read_id"] is None and item["read_id"] is not None:
                    signal["read_id"] = item["read_id"]
            else:
                items.append(item)
        if can(self.perms, "agenttask"):
            items.extend(item for _, item in task_signals.values())
        # Signals precede messages. Keys/count/list all describe this same window,
        # never raw DB counts or a claim that unseen/failed sources are healthy.
        connections = [item for item in items if item["kind"] == "connection"]
        tasks = sorted(
            (item for item in items if item["kind"] == "task"),
            key=lambda item: (item["created_at"], item["key"]),
            reverse=True,
        )
        events = sorted(
            (item for item in items if item["kind"] == "notification"),
            key=lambda item: (item["created_at"], item["key"]),
            reverse=True,
        )
        deduped = list({item["key"]: item for item in [*connections, *tasks, *events]}.values())
        visible = deduped[:limit]
        return dict(
            context=self.context.payload(),
            notifications=visible,
            count=len(visible),
            has_more=len(deduped) > limit,
            complete=not errors,
            unavailable=errors,
            windowed=True,
        )

    async def read_one(self, notification_id):
        if not positive_id(notification_id):
            return False
        if not can(self.perms, "notification", "update") or not can(self.perms, "notification"):
            return False
        row = await self.repo.event(self.context, notification_id)
        if row is None or not await self.eligible_events([row], include_read=True):
            return False
        # Recheck the same WebPerms immediately before the workspace-bound write.
        if not can(self.perms, "notification", "update") or not can(self.perms, "notification"):
            return False
        model = ENTITY_MODELS.get(row.related_entity_type)
        if model and not can(self.perms, model):
            return False
        # Shared acknowledgements are idempotent when another member already
        # read the event. Verify visibility first; never acknowledge a hidden ID.
        if row.is_read:
            return True
        if await self.repo.mark_read(self.context, [row.id]):
            return True
        current = await self.repo.event(self.context, row.id)
        return bool(
            current is not None and current.is_read and await self.eligible_events([current], include_read=True)
        )

    async def read_window(self):
        if not can(self.perms, "notification", "update") or not can(self.perms, "notification"):
            return None
        events = await self.eligible_events(await self.repo.events(self.context, self.perms))
        ids = [row.id for row, _, _, _ in events]
        if not can(self.perms, "notification", "update"):
            return None
        return await self.repo.mark_read(self.context, ids) if ids else 0
