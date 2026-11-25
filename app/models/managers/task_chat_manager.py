"""Bindings between one actor, one canonical task and one agent session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..agent.task_chat import TaskChatBinding


def _is_binding_actor_conflict(error: IntegrityError) -> bool:
    """Recognize only the named actor uniqueness conflict, never its text."""
    original = error.orig
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if code != "23505":
        return False
    constraint = getattr(original, "constraint_name", None)
    if constraint is None:
        constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
    if constraint is None:
        constraint = getattr(getattr(original, "__cause__", None), "constraint_name", None)
    return constraint == "uq_task_chat_binding_actor"


class TaskChatBindingManager(BaseManager["TaskChatBinding"]):
    """One binding per (tenant, user, task); a session is bound at most once."""

    def __init__(self):
        from ..agent.task_chat import TaskChatBinding

        super().__init__(TaskChatBinding)

    async def get_for(self, *, user_id: int, task_id: int) -> TaskChatBinding | None:
        """Read an existing conversation without creating any row."""
        return await self.get(user_id=user_id, task_id=task_id)

    async def get_or_create(self, *, user_id: int, task_id: int, session_id: int) -> TaskChatBinding | None:
        """Converge concurrent first sends on the committed binding.

        `create()` owns its transaction and has already rolled it back, so the
        recovery path may only adopt a committed row that is visible in the
        current workspace. The caller allocates the session through the
        deterministic chat id, so both racers point at the same session.
        """
        binding = await self.get_for(user_id=user_id, task_id=task_id)
        if binding is not None:
            return binding
        try:
            return await self.create(user_id=user_id, task_id=task_id, session_id=session_id)
        except IntegrityError as error:
            if not _is_binding_actor_conflict(error):
                raise
            return await self.get_for(user_id=user_id, task_id=task_id)


task_chat_bindings = TaskChatBindingManager()
