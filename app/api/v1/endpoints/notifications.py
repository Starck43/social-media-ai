"""Authenticated User inbox, independent of the API's selected workspace.

Canonical routes live directly under /api/v1/notifications. The old doubled
prefix stays as a hidden compatibility alias. Mutations affect only receipts.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import require_model_perm
from app.models import NotificationReceipt, User
from app.schemas.notification import NotificationCreate, NotificationResponse, NotificationStats
from app.services.notifications.inbox import PersonalInbox
from app.services.notifications.links import open_link
from app.services.notifications.service import notify
from app.services.user.auth import get_authenticated_user
from app.types import ActionType, NotificationType


def _private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(tags=["notifications"], dependencies=[Depends(_private_response)])


def _response(entry):
    event, receipt = entry.event, entry.receipt
    return NotificationResponse(
        id=event.id,
        title=event.title,
        message=event.message,
        notification_type=event.notification_type,
        is_read=receipt.read_at is not None,
        related_entity_type=event.related_entity_type,
        related_entity_id=event.related_entity_id,
        created_at=event.created_at.isoformat(),
        origin_workspace_id=event.tenant_id,
        severity=event.severity,
        destination=open_link(event.id, receipt.user_id),
    )


@router.get("", response_model=list[NotificationResponse])
@router.get("/notifications", response_model=list[NotificationResponse], include_in_schema=False)
async def list_notifications(
    is_read: bool | None = None,
    notification_type: str | None = None,
    since: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_authenticated_user),
):
    if notification_type is not None:
        notification_type = NotificationType.get_by_name(notification_type.upper()) or NotificationType.get_by_value(
            notification_type.lower()
        )
        if notification_type is None:
            raise HTTPException(422, "Unknown notification type")
    return [
        _response(e)
        for e in await PersonalInbox(current_user).entries(
            is_read=is_read, notification_type=notification_type, since=since, limit=limit, offset=offset
        )
    ]


@router.get("/stats", response_model=NotificationStats)
@router.get("/notifications/stats", response_model=NotificationStats, include_in_schema=False)
async def get_notification_stats(since: datetime | None = None, current_user: User = Depends(get_authenticated_user)):
    return await NotificationReceipt.objects.stats_for_user(current_user.id, since)


@router.post("", response_model=NotificationResponse)
@router.post("/notifications", response_model=NotificationResponse, include_in_schema=False)
async def create_notification(
    request: NotificationCreate, current_user: User = Depends(require_model_perm("notification", ActionType.CREATE))
):
    event = await notify.create(
        title=request.title,
        message=request.message,
        ntype=request.notification_type,
        entity_type=request.related_entity_type,
        entity_id=request.related_entity_id,
        recipient_user_ids=[current_user.id],
        severity=request.severity,
    )
    entries = await PersonalInbox(current_user).entries(notification_id=event.id)
    if not entries:
        raise HTTPException(404, "Notification target unavailable")
    return _response(entries[0])


@router.post("/mark-all-read")
@router.post("/notifications/mark-all-read", include_in_schema=False)
async def mark_all_as_read(current_user: User = Depends(get_authenticated_user)):
    return {"status": "success", "marked_count": await PersonalInbox(current_user).read_window(), "windowed": True}


@router.post("/cleanup")
@router.post("/notifications/cleanup", include_in_schema=False)
async def cleanup_old_notifications(
    days: int = Query(30, ge=1, le=365), current_user: User = Depends(get_authenticated_user)
):
    count = await NotificationReceipt.objects.cleanup_for_user(current_user.id, days)
    return {"status": "cleaned", "deleted_count": count, "older_than_days": days}


@router.post("/{notification_id}/mark-read")
@router.post("/notifications/{notification_id}/mark-read", include_in_schema=False)
async def mark_notification_as_read(notification_id: int, current_user: User = Depends(get_authenticated_user)):
    if not await PersonalInbox(current_user).read(notification_id):
        raise HTTPException(404, "Notification not found")
    return {"status": "marked_as_read", "notification_id": notification_id}


@router.get("/{notification_id}", response_model=NotificationResponse)
@router.get("/notifications/{notification_id}", response_model=NotificationResponse, include_in_schema=False)
async def get_notification(notification_id: int, current_user: User = Depends(get_authenticated_user)):
    entries = await PersonalInbox(current_user).entries(notification_id=notification_id)
    if not entries:
        raise HTTPException(404, "Notification not found")
    return _response(entries[0])


@router.delete("/{notification_id}")
@router.delete("/notifications/{notification_id}", include_in_schema=False)
async def delete_notification(notification_id: int, current_user: User = Depends(get_authenticated_user)):
    if not await PersonalInbox(current_user).entries(notification_id=notification_id):
        raise HTTPException(404, "Notification not found")
    await NotificationReceipt.objects.dismiss(current_user.id, [notification_id])
    return {"status": "deleted", "notification_id": notification_id}
