"""Notifications for `/app` — JSON endpoints backing the header bell modal.

The bell in `base.html` fetches recent notifications from `/app/notifications`
and (after a short delay) marks them read via `/app/notifications/read-all`.
Both run inside `tenant_scope` (see TenantUIMiddleware), so the tenant-scoped
Notification manager filters to the active workspace.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models import Notification

router = APIRouter(prefix="/notifications")


def _serialize(notification) -> dict:
    return {
        "id": notification.id,
        "title": notification.title,
        "message": notification.message,
        "notification_type": (
            getattr(notification.notification_type, "value", None)
            if notification.notification_type is not None
            else None
        ),
        "is_read": notification.is_read,
        "created_at": notification.created_at.strftime("%d.%m %H:%M") if notification.created_at else "",
    }


@router.get("")
@router.get("/")
async def notifications_list(request: Request, limit: int = 30):
    """Unread notifications for the active tenant (newest first).

    Read notifications drop out of the list entirely — once dismissed they no
    longer resurface when the bell is reopened.
    """
    notifications = (
        await Notification.objects.filter(is_read=False).order_by(Notification.created_at.desc()).limit(limit)
    )
    return JSONResponse({"notifications": [_serialize(n) for n in notifications]})


@router.post("/read-all")
async def notifications_read_all(request: Request):
    """Mark all unread notifications of the active tenant as read."""
    from app.models.managers.notification_manager import NotificationManager

    count = await NotificationManager().mark_all_as_read()
    return JSONResponse({"marked": count})


@router.post("/read/{notification_id}")
async def notification_read_one(request: Request, notification_id: int):
    """Mark a single notification as read (removes it from the unread list)."""
    from app.models.managers.notification_manager import NotificationManager

    updated = await NotificationManager().mark_as_read(notification_id)
    return JSONResponse({"ok": bool(updated)})
