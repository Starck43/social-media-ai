"""Server-resolved task conversation scope for the shared web chat.

Everything the browser sends about the selected conversation is a selector only.
This module answers three questions for the caller, in the current workspace and
for the authenticated actor:

- may this actor use AI chat at all (`ai_chat_available`);
- does a task have an explicitly configured, usable agent scenario, and which
  step of its published plan supplies it (`open_task_chat`);
- which agent session renders it (`conversation`, allocated on the first send).

The binding itself lives in `TaskChatBinding`; the transcript stays in the
existing agent session and message tables.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_catalog import workspace_grants
from app.core.database import with_db_session
from app.core.permissions import get_current_user
from app.core.pii import PIIError, decrypt_pii
from app.core.tenant_context import current_tenant_id
from app.models import AgentScenario, Task, TaskPlanRevision, TenantUser
from app.services.execution_plan_contracts import ExecutionPlanError, parse_execution_plan
from app.services.planner.canonical_tasks import task_service
from app.services.planner.tasks import PlannerUnavailable
from app.types import ActionType

WEB_CHANNEL = "web"
TASK_CHAT_PREFIX = "task:"
MAIN_TITLE = "Основной чат"

DENIED_REASONS = frozenset(
    {
        "ai_chat_denied",
        "task_unavailable",
        "no_scenario_binding",
        "scenario_unavailable",
        "conversation_unavailable",
    }
)


class TaskChatDenied(PermissionError):
    """Fail-closed refusal with a stable reason code for the UI."""

    def __init__(self, reason: str = "task_unavailable") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TaskChatAction:
    """What a task card may offer, and what the drawer will talk about."""

    available: bool
    task_id: int
    title: str = ""
    scenario_id: int | None = None
    scenario_name: str = ""
    step: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ChatScope:
    """One authorized conversation, already resolved on the server."""

    kind: str  # "main" | "task"
    title: str
    session_id: int | None
    task_id: int | None = None
    scenario_id: int | None = None
    scenario_name: str = ""
    step: str = ""

    @property
    def is_task(self) -> bool:
        return self.kind == "task"


def task_chat_actor_id(user_id: int, task_id: int) -> str:
    """Session chat id owned by one actor, independent of the request selector.

    The task id alone is not enough: the session key is workspace-scoped, so two
    members of the same workspace would otherwise share one transcript.
    """
    return f"{TASK_CHAT_PREFIX}{task_id}:{user_id}"


def _scenario_grants(role: str | None) -> bool:
    return ("agentscenario", ActionType.VIEW) in workspace_grants(role)


def _latest_definition(stored: TaskPlanRevision):
    """Decode one immutable revision; a broken snapshot is not usable."""
    definition = json.loads(decrypt_pii(stored.definition_encrypted))
    plan = parse_execution_plan(json.dumps(definition["plan"], ensure_ascii=False, separators=(",", ":")))
    return plan, definition.get("methodology") or {}


async def _ai_chat_available(session: AsyncSession) -> bool:
    """Effective workspace AI access: owner, or an enabled active member."""
    tenant_id = current_tenant_id()
    actor = get_current_user()
    if type(tenant_id) is not int or tenant_id <= 0 or actor is None or not actor.is_active:
        return False
    member = await session.scalar(
        select(TenantUser).where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.user_id == actor.id,
            TenantUser.is_active.is_(True),
        )
    )
    return bool(member is not None and (member.is_owner or member.ai_chat_enabled))


async def _open_task_chat(session: AsyncSession, task_id: int) -> TaskChatAction:
    """The `open_task_chat` action: only a configured, usable binding qualifies.

    A catalog scenario, the workspace default or another task's binding never
    qualifies. With several agent steps the plan's last LLM step (the one that
    produces the artifact) is the resolved step; the choice is explicit and
    stable for a given revision.
    """
    actor = get_current_user()
    title = ""
    try:
        if actor is None or not await _ai_chat_available(session):
            return TaskChatAction(False, task_id, reason="ai_chat_denied")
        identity = await task_service._actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity, lock=False)
        if task.deleted_at is not None:
            return TaskChatAction(False, task_id, reason="task_unavailable")
        title = str(task.title_encrypted or "")
        stored = await session.scalar(
            select(TaskPlanRevision)
            .where(TaskPlanRevision.tenant_id == identity[0], TaskPlanRevision.task_id == task.id)
            .order_by(TaskPlanRevision.revision.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        if stored is None:
            return TaskChatAction(False, task_id, title, reason="no_scenario_binding")
        plan, methodology = _latest_definition(stored)
        steps = [step for step in plan.steps if step.scenario_id is not None]
        if not steps:
            return TaskChatAction(False, task_id, title, reason="no_scenario_binding")
        step = steps[-1]
        snapshots = methodology.get("scenarios") or {}
        if str(step.scenario_id) not in snapshots:
            return TaskChatAction(False, task_id, title, reason="no_scenario_binding")
        if not _scenario_grants(identity[2]):
            return TaskChatAction(False, task_id, title, reason="scenario_unavailable")
        scenario = await session.scalar(
            select(AgentScenario).where(
                AgentScenario.tenant_id == identity[0],
                AgentScenario.id == step.scenario_id,
                AgentScenario.is_active.is_(True),
            )
        )
        if scenario is None:
            return TaskChatAction(False, task_id, title, reason="scenario_unavailable")
        return TaskChatAction(True, task.id, title, scenario.id, str(scenario.name or ""), step.name)
    except (PermissionError, PlannerUnavailable, ExecutionPlanError, PIIError, ValueError, KeyError, TypeError):
        # A missing right and a broken revision get one answer: not offered.
        return TaskChatAction(False, task_id, title, reason="task_unavailable")


def _task_scope(action: TaskChatAction, session_id: int | None) -> ChatScope:
    return ChatScope(
        "task",
        action.title,
        session_id,
        task_id=action.task_id,
        scenario_id=action.scenario_id,
        scenario_name=action.scenario_name,
        step=action.step,
    )


async def _main_session_id(session: AsyncSession) -> int | None:
    """The existing global session for this actor; reading never creates it."""
    from app.models.agent.agent_session import AgentSession

    actor = get_current_user()
    if actor is None:
        return None
    return await session.scalar(
        select(AgentSession.id).where(
            AgentSession.channel == WEB_CHANNEL,
            AgentSession.chat_id == str(actor.id),
            AgentSession.tenant_id == current_tenant_id(),
        )
    )


class TaskChatService:
    """Read-time eligibility and first-send allocation for task conversations."""

    @with_db_session
    async def ai_chat_available(self, *, session: AsyncSession | None = None) -> bool:
        return await _ai_chat_available(session)

    @with_db_session
    async def open_task_chat(self, task_id: int, *, session: AsyncSession | None = None) -> TaskChatAction:
        return await _open_task_chat(session, task_id)

    @with_db_session
    async def conversation(self, task_id: int | None = None, *, session: AsyncSession | None = None) -> ChatScope:
        """Resolve the selected conversation without creating any row.

        The main conversation keeps its historical session id; a task
        conversation exists only after this actor's first send allocated it.
        """
        if task_id is None:
            return ChatScope("main", MAIN_TITLE, await _main_session_id(session))
        action = await _open_task_chat(session, task_id)
        if not action.available:
            raise TaskChatDenied(action.reason)
        binding = await _binding_for(task_id)
        return _task_scope(action, binding.session_id if binding is not None else None)

    @with_db_session
    async def list_task_conversations(self, *, limit: int = 20, session: AsyncSession | None = None) -> list[dict]:
        """Canonical tasks whose saved binding makes a conversation available.

        Bounded on purpose: the picker shows the newest visible tasks, and each
        candidate is checked with the same rule the send path uses.
        """
        identity = await task_service._actor(session, ActionType.VIEW)
        rows = await session.scalars(
            select(Task)
            .where(
                Task.tenant_id == identity[0],
                Task.deleted_at.is_(None),
                task_service.visibility(identity[1], identity[2]),
            )
            .order_by(Task.id.desc())
            .limit(limit)
        )
        items: list[dict] = []
        for task in rows:
            action = await _open_task_chat(session, task.id)
            if action.available:
                items.append(
                    {
                        "task_id": action.task_id,
                        "title": action.title,
                        "scenario_name": action.scenario_name,
                        "step": action.step,
                    }
                )
        return items

    @with_db_session
    async def ensure_task_conversation(self, task_id: int, *, session: AsyncSession | None = None) -> ChatScope:
        """Allocate this actor's task conversation once, on the first send."""
        action = await _open_task_chat(session, task_id)
        if not action.available:
            raise TaskChatDenied(action.reason)
        actor = get_current_user()
        if actor is None:
            raise TaskChatDenied("ai_chat_denied")
        binding = await _binding_for(task_id)
        if binding is not None:
            return _task_scope(action, binding.session_id)
        session_id = await _allocate_session(actor.id, task_id)
        if session_id is None:
            raise TaskChatDenied("conversation_unavailable")
        return _task_scope(action, session_id)


