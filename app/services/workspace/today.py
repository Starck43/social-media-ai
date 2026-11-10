"""Read-time «Сегодня» home projection, actor state, and start-page fallback (P20-T07).

Invariants (`docs/WORKSPACE_UX.md#today-page`, `docs/tasks/p20-t07.md`):
- Read-time projection over owning modules: no duplicate task/action/message tables.
- Section order is fixed:
  1. `brief` («Утренняя сводка»)
  2. `decisions` («Ждут решения»)
  3. `today_plan` («По плану на сегодня»)
  4. `awaiting_reply` («Ждут ответа»)
  5. `attention` («Требует внимания»)
  6. `recent_results` («Последние результаты»)
- Every provider is optional, tenant-isolated, permission-filtered, and bounded.
- One failing provider reports `status="unavailable"` with a clear reason and never blanks the page.
- «Моё / Команда» (`scope="mine" | "team"`, default `"mine"`) widens only within the caller's role and permissions.
- Per-actor dismiss/snooze/seen state stores only `{item_key, state, until_iso, updated_at_iso}`,
  never copying module rows, bodies, or approval state (`D11`, `D23/D24`).
- Start-page preference (`O-2`) defaults to `/app/` («Сегодня») and falls back safely to `/app/`
  whenever a chosen section is unavailable or unpermitted for the active workspace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from app.web.nav import START_PAGE_ITEMS, allows

NAV_BY_KEY = {item.key: item for item in START_PAGE_ITEMS}

logger = logging.getLogger(__name__)

PROVIDER_LIMIT = 5
DECISIONS_LIMIT = 7
TODAY_SECTION_ORDER: tuple[str, ...] = (
    "brief",
    "decisions",
    "today_plan",
    "awaiting_reply",
    "attention",
    "recent_results",
)
TODAY_SECTION_TITLES: dict[str, str] = {
    "brief": "Утренняя сводка",
    "decisions": "Ждут решения",
    "today_plan": "По плану на сегодня",
    "awaiting_reply": "Ждут ответа",
    "attention": "Требует внимания",
    "recent_results": "Последние результаты",
}
VALID_SCOPES = frozenset({"mine", "team"})
VALID_ITEM_OPERATIONS = frozenset({"seen", "snooze", "dismiss", "restore"})
MANAGER_ROLES = frozenset({"owner", "admin", "manager", "OWNER", "ADMIN", "MANAGER"})

# Per-user start page and per-actor Today item state (process-level + session-backed; no schema migration).
_USER_START_PAGES: dict[int, str] = {}
_ACTOR_ITEM_STATES: dict[tuple[int, int], dict[str, dict[str, str | None]]] = {}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _can(perms: Any, resource: str, action: str = "view") -> bool:
    if perms is None or not hasattr(perms, "can"):
        return False
    try:
        return bool(perms.can(resource, action))
    except Exception:
        return False


def _is_manager_role(perms: Any) -> bool:
    if perms is None:
        return False
    if getattr(perms, "is_owner", False):
        return True
    role = getattr(perms, "workspace_role", None)
    role_str = str(getattr(role, "value", None) or getattr(role, "name", None) or role or "")
    return role_str in MANAGER_ROLES


def normalize_scope(scope: str | None) -> str:
    val = (scope or "").strip().lower()
    return val if val in VALID_SCOPES else "mine"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_or_empty(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "")


# ------------------------------------------------ Start-page preference (O-2) ----


def available_start_pages(perms: Any, nav_items=START_PAGE_ITEMS) -> list[dict[str, str]]:
    """Return ready, permitted navigation items that the user can pick as start page."""
    options: list[dict[str, str]] = []
    for item in nav_items:
        if not item.ready:
            continue
        if item.key == "settings":
            continue
        if item.key == "dashboard" or allows(item, perms):
            options.append(
                {
                    "key": item.key,
                    "label": "Сегодня (по умолчанию)" if item.key == "dashboard" else item.label,
                    "href": item.href,
                }
            )
    if not any(opt["key"] == "dashboard" for opt in options):
        options.insert(0, {"key": "dashboard", "label": "Сегодня (по умолчанию)", "href": "/app/"})
    return options


def get_user_start_page(user_id: int | None, session: dict[str, Any] | None = None) -> str:
    if isinstance(session, dict):
        sess_val = session.get("start_page")
        if isinstance(sess_val, str) and sess_val in NAV_BY_KEY and sess_val != "settings":
            return sess_val
    if _positive_int(user_id):
        stored = _USER_START_PAGES.get(int(user_id))
        if isinstance(stored, str) and stored in NAV_BY_KEY and stored != "settings":
            return stored
    return "dashboard"


def set_user_start_page(
    user_id: int | None,
    page_key: str,
    *,
    session: dict[str, Any] | None = None,
) -> str:
    """Validate and save per-user start page key (defaults to 'dashboard' on unknown keys)."""
    cleaned = (page_key or "").strip().lower()
    if cleaned not in NAV_BY_KEY or cleaned == "settings":
        raise ValueError("invalid_start_page")
    if _positive_int(user_id):
        _USER_START_PAGES[int(user_id)] = cleaned
    if isinstance(session, dict):
        session["start_page"] = cleaned
    return cleaned


def resolve_start_page(
    user_id: int | None,
    perms: Any,
    *,
    session: dict[str, Any] | None = None,
    nav_items=START_PAGE_ITEMS,
) -> dict[str, Any]:
    """Resolve user's preferred start page with safe fallback to '/app/' ('Сегодня')."""
    preferred_key = get_user_start_page(user_id, session=session)
    by_key = {item.key: item for item in nav_items}
    item = by_key.get(preferred_key)
    if preferred_key == "dashboard":
        return {
            "preferred_key": "dashboard",
            "effective_key": "dashboard",
            "href": "/app/",
            "fell_back": False,
        }
    if item is None or not item.ready or not allows(item, perms):
        return {
            "preferred_key": preferred_key,
            "effective_key": "dashboard",
            "href": "/app/",
            "fell_back": True,
        }
    return {
        "preferred_key": preferred_key,
        "effective_key": item.key,
        "href": item.href,
        "fell_back": False,
    }


