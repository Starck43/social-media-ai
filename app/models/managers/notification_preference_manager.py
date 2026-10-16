"""Owned-session operations for user notification preferences."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import User
from app.services.notifications.preferences import validate

from .base_manager import BaseManager


class NotificationPreferenceUserInactive(Exception):
    """The authenticated user was disabled before the settings write committed."""


class NotificationPreferenceManager(BaseManager):
    def __init__(self):
        from ..notifications.notification_preference import NotificationPreference

        super().__init__(NotificationPreference)

    async def get_for_user(self, user_id: int) -> dict | None:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("Invalid notification preference owner")
        async with self._get_owned_session() as session:
            active_user = await session.scalar(select(User.id).where(User.id == user_id, User.is_active.is_(True)))
            if active_user is None:
                raise NotificationPreferenceUserInactive
            row = await session.get(self.model, user_id)
            return self._payload(row) if row else None

    async def save_for_user(self, user_id: int, value: dict) -> dict:
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("Invalid notification preference owner")
        value = validate(value)
        if value is None:
            raise ValueError("Invalid notification preferences")
        async with self._get_owned_session() as session:
            active_user = await session.scalar(
                select(User.id).where(User.id == user_id, User.is_active.is_(True)).with_for_update()
            )
            if active_user is None:
                raise NotificationPreferenceUserInactive
            statement = (
                pg_insert(self.model)
                .values(user_id=user_id, **value)
                .on_conflict_do_update(index_elements=[self.model.user_id], set_={**value, "updated_at": func.now()})
                .returning(self.model)
            )
            row = (await session.execute(statement)).scalar_one()
            return self._payload(row)

    @staticmethod
    def _payload(row) -> dict:
        return {
            "version": row.version,
            "connections": row.connections,
            "tasks": row.tasks,
            "popup": row.popup,
            "quiet": row.quiet,
            "start": row.start,
            "end": row.end,
        }
