"""Explicit actor-owned cross-workspace inbox operations, never tenant bypass.

The only cross-workspace reads join receipts owned by the authenticated User.
TenantUser is an access predicate, not the inbox owner. Generic event archive
methods remain on NotificationManager for compatibility, not user-facing reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id

from .base_manager import BaseManager


class NotificationReceiptManager(BaseManager):
    def __init__(self):
        from ..notifications.notification_receipt import NotificationReceipt

        super().__init__(NotificationReceipt)

    @staticmethod
    def _actor(user_id):
        user = get_current_user()
        if type(user_id) is not int or user is None or not user.is_active or user.id != user_id:
            raise PermissionError("Personal notification owner required")

    def _visible(self, user_id):
        from app.models import Notification, Tenant, TenantUser, User

        now = datetime.now(timezone.utc)
        membership = (
            select(TenantUser.id)
            .where(
                TenantUser.user_id == user_id,
                TenantUser.tenant_id == Notification.tenant_id,
                TenantUser.channel == "web",
                TenantUser.is_active.is_(True),
                TenantUser.role_id.is_not(None),
            )
            .exists()
        )
        return (
            self.model.user_id == user_id,
            membership,
            select(User.id).where(User.id == user_id, User.is_active.is_(True)).exists(),
            select(Tenant.id).where(Tenant.id == Notification.tenant_id, Tenant.is_active.is_(True)).exists(),
            or_(Notification.expires_at.is_(None), Notification.expires_at > now),
            self.model.dismissed_at.is_(None),
            or_(self.model.snoozed_until.is_(None), self.model.snoozed_until <= now),
        )

    async def publish(
        self,
        *,
        recipient_user_ids,
        title,
        message,
        notification_type,
        related_entity_type=None,
        related_entity_id=None,
        severity="info",
        dedup_key=None,
        expires_at=None,
    ):
        """Trusted producer: explicit active recipients in the event's origin workspace.

        Empty recipients keep an archive event, never broadcast. Event + receipts
        are committed together. No automatic migration of historical shared flags.
        """
        from app.models import Notification, Tenant, TenantUser, User

        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise PermissionError("Notification origin workspace required")
        recipients = set(recipient_user_ids)
        if any(type(uid) is not int or uid <= 0 for uid in recipients):
            raise ValueError("Invalid recipient")
        if severity not in {"info", "warning", "action_required"} or (
            dedup_key is not None and not 1 <= len(dedup_key) <= 200
        ):
            raise ValueError("Invalid notification lifecycle")
        if expires_at is not None and (expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc)):
            raise ValueError("Notification expiry must be in the future")
        async with self._get_owned_session() as session:
            if not await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))):
                raise PermissionError("Inactive notification origin")
            allowed = (
                set(
                    (
                        await session.scalars(
                            select(User.id)
                            .join(TenantUser, TenantUser.user_id == User.id)
                            .where(
                                User.id.in_(recipients),
                                User.is_active.is_(True),
                                TenantUser.tenant_id == tenant_id,
                                TenantUser.channel == "web",
                                TenantUser.is_active.is_(True),
                                TenantUser.role_id.is_not(None),
                            )
                        )
                    ).all()
                )
                if recipients
                else set()
            )
            if recipients != allowed:
                raise PermissionError("Recipients must be active users of the origin workspace")
            values = dict(
                tenant_id=tenant_id,
                title=title,
                message=message,
                notification_type=notification_type,
                related_entity_type=related_entity_type,
                related_entity_id=related_entity_id,
                severity=severity,
                dedup_key=dedup_key,
                expires_at=expires_at,
                is_read=False,
            )
            stmt = insert(Notification).values(**values)
            if dedup_key is not None:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[Notification.tenant_id, Notification.dedup_key],
                    index_where=Notification.dedup_key.is_not(None),
                )
            event = (await session.execute(stmt.returning(Notification))).scalar_one_or_none()
            if event is None:
                event = await session.scalar(
                    select(Notification).where(Notification.tenant_id == tenant_id, Notification.dedup_key == dedup_key)
                )
                if any(
                    getattr(event, key) != values[key]
                    for key in (
                        "title",
                        "message",
                        "notification_type",
                        "related_entity_type",
                        "related_entity_id",
                        "severity",
                    )
                ):
                    raise ValueError("Notification dedup key reused for different content")
                existing = set(
                    (
                        await session.scalars(select(self.model.user_id).where(self.model.notification_id == event.id))
                    ).all()
                )
                if existing != recipients:
                    raise ValueError("Notification dedup key reused for different recipients")
                return event
            if recipients:
                await session.execute(
                    insert(self.model).values([dict(notification_id=event.id, user_id=uid) for uid in recipients])
                )
            return event

    async def list_for_user(
        self, user_id, *, is_read=None, notification_type=None, since=None, limit=100, offset=0, notification_id=None
    ):
        self._actor(user_id)
        from app.models import Notification

        conditions = list(self._visible(user_id))
        if is_read is not None:
            conditions.append(self.model.read_at.is_not(None) if is_read else self.model.read_at.is_(None))
        if notification_type is not None:
            conditions.append(Notification.notification_type == notification_type)
        if since is not None:
            conditions.append(Notification.created_at >= since)
        if notification_id is not None:
            conditions.append(Notification.id == notification_id)
        statement = (
            select(self.model, Notification)
            .join(Notification, Notification.id == self.model.notification_id)
            .where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(max(1, min(limit, 100)))
            .offset(max(0, offset))
        )
        async with self._get_owned_session() as session:
            return list((await session.execute(statement)).all())

    async def mark_read(self, user_id, notification_ids):
        self._actor(user_id)
        from app.models import Notification

        ids = (
            select(self.model.id)
            .join(Notification, Notification.id == self.model.notification_id)
            .where(*self._visible(user_id), Notification.id.in_(notification_ids))
        )
        async with self._get_owned_session() as session:
            result = await session.execute(
                update(self.model)
                .where(self.model.id.in_(ids), self.model.read_at.is_(None))
                .values(read_at=func.now())
                .execution_options(synchronize_session=False)
            )
            return result.rowcount

    async def dismiss(self, user_id, notification_ids):
        self._actor(user_id)
        async with self._get_owned_session() as session:
            result = await session.execute(
                update(self.model)
                .where(
                    self.model.user_id == user_id,
                    self.model.notification_id.in_(notification_ids),
                    self.model.dismissed_at.is_(None),
                )
                .values(dismissed_at=func.now())
            )
            return result.rowcount

    async def cleanup_for_user(self, user_id, days=30):
        self._actor(user_id)
        async with self._get_owned_session() as session:
            result = await session.execute(
                delete(self.model).where(
                    self.model.user_id == user_id,
                    self.model.read_at < datetime.now(timezone.utc) - timedelta(days=days),
                )
            )
            return result.rowcount

    async def stats_for_user(self, user_id, since=None):
        self._actor(user_id)
        from app.models import Notification

        conditions = list(self._visible(user_id))
        if since is not None:
            conditions.append(Notification.created_at >= since)
        stmt = (
            select(Notification.notification_type, self.model.read_at.is_(None), func.count(self.model.id))
            .join(Notification, Notification.id == self.model.notification_id)
            .where(*conditions)
            .group_by(Notification.notification_type, self.model.read_at.is_(None))
        )
        async with self._get_owned_session() as session:
            rows = (await session.execute(stmt)).all()
        stats = {"total": 0, "unread": 0, "by_type": {}}
        for kind, unread, count in rows:
            key = kind.name
            stats["total"] += count
            stats["unread"] += count if unread else 0
            stats["by_type"][key] = stats["by_type"].get(key, 0) + count
        return stats