# ------------------------------------ Per-actor dismiss/snooze/seen state ----


def get_actor_item_states(
    user_id: int | None,
    workspace_id: int | None,
    *,
    session: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, dict[str, str | None]]:
    """Return active per-actor item states without any module row payload."""
    if not _positive_int(user_id) or not _positive_int(workspace_id):
        return {}
    now = now or _utc_now()
    bucket_key = (int(user_id), int(workspace_id))
    merged: dict[str, dict[str, str | None]] = {}
    for source in (
        _ACTOR_ITEM_STATES.get(bucket_key, {}),
        (
            session.get("today_item_states", {}).get(f"{user_id}:{workspace_id}", {})
            if isinstance(session, dict)
            else {}
        ),
    ):
        if not isinstance(source, dict):
            continue
        for item_key, meta in source.items():
            if not isinstance(item_key, str) or not isinstance(meta, dict):
                continue
            state = meta.get("state")
            if state not in {"seen", "snoozed", "dismissed"}:
                continue
            until_iso = meta.get("until_iso")
            if state == "snoozed" and isinstance(until_iso, str) and until_iso:
                try:
                    until_dt = datetime.fromisoformat(until_iso)
                    if until_dt.tzinfo is None:
                        until_dt = until_dt.replace(tzinfo=timezone.utc)
                    if now >= until_dt:
                        continue
                except ValueError:
                    continue
            merged[item_key] = {
                "item_key": item_key,
                "state": state,
                "until_iso": until_iso if isinstance(until_iso, str) else None,
                "updated_at_iso": str(meta.get("updated_at_iso") or ""),
            }
    return merged


def record_actor_item_state(
    user_id: int | None,
    workspace_id: int | None,
    item_key: str,
    operation: str,
    *,
    snooze_minutes: int = 240,
    session: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, str | None] | None:
    """Record per-actor dismiss/snooze/seen/restore state for an opaque item key."""
    if not _positive_int(user_id) or not _positive_int(workspace_id):
        raise PermissionError("today_actor_required")
    cleaned_key = (item_key or "").strip()
    if not cleaned_key or len(cleaned_key) > 160 or any(ch.isspace() for ch in cleaned_key):
        raise ValueError("invalid_item_key")
    op = (operation or "").strip().lower()
    if op not in VALID_ITEM_OPERATIONS:
        raise ValueError("invalid_operation")

    now = now or _utc_now()
    bucket_key = (int(user_id), int(workspace_id))
    bucket = _ACTOR_ITEM_STATES.setdefault(bucket_key, {})

    if op == "restore":
        bucket.pop(cleaned_key, None)
        if isinstance(session, dict):
            sess_root = session.setdefault("today_item_states", {})
            sess_bucket = sess_root.get(f"{user_id}:{workspace_id}")
            if isinstance(sess_bucket, dict):
                sess_bucket.pop(cleaned_key, None)
        return None

    state_map = {"seen": "seen", "snooze": "snoozed", "dismiss": "dismissed"}
    state = state_map[op]
    minutes = max(5, min(int(snooze_minutes or 240), 7 * 24 * 60))
    until_iso = (now + timedelta(minutes=minutes)).isoformat() if state == "snoozed" else None
    entry: dict[str, str | None] = {
        "item_key": cleaned_key,
        "state": state,
        "until_iso": until_iso,
        "updated_at_iso": now.isoformat(),
    }
    bucket[cleaned_key] = entry
    if isinstance(session, dict):
        sess_root = session.setdefault("today_item_states", {})
        sess_bucket = sess_root.setdefault(f"{user_id}:{workspace_id}", {})
        if isinstance(sess_bucket, dict):
            sess_bucket[cleaned_key] = dict(entry)
    return entry


def clear_today_state_for_tests() -> None:
    """Reset in-memory Today state between isolated unit tests."""
    _USER_START_PAGES.clear()
    _ACTOR_ITEM_STATES.clear()


# ------------------------------------------------ Today section data types ----


@dataclass
class TodaySection:
    key: str
    title: str
    status: str = "ok"  # "ok" | "unavailable" | "not_configured" | "denied"
    reason: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    hidden_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "reason": self.reason,
            "items": list(self.items),
            "count": len(self.items),
            "hidden_count": self.hidden_count,
        }