async def _binding_for(task_id: int):
    from app.models.managers.task_chat_manager import task_chat_bindings

    actor = get_current_user()
    if actor is None:
        return None
    return await task_chat_bindings.get_for(user_id=actor.id, task_id=task_id)


async def _allocate_session(actor_id: int, task_id: int) -> int | None:
    """Allocate the deterministic task session and its binding, converging on a race.

    Both steps own their own transaction. The chat id is deterministic per
    (workspace, actor, task), so concurrent first sends resolve to the same
    session row and then to the same committed binding.
    """
    from app.models.managers.agent_session_manager import agent_sessions
    from app.models.managers.task_chat_manager import task_chat_bindings

    agent_session = await agent_sessions.get_or_create(
        channel=WEB_CHANNEL, chat_id=task_chat_actor_id(actor_id, task_id)
    )
    if agent_session is None:
        return None
    binding = await task_chat_bindings.get_or_create(user_id=actor_id, task_id=task_id, session_id=agent_session.id)
    return binding.session_id if binding is not None else None


task_chat_service = TaskChatService()

__all__ = [
    "ChatScope",
    "MAIN_TITLE",
    "TaskChatAction",
    "TaskChatDenied",
    "TaskChatService",
    "task_chat_actor_id",
    "task_chat_service",
]