class TodayRepository:
    """Default ORM repository with strict tenant predicates and bounded queries."""

    async def brief(self, workspace_id: int, user_id: int, scope: str) -> dict[str, Any] | None:
        # P20-T02 brief generator is not yet wired; return None so the slot renders honestly.
        return None

    async def pending_actions(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models import AgentAction
        from app.types import BotActionStatus

        return (
            await AgentAction.objects.filter(
                tenant_id=workspace_id,
                status__in=[BotActionStatus.PENDING, BotActionStatus.APPROVED],
            )
            .order_by(AgentAction.id.desc())
            .limit(limit * 2)
        )

    async def pending_commands(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models import BusinessCommand

        return (
            await BusinessCommand.objects.filter(
                tenant_id=workspace_id,
                state__in=["previewed", "approved", "unknown"],
            )
            .order_by(BusinessCommand.id.desc())
            .limit(limit * 2)
        )

    async def command_decisions(
        self,
        workspace_id: int,
        user_id: int,
        perms: Any,
        scope: str,
        now: datetime,
    ) -> list[dict[str, Any]]:
        """Return the shared authorized inbox projection used by Today."""
        from app.core.tenant_context import tenant_scope
        from app.services.commands.approval_inbox import approval_inbox

        with tenant_scope(workspace_id):
            return await approval_inbox.project_for_today(
                perms=perms,
                scope=scope,
                actor_id=user_id,
                now=now,
            )

    async def pending_outbox(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models import OutboxMessage

        return (
            await OutboxMessage.objects.filter(
                tenant_id=workspace_id,
                state__in=["draft", "validated", "uncertain_delivery", "failed"],
            )
            .order_by(OutboxMessage.id.desc())
            .limit(limit * 2)
        )

    async def planner_tasks(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models import PlannerTask

        return (
            await PlannerTask.objects.filter(
                tenant_id=workspace_id,
                status__in=["todo", "in_progress"],
            )
            .order_by(PlannerTask.id.desc())
            .limit(limit * 3)
        )

    async def planner_schedule(self, task_id: int):
        from app.services.planner.tasks import planner_service

        return await planner_service.get(task_id)

    async def followups(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models import KnowledgeSnippet

        return (
            await KnowledgeSnippet.objects.filter(
                tenant_id=workspace_id,
                context_type="followup",
            )
            .order_by(KnowledgeSnippet.id.desc())
            .limit(limit * 2)
        )

    async def upcoming_agent_tasks(
        self, workspace_id: int, show_sources: bool = False, limit: int = 3
    ) -> list[dict[str, Any]]:
        from app.models.managers.agent_task_manager import tasks as task_manager

        return await task_manager.dashboard_upcoming(workspace_id, limit=limit, include_sources=show_sources)

    async def unread_threads(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models import MessageThread

        return (
            await MessageThread.objects.filter(
                tenant_id=workspace_id,
                is_archived=False,
            )
            .order_by(MessageThread.id.desc())
            .limit(limit * 3)
        )

    async def personal_connections(self, user_id: int) -> list[Any]:
        from app.services.social.connections import statuses_needing_action

        return await statuses_needing_action(user_id)

    async def workspace_connections(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models import WorkspaceConnection

        return (
            await WorkspaceConnection.objects.filter(
                tenant_id=workspace_id,
                is_active=True,
            )
            .order_by(WorkspaceConnection.id.desc())
            .limit(limit * 2)
        )

    async def digest_runs(self, workspace_id: int, limit: int = PROVIDER_LIMIT) -> list[Any]:
        from app.models.notifications.digest_run import DigestRun

        return (
            await DigestRun.objects.filter(tenant_id=workspace_id)
            .order_by(DigestRun.created_at.desc(), DigestRun.id.desc())
            .limit(limit * 2)
        )

    async def budget_status(self, workspace_id: int) -> dict[str, Any] | None:
        from app.models import LLMBudgetDay, Tenant

        tenant = await Tenant.objects.get(id=workspace_id)
        if tenant is None or getattr(tenant, "id", None) != workspace_id:
            return None
        limits = tenant.effective_limits() if hasattr(tenant, "effective_limits") else {}
        ceiling = limits.get("daily_cost_limit", getattr(tenant, "daily_cost_limit", None))
        today_utc = _utc_now().date()
        budget_day = await LLMBudgetDay.objects.filter(tenant_id=workspace_id, utc_day=today_utc).first()
        settled = float(getattr(budget_day, "settled_usd", 0.0) or 0.0) if budget_day else 0.0
        reserved = float(getattr(budget_day, "reserved_usd", 0.0) or 0.0) if budget_day else 0.0
        frozen = bool(getattr(budget_day, "frozen", False)) if budget_day else False
        return {
            "tenant_id": workspace_id,
            "daily_cost_limit": float(ceiling) if ceiling is not None else None,
            "spent_usd": settled + reserved,
            "frozen": frozen,
        }


class TodayProjection:
    """Read-time «Сегодня» aggregator with isolated, bounded, permission-checked providers."""

    def __init__(
        self,
        *,
        workspace_id: int | None,
        user_id: int | None,
        perms: Any,
        scope: str = "mine",
        repository: Any | None = None,
        actor_states: dict[str, dict[str, str | None]] | None = None,
        include_dismissed: bool = False,
        now: datetime | None = None,
    ):
        self.workspace_id = workspace_id if _positive_int(workspace_id) else None
        self.user_id = user_id if _positive_int(user_id) else None
        self.perms = perms
        self.scope = normalize_scope(scope)
        self.repo = repository or TodayRepository()
        self.actor_states = actor_states or {}
        self.include_dismissed = bool(include_dismissed)
        self.now = now or _utc_now()

    def _apply_actor_state(
        self, items: list[dict[str, Any]], *, limit: int = PROVIDER_LIMIT
    ) -> tuple[list[dict[str, Any]], int]:
        visible: list[dict[str, Any]] = []
        hidden_count = 0
        for item in items:
            key = item.get("item_key") or ""
            meta = self.actor_states.get(key)
            state = meta.get("state") if isinstance(meta, dict) else None
            item["actor_state"] = state
            if state in {"dismissed", "snoozed"} and not self.include_dismissed:
                hidden_count += 1
                continue
            visible.append(item)
        return visible[:limit], hidden_count

    async def build_brief_section(self) -> TodaySection:
        section = TodaySection(key="brief", title=TODAY_SECTION_TITLES["brief"])
        if self.workspace_id is None or self.user_id is None:
            section.status = "not_configured"
            section.reason = "Выберите рабочее пространство для просмотра утренней сводки."
            return section
        try:
            brief_data = await self.repo.brief(self.workspace_id, self.user_id, self.scope)
        except Exception:
            logger.warning("Today brief provider failed", exc_info=True)
            section.status = "unavailable"
            section.reason = "Утренняя сводка временно недоступна."
            return section
        if not brief_data or not isinstance(brief_data, dict) or brief_data.get("tenant_id") != self.workspace_id:
            section.status = "not_configured"
            section.reason = "Утренняя сводка появится после настройки расписания брифа."
            return section
        item = {
            "item_key": f"brief:{self.workspace_id}:{brief_data.get('id', 'today')}",
            "kind": "brief",
            "title": str(brief_data.get("title") or "Утренняя сводка"),
            "summary": str(brief_data.get("summary") or "")[:240],
            "href": str(brief_data.get("href") or "/app/digests"),
            "chat_url": "/app/chat",
            "created_at": _iso_or_empty(brief_data.get("created_at")),
            "stale_or_unavailable": bool(brief_data.get("stale_or_unavailable", False)),
            "marker_label": str(brief_data.get("marker_label") or ""),
        }
        section.items, section.hidden_count = self._apply_actor_state([item])
        return section

    async def build_decisions_section(self) -> TodaySection:
        section = TodaySection(key="decisions", title=TODAY_SECTION_TITLES["decisions"])
        if self.workspace_id is None or self.user_id is None:
            return section
        can_actions = _can(self.perms, "botaction", "view") or _can(self.perms, "scheduling.action", "view")
        can_comms = _can(self.perms, "comms.message", "view") or _can(self.perms, "outboxmessage", "view")
        if not can_actions and not can_comms:
            section.status = "denied"
            section.reason = "Для вашей роли просмотр ожидающих решений не разрешён."
            return section

        items: list[dict[str, Any]] = []
        errors: list[str] = []

        if can_actions:
            try:
                action_items: list[dict[str, Any]] = []
                for row in await self.repo.pending_actions(self.workspace_id, limit=PROVIDER_LIMIT):
                    if getattr(row, "tenant_id", None) != self.workspace_id or not _positive_int(
                        getattr(row, "id", None)
                    ):
                        continue
                    approved_uid = getattr(row, "approved_user_id", None)
                    if self.scope == "mine" and approved_uid is not None and approved_uid != self.user_id:
                        continue
                    status_name = str(
                        getattr(getattr(row, "status", None), "name", None) or getattr(row, "status", "")
                    ).upper()
                    if status_name not in {"PENDING", "APPROVED"}:
                        continue
                    cap = str(getattr(row, "capability", None) or "social.legacy")
                    action_items.append(
                        {
                            "item_key": f"decision:action:{self.workspace_id}:{row.id}",
                            "kind": "action",
                            "id": row.id,
                            "title": f"Действие #{row.id} ({cap})",
                            "summary": (
                                "Ожидает подтверждения перед выполнением"
                                if status_name == "PENDING"
                                else "Подтверждено, ожидает запуска"
                            ),
                            "status_label": "Ждёт подтверждения" if status_name == "PENDING" else "Готово к запуску",
                            "href": f"/app/actions/{row.id}",
                            "chat_url": "/app/chat?" + urlencode({"action_id": row.id}),
                            "created_at": _iso_or_empty(getattr(row, "created_at", None)),
                            "stale_or_unavailable": False,
                            "marker_label": "",
                        }
                    )
                    if len(action_items) >= PROVIDER_LIMIT:
                        break
                items.extend(action_items)
            except Exception:
                errors.append("actions")
                logger.warning("Today pending_actions provider failed", exc_info=True)

            try:
                command_items: list[dict[str, Any]] = []
                # F6: repository injection supplies the same authorized projection
                # as the inbox. There is no raw ledger fallback in Today.
                today_decisions = await self.repo.command_decisions(
                    self.workspace_id,
                    self.user_id,
                    self.perms,
                    self.scope,
                    self.now,
                )
                for decision in today_decisions:
                    if decision.get("tenant_id") != self.workspace_id:
                        continue
                    if self.scope == "mine" and decision.get("requester_user_id") != self.user_id:
                        continue
                    command_items.append(
                        {
                            "item_key": f"decision:command:{self.workspace_id}:{decision['id']}",
                            "kind": "command",
                            "id": decision["id"],
                            "title": decision["title"],
                            "summary": decision["summary"],
                            "status_label": decision["status_label"],
                            "href": "/app/commands",
                            "authoritative_url": decision["authoritative_url"],
                            "chat_url": "/app/chat",
                            "created_at": decision["freshness"]["updated_at"],
                            "stale_or_unavailable": decision["status"] == "unknown",
                            "marker_label": "Статус не подтверждён" if decision["status"] == "unknown" else "",
                            "payload_hash": decision["payload_hash"],
                            "redacted": False,
                        }
                    )
                    if len(command_items) >= PROVIDER_LIMIT:
                        break
                items.extend(command_items)
            except Exception:
                errors.append("commands")
                logger.warning("Today pending_commands provider failed", exc_info=True)

        if can_comms:
            try:
                outbox_items: list[dict[str, Any]] = []
                for out in await self.repo.pending_outbox(self.workspace_id, limit=PROVIDER_LIMIT):
                    if getattr(out, "tenant_id", None) != self.workspace_id or not _positive_int(
                        getattr(out, "id", None)
                    ):
                        continue
                    author_uid = getattr(out, "author_id", None)
                    if self.scope == "mine" and author_uid is not None and author_uid != self.user_id:
                        continue
                    state = str(getattr(out, "state", "")).lower()
                    if state not in {"draft", "validated"}:
                        continue
                    subj = str(getattr(out, "subject_encrypted", None) or f"Исходящее сообщение #{out.id}")[:120]
                    outbox_items.append(
                        {
                            "item_key": f"decision:outbox:{self.workspace_id}:{out.id}",
                            "kind": "outbox",
                            "id": out.id,
                            "title": subj,
                            "summary": "Подготовлено к отправке — ожидает проверки и одобрения",
                            "status_label": "Черновик исходящего",
                            "href": "/app/communications?tab=outbox",
                            "chat_url": "/app/chat",
                            "created_at": _iso_or_empty(getattr(out, "created_at", None)),
                            "stale_or_unavailable": False,
                            "marker_label": "",
                        }
                    )
                    if len(outbox_items) >= PROVIDER_LIMIT:
                        break
                items.extend(outbox_items)
            except Exception:
                errors.append("outbox")
                logger.warning("Today pending_outbox provider failed", exc_info=True)

        section.items, section.hidden_count = self._apply_actor_state(items, limit=DECISIONS_LIMIT)
        if errors and not section.items:
            section.status = "unavailable"
            section.reason = "Не удалось загрузить ожидающие решения. Повторите попытку позже."
        elif errors:
            section.reason = "Часть источников решений временно недоступна."
        return section

    async def build_today_plan_section(self) -> TodaySection:
        section = TodaySection(key="today_plan", title=TODAY_SECTION_TITLES["today_plan"])
        if self.workspace_id is None or self.user_id is None:
            return section
        can_planner = _can(self.perms, "plannertask", "view") or _can(self.perms, "planner.task", "view")
        can_tasks = _can(self.perms, "agenttask", "view") or _can(self.perms, "scheduling.task", "view")
        if not can_planner and not can_tasks:
            section.status = "denied"
            section.reason = "Для вашей роли просмотр плана и запусков не разрешён."
            return section

        items: list[dict[str, Any]] = []
        errors: list[str] = []
        today_date = self.now.date()
        is_mgr = _is_manager_role(self.perms)

        if can_planner:
            try:
                for ptask in await self.repo.planner_tasks(self.workspace_id, limit=PROVIDER_LIMIT):
                    if getattr(ptask, "tenant_id", None) != self.workspace_id or not _positive_int(
                        getattr(ptask, "id", None)
                    ):
                        continue
                    owner_id = getattr(ptask, "owner_user_id", None)
                    assignee_id = getattr(ptask, "assignee_id", None)
                    is_own = self.user_id in {owner_id, assignee_id}
                    if not is_own and (self.scope == "mine" or not is_mgr):
                        continue
                    status = str(getattr(ptask, "status", "todo")).lower()
                    if status not in {"todo", "in_progress"}:
                        continue
                    source_unavail = False
                    schedule = ptask
                    if getattr(ptask, "recurrence_source", "planner") != "planner":
                        try:
                            schedule_reader = getattr(self.repo, "planner_schedule", None)
                            schedule = await schedule_reader(ptask.id) if schedule_reader is not None else None
                        except Exception:
                            schedule = None
                        source_unavail = schedule is None
                    due_d = getattr(schedule, "due_date", None)
                    due_at = getattr(schedule, "due_at", None)
                    due_day = due_d or (due_at.date() if isinstance(due_at, datetime) else None)
                    if due_day is not None and due_day > today_date:
                        continue
                    is_overdue = due_day is not None and due_day < today_date
                    title = str(
                        getattr(ptask, "title_encrypted", None)
                        or getattr(ptask, "title", None)
                        or f"Задача #{ptask.id}"
                    )[:140]
                    items.append(
                        {
                            "item_key": f"plan:planner:{self.workspace_id}:{ptask.id}",
                            "kind": "planner_task",
                            "id": ptask.id,
                            "title": title,
                            "summary": (
                                f"Просрочено ({due_day.isoformat()})"
                                if is_overdue
                                else ("Срок сегодня" if due_day == today_date else "В плане к выполнению")
                            ),
                            "status_label": (
                                "Источник недоступен"
                                if source_unavail
                                else ("Просрочено" if is_overdue else "На сегодня")
                            ),
                            "href": f"/app/planner/{ptask.id}/edit",
                            "chat_url": "/app/chat",
                            "created_at": _iso_or_empty(due_at or due_d),
                            "stale_or_unavailable": source_unavail,
                            "marker_label": "Внешний источник повторения недоступен" if source_unavail else "",
                        }
                    )
            except Exception:
                errors.append("planner")
                logger.warning("Today planner_tasks provider failed", exc_info=True)

        if can_tasks:
            try:
                for fup in await self.repo.followups(self.workspace_id, limit=PROVIDER_LIMIT):
                    if getattr(fup, "tenant_id", None) != self.workspace_id or not _positive_int(
                        getattr(fup, "id", None)
                    ):
                        continue
                    boundary = str(getattr(fup, "data_boundary", None) or "cloud")
                    title = str(getattr(fup, "title", None) or f"Напоминание #{fup.id}")[:140]
                    note = str(getattr(fup, "extraction_limit_note", None) or "")
                    items.append(
                        {
                            "item_key": f"plan:followup:{self.workspace_id}:{fup.id}",
                            "kind": "followup",
                            "id": fup.id,
                            "title": title,
                            "summary": str(getattr(fup, "excerpt", None) or "Контроль договорённости")[:180],
                            "status_label": "Договорённость",
                            "href": "/app/followups",
                            "chat_url": "/app/chat?"
                            + urlencode(
                                {
                                    "context_type": "followup",
                                    "context_id": str(fup.id),
                                    "boundary": boundary,
                                }
                            ),
                            "created_at": _iso_or_empty(getattr(fup, "created_at", None)),
                            "stale_or_unavailable": bool(note),
                            "marker_label": note[:100] if note else "",
                        }
                    )
            except Exception:
                errors.append("followups")
                logger.warning("Today followups provider failed", exc_info=True)

            try:
                show_sources = _can(self.perms, "source", "view") or _can(self.perms, "collection.source", "view")
                for atask in await self.repo.upcoming_agent_tasks(
                    self.workspace_id, show_sources=show_sources, limit=3
                ):
                    tid = atask.get("id") if isinstance(atask, dict) else getattr(atask, "id", None)
                    if not _positive_int(tid):
                        continue
                    t_tenant = (
                        atask.get("tenant_id", self.workspace_id)
                        if isinstance(atask, dict)
                        else getattr(atask, "tenant_id", self.workspace_id)
                    )
                    if t_tenant != self.workspace_id:
                        continue
                    name = str(
                        (atask.get("name") if isinstance(atask, dict) else getattr(atask, "name", ""))
                        or f"Запуск #{tid}"
                    )[:120]
                    is_due = bool(atask.get("is_due") if isinstance(atask, dict) else getattr(atask, "is_due", False))
                    next_run = (
                        atask.get("next_run_at") if isinstance(atask, dict) else getattr(atask, "next_run_at", None)
                    )
                    items.append(
                        {
                            "item_key": f"plan:agenttask:{self.workspace_id}:{tid}",
                            "kind": "agent_task",
                            "id": tid,
                            "title": name,
                            "summary": "Ожидает запуска" if is_due else f"Следующий запуск: {_iso_or_empty(next_run)}",
                            "status_label": "Ожидает запуска" if is_due else "По расписанию",
                            "href": f"/app/tasks/{tid}",
                            "chat_url": "/app/chat?"
                            + urlencode({"context_type": "task", "context_id": str(tid), "boundary": "cloud"}),
                            "created_at": _iso_or_empty(next_run),
                            "stale_or_unavailable": False,
                            "marker_label": "",
                        }
                    )
            except Exception:
                errors.append("agent_tasks")
                logger.warning("Today upcoming_agent_tasks provider failed", exc_info=True)

        section.items, section.hidden_count = self._apply_actor_state(items)
        if errors and not section.items:
            section.status = "unavailable"
            section.reason = "Не удалось загрузить план на сегодня."
        elif errors:
            section.reason = "Часть элементов плана временно недоступна."
        return section

    async def build_awaiting_reply_section(self) -> TodaySection:
        section = TodaySection(key="awaiting_reply", title=TODAY_SECTION_TITLES["awaiting_reply"])
        if self.workspace_id is None or self.user_id is None:
            return section
        can_comms = (
            _can(self.perms, "comms.message", "view")
            or _can(self.perms, "unifiedmessage", "view")
            or _can(self.perms, "botaction", "view")
            or _can(self.perms, "scheduling.action", "view")
        )
        if not can_comms:
            section.status = "denied"
            section.reason = "Для вашей роли просмотр входящих диалогов не разрешён."
            return section

        try:
            rows = await self.repo.unread_threads(self.workspace_id, limit=PROVIDER_LIMIT)
        except Exception:
            logger.warning("Today unread_threads provider failed", exc_info=True)
            section.status = "unavailable"
            section.reason = "Входящие диалоги временно недоступны."
            return section

        from app.services.communications.triage import InboxItemUnavailable, triage_thread_record

        can_view_contact = _can(self.perms, "contact", "view")
        items: list[dict[str, Any]] = []
        for th in rows:
            if getattr(th, "tenant_id", None) != self.workspace_id or not _positive_int(getattr(th, "id", None)):
                continue
            if bool(getattr(th, "is_archived", False)):
                continue
            unread = int(getattr(th, "unread_count", 0) or 0)
            if unread <= 0:
                continue
            try:
                triaged = triage_thread_record(
                    th,
                    tenant_id=self.workspace_id,
                    user_id=self.user_id,
                    can_view_contact=can_view_contact,
                )
            except InboxItemUnavailable:
                continue
            channel = str(getattr(th, "channel", "email") or "email")
            subject = str(getattr(th, "subject_encrypted", None) or getattr(th, "subject", None) or f"Диалог #{th.id}")[
                :120
            ]
            unavail = bool(getattr(th, "is_unavailable", False) or getattr(th, "unavailable_at", None) is not None)
            items.append(
                {
                    "item_key": f"reply:thread:{self.workspace_id}:{th.id}",
                    "provider": "communications.inbox_triage",
                    "kind": "thread",
                    "id": th.id,
                    "channel": channel,
                    "title": subject,
                    "summary": f"Канал: {channel.upper()} · Непрочитанных: {unread} · {triaged.category_label}: {triaged.reason}",
                    "status_label": f"{unread} непрочит.",
                    "triage_category": triaged.category,
                    "triage_label": triaged.category_label,
                    "triage_reason": triaged.reason,
                    "triage_basis": triaged.basis,
                    "is_model_fact": triaged.is_model_fact,
                    "recommended_priority": triaged.recommended_priority,
                    "priority_basis": triaged.priority_basis,
                    "data_boundary": triaged.data_boundary,
                    "authoritative_ref": triaged.authoritative_ref,
                    "proposals": [p.to_dict() for p in triaged.proposals],
                    "href": f"/app/communications?thread_id={th.id}",
                    "chat_url": "/app/chat?"
                    + urlencode(
                        {
                            "context_type": "communication",
                            "context_id": str(th.id),
                            "boundary": "cloud",
                        }
                    ),
                    "created_at": _iso_or_empty(getattr(th, "last_message_at", None)),
                    "stale_or_unavailable": unavail,
                    "marker_label": "Источник недоступен (только метаданные)" if unavail else "",
                }
            )
        section.items, section.hidden_count = self._apply_actor_state(items)
        return section

    async def build_attention_section(self) -> TodaySection:
        section = TodaySection(key="attention", title=TODAY_SECTION_TITLES["attention"])
        if self.workspace_id is None or self.user_id is None:
            return section

        items: list[dict[str, Any]] = []
        errors: list[str] = []

        # 1. Personal OAuth connections needing reauthorization (strictly scoped to current user_id)
        try:
            platform_labels = {"vk": "ВКонтакте", "telegram": "Telegram"}
            for status in await self.repo.personal_connections(self.user_id):
                plat = getattr(status, "platform", None)
                state = getattr(status, "state", None)
                if (
                    plat not in platform_labels
                    or state not in {"reauth", "missing"}
                    or not getattr(status, "needs_action", False)
                    or not getattr(status, "can_connect", False)
                ):
                    continue
                action_hint = "требуется повторный вход" if state == "reauth" else "подключите доступ"
                items.append(
                    {
                        "item_key": f"attention:personal_conn:{self.workspace_id}:{self.user_id}:{plat}:{state}",
                        "kind": "personal_connection",
                        "title": f"{platform_labels[plat]}: {action_hint}",
                        "summary": "Личное подключение требует переавторизации.",
                        "status_label": "Переавторизация" if state == "reauth" else "Не подключено",
                        "href": "/app/settings?tab=connections",
                        "chat_url": "/app/chat",
                        "created_at": "",
                        "stale_or_unavailable": True,
                        "marker_label": "Требуется повторный вход",
                    }
                )
        except Exception:
            errors.append("personal_connections")
            logger.warning("Today personal_connections provider failed", exc_info=True)

        # 2. Workspace connections needing attention (if permitted)
        can_ws_conn = (
            _is_manager_role(self.perms)
            or _can(self.perms, "source", "view")
            or _can(self.perms, "collection.source", "view")
        )
        if can_ws_conn:
            try:
                for conn in await self.repo.workspace_connections(self.workspace_id, limit=PROVIDER_LIMIT):
                    if getattr(conn, "tenant_id", None) != self.workspace_id or not _positive_int(
                        getattr(conn, "id", None)
                    ):
                        continue
                    c_state = str(getattr(conn, "state", "verified") or "verified").lower()
                    err_code = getattr(conn, "last_error_code", None)
                    if c_state == "verified" and not err_code:
                        continue
                    label = str(
                        getattr(conn, "display_name", None)
                        or getattr(conn, "provider_code", None)
                        or f"Подключение #{conn.id}"
                    )[:100]
                    items.append(
                        {
                            "item_key": f"attention:ws_conn:{self.workspace_id}:{conn.id}",
                            "kind": "workspace_connection",
                            "id": conn.id,
                            "title": f"Подключение «{label}» требует проверки",
                            "summary": f"Состояние: {c_state}" + (f" ({err_code})" if err_code else ""),
                            "status_label": "Сбой подключения",
                            "href": "/app/settings?tab=connections",
                            "chat_url": "/app/chat",
                            "created_at": _iso_or_empty(getattr(conn, "updated_at", None)),
                            "stale_or_unavailable": True,
                            "marker_label": "Интеграция недоступна",
                        }
                    )
            except Exception:
                errors.append("workspace_connections")
                logger.warning("Today workspace_connections provider failed", exc_info=True)

        # 3. Failed or uncertain digest / outbox deliveries
        can_digests = _can(self.perms, "digestrun", "view") or _can(self.perms, "notifications.digest_run", "view")
        if can_digests:
            try:
                from app.services.digest.checkpoints import validate_checkpoint
                from app.services.digest.delivery_outcomes import summarize_delivery

                for run in await self.repo.digest_runs(self.workspace_id, limit=PROVIDER_LIMIT):
                    if getattr(run, "tenant_id", None) != self.workspace_id or not _positive_int(
                        getattr(run, "id", None)
                    ):
                        continue
                    deliv_state = getattr(run, "delivery_state", None)
                    if isinstance(deliv_state, dict):
                        try:
                            checkpoint = validate_checkpoint(deliv_state)
                            if checkpoint["run_id"] != run.id or checkpoint["tenant_id"] != self.workspace_id:
                                d_status = "unknown"
                            else:
                                d_status = summarize_delivery(checkpoint)["status"]
                        except (KeyError, TypeError, ValueError):
                            d_status = "unknown"
                    else:
                        raw_st = str(getattr(run, "status", "")).lower()
                        d_status = raw_st if raw_st in {"failed", "partial", "unknown"} else "ok"
                    if d_status not in {"failed", "partial", "unknown"}:
                        continue
                    items.append(
                        {
                            "item_key": f"attention:digest_delivery:{self.workspace_id}:{run.id}",
                            "kind": "digest_delivery",
                            "id": run.id,
                            "title": f"Доставка дайджеста #{run.id}",
                            "summary": (
                                "Статус доставки не подтверждён"
                                if d_status == "unknown"
                                else ("Доставлен частично" if d_status == "partial" else "Ошибка доставки дайджеста")
                            ),
                            "status_label": "Не подтверждено" if d_status == "unknown" else "Сбой доставки",
                            "href": "/app/digests#delivery",
                            "chat_url": "/app/chat",
                            "created_at": _iso_or_empty(getattr(run, "created_at", None)),
                            "stale_or_unavailable": True,
                            "marker_label": "Требует проверки доставки",
                        }
                    )
            except Exception:
                errors.append("digest_deliveries")
                logger.warning("Today digest_runs delivery provider failed", exc_info=True)

        can_comms = _can(self.perms, "comms.message", "view") or _can(self.perms, "outboxmessage", "view")
        if can_comms:
            try:
                for out in await self.repo.pending_outbox(self.workspace_id, limit=PROVIDER_LIMIT):
                    if getattr(out, "tenant_id", None) != self.workspace_id or not _positive_int(
                        getattr(out, "id", None)
                    ):
                        continue
                    author_uid = getattr(out, "author_id", None)
                    if self.scope == "mine" and author_uid is not None and author_uid != self.user_id:
                        continue
                    state = str(getattr(out, "state", "")).lower()
                    if state not in {"failed", "uncertain_delivery"}:
                        continue
                    subj = str(getattr(out, "subject_encrypted", None) or f"Исходящее #{out.id}")[:120]
                    items.append(
                        {
                            "item_key": f"attention:outbox_delivery:{self.workspace_id}:{out.id}",
                            "kind": "outbox_delivery",
                            "id": out.id,
                            "title": f"Исходящее: {subj}",
                            "summary": (
                                "Статус отправки не подтверждён — проверьте перед повтором"
                                if state == "uncertain_delivery"
                                else "Отправка завершилась с ошибкой"
                            ),
                            "status_label": "Не подтверждено" if state == "uncertain_delivery" else "Ошибка отправки",
                            "href": "/app/communications?tab=outbox",
                            "chat_url": "/app/chat",
                            "created_at": _iso_or_empty(
                                getattr(out, "updated_at", None) or getattr(out, "created_at", None)
                            ),
                            "stale_or_unavailable": True,
                            "marker_label": (
                                "Неопределённый статус отправки" if state == "uncertain_delivery" else "Сбой отправки"
                            ),
                        }
                    )
            except Exception:
                errors.append("outbox_deliveries")
                logger.warning("Today outbox delivery provider failed", exc_info=True)

        # 4. Budget limit alerts
        if _is_manager_role(self.perms):
            try:
                b_status = await self.repo.budget_status(self.workspace_id)
                if isinstance(b_status, dict) and b_status.get("tenant_id") == self.workspace_id:
                    limit_usd = b_status.get("daily_cost_limit")
                    spent_usd = float(b_status.get("spent_usd") or 0.0)
                    if isinstance(limit_usd, (int, float)) and limit_usd > 0 and spent_usd >= limit_usd * 0.8:
                        reached = spent_usd >= limit_usd
                        items.append(
                            {
                                "item_key": f"attention:budget:{self.workspace_id}:{self.now.date().isoformat()}",
                                "kind": "budget",
                                "title": (
                                    "Дневной лимит LLM исчерпан"
                                    if reached
                                    else "Расход LLM приблизился к дневному лимиту"
                                ),
                                "summary": f"Использовано ${spent_usd:.2f} из ${limit_usd:.2f}",
                                "status_label": "Лимит исчерпан" if reached else "Почти исчерпан",
                                "href": "/app/settings?tab=llm",
                                "chat_url": "/app/chat",
                                "created_at": self.now.date().isoformat(),
                                "stale_or_unavailable": reached,
                                "marker_label": "Лимит бюджета",
                            }
                        )
            except Exception:
                errors.append("budget")
                logger.warning("Today budget provider failed", exc_info=True)

        section.items, section.hidden_count = self._apply_actor_state(items)
        if errors and not section.items:
            section.status = "unavailable"
            section.reason = "Сигналы внимания временно недоступны."
        elif errors:
            section.reason = "Часть сигналов внимания временно недоступна."
        return section

    async def build_recent_results_section(self, merged_results: list[dict[str, Any]] | None = None) -> TodaySection:
        section = TodaySection(key="recent_results", title=TODAY_SECTION_TITLES["recent_results"])
        can_analyses = _can(self.perms, "aianalytics", "view") or _can(self.perms, "analysis.result", "view")
        can_digests = _can(self.perms, "digestrun", "view") or _can(self.perms, "notifications.digest_run", "view")
        if not can_analyses and not can_digests:
            section.status = "denied"
            section.reason = "Для вашей роли просмотр результатов не разрешён."
            return section
        raw_items: list[dict[str, Any]] = []
        for idx, res in enumerate((merged_results or [])[:PROVIDER_LIMIT]):
            if not isinstance(res, dict):
                continue
            rid = res.get("id", idx)
            kind = str(res.get("kind") or "analysis")
            incomplete = bool(res.get("analysis_incomplete", False))
            deliv = res.get("delivery_status")
            raw_items.append(
                {
                    "item_key": f"result:{kind}:{self.workspace_id or 0}:{rid}",
                    "kind": kind,
                    "id": rid,
                    "title": str(res.get("title") or "Результат"),
                    "summary": str(res.get("summary") or ""),
                    "href": str(res.get("href") or ("/app/digests" if kind == "digest" else f"/app/analytics/{rid}")),
                    "chat_url": (
                        "/app/chat?" + urlencode({"result_id": str(rid)}) if kind == "analysis" else "/app/chat"
                    ),
                    "created_at": _iso_or_empty(res.get("created_at")),
                    "source_name": res.get("source_name"),
                    "period_label": res.get("period_label"),
                    "delivery_status": deliv,
                    "analysis_incomplete": incomplete,
                    "stale_or_unavailable": incomplete
                    or deliv in {"Доставлен частично", "Не доставлен", "Статус доставки не подтверждён"},
                    "marker_label": "Неполный охват" if incomplete else (str(deliv) if deliv else ""),
                }
            )
        section.items, section.hidden_count = self._apply_actor_state(raw_items)
        return section

    async def snapshot(self, *, merged_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Compute all 6 Today sections in canonical order with per-provider failure isolation."""
        brief = await self.build_brief_section()
        decisions = await self.build_decisions_section()
        today_plan = await self.build_today_plan_section()
        awaiting_reply = await self.build_awaiting_reply_section()
        attention = await self.build_attention_section()
        recent_results = await self.build_recent_results_section(merged_results=merged_results)

        ordered = [brief, decisions, today_plan, awaiting_reply, attention, recent_results]
        sections_by_key = {sec.key: sec.to_dict() for sec in ordered}
        return {
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "scope": self.scope,
            "include_dismissed": self.include_dismissed,
            "section_order": list(TODAY_SECTION_ORDER),
            "sections": [sec.to_dict() for sec in ordered],
            "by_key": sections_by_key,
        }
