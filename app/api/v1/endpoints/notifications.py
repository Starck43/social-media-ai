"""Notification API endpoints for managing user notifications."""

import logging
from typing import Optional, List
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models import User, Notification
from app.services.user.auth import get_authenticated_user
from app.services.notifications.service import notify
from app.types import NotificationType
from app.schemas.notification import (
    NotificationResponse,
    NotificationCreate,
    NotificationStats,
)

router = APIRouter(tags=["notifications"])
logger = logging.getLogger(__name__)


@router.get("/notifications", response_model=list[NotificationResponse])
async def list_notifications(
    is_read: Optional[bool] = None,
    notification_type: Optional[NotificationType] = None,
    since: Optional[datetime] = Query(None, description="Show notifications since this date"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_authenticated_user),
):
    """
    List notifications with filters.
    
    Filters:
    — is_read: Filter by read status
    — notification_type: Filter by type
    — since: Show notifications created after this date
    — limit: Max amount notifications to return
    — offset: Pagination offset
    """
    logger.info(
        f"User {current_user.username} listing notifications "
        f"(is_read={is_read}, type={notification_type}, since={since})"
    )

    # Build query
    query = Notification.objects.filter()

    if is_read is not None:
        query = query.filter(is_read=is_read)

    if notification_type:
        query = query.filter(notification_type=notification_type.value)

    if since:
        query = query.filter(created_at__gte=since)

    # Apply ordering and pagination
    notifications = await (
        query.order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    return [
        NotificationResponse(
            id=n.id,
            title=n.title,
            message=n.message,
            notification_type=str(n.notification_type) if n.notification_type else "unknown",
            is_read=n.is_read,
            related_entity_type=n.related_entity_type,
            related_entity_id=n.related_entity_id,
            created_at=n.created_at.isoformat() if n.created_at else "",
        )
        for n in notifications
    ]


@router.get("/notifications/stats", response_model=NotificationStats)
async def get_notification_stats(
    since: Optional[datetime] = Query(None, description="Stats since this date"),
    current_user: User = Depends(get_authenticated_user),
):
    """
    Get notification statistics.
    
    Returns total count, unread count, and breakdown by type.
    """
    logger.info(f"User {current_user.username} requesting notification stats")

    # Build query
    query = Notification.objects.filter()
    if since:
        query = query.filter(created_at__gte=since)

    all_notifications = await query

    total = len(all_notifications)
    unread = len([n for n in all_notifications if not n.is_read])

    # Count by type
    by_type = {}
    for n in all_notifications:
        ntype = str(n.notification_type) if n.notification_type else "unknown"
        by_type[ntype] = by_type.get(ntype, 0) + 1

    return NotificationStats(total=total, unread=unread, by_type=by_type)


@router.get("/notifications/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: int, current_user: User = Depends(get_authenticated_user)
):
    """Get a specific notification by ID."""
    notification = await Notification.objects.get(id=notification_id)

    if not notification:
        logger.warning(f"Notification {notification_id} not found")
        raise HTTPException(status_code=404, detail="Notification not found")

    logger.info(f"User {current_user.username} viewing notification {notification_id}")

    return NotificationResponse(
        id=notification.id,
        title=notification.title,
        message=notification.message,
        notification_type=str(notification.notification_type)
        if notification.notification_type
        else "unknown",
        is_read=notification.is_read,
        related_entity_type=notification.related_entity_type,
        related_entity_id=notification.related_entity_id,
        created_at=notification.created_at.isoformat() if notification.created_at else "",
    )


@router.post("/notifications", response_model=NotificationResponse)
async def create_notification(
    request: NotificationCreate, current_user: User = Depends(get_authenticated_user)
):
    """
    Create a new notification.
    
    Admin access required.
    """
    if not current_user.is_superuser:
        logger.warning(
            f"User {current_user.username} attempted to create notification without permission"
        )
        raise HTTPException(status_code=403, detail="Admin access required")

    logger.info(
        f"User {current_user.username} creating notification: {request.title} "
        f"(type={request.notification_type})"
    )

    notification = await notify.create(
        title=request.title,
        message=request.message,
        ntype=request.notification_type,
        entity_type=request.related_entity_type,
        entity_id=request.related_entity_id,
    )

    return NotificationResponse(
        id=notification.id,
        title=notification.title,
        message=notification.message,
        notification_type=str(notification.notification_type)
        if notification.notification_type
        else "unknown",
        is_read=notification.is_read,
        related_entity_type=notification.related_entity_type,
        related_entity_id=notification.related_entity_id,
        created_at=notification.created_at.isoformat() if notification.created_at else "",
    )


@router.post("/notifications/{notification_id}/mark-read")
async def mark_notification_as_read(
    notification_id: int, current_user: User = Depends(get_authenticated_user)
):
    """Mark a notification as read."""
    notification = await Notification.objects.get(id=notification_id)

    if not notification:
        logger.warning(f"Notification {notification_id} not found")
        raise HTTPException(status_code=404, detail="Notification not found")

    if not notification.is_read:
        await Notification.objects.update_by_id(notification_id, is_read=True)
        logger.info(
            f"User {current_user.username} marked notification {notification_id} as read"
        )

    return {"status": "marked_as_read", "notification_id": notification_id}


@router.post("/notifications/mark-all-read")
async def mark_all_as_read(current_user: User = Depends(get_authenticated_user)):
    """Mark all unread notifications as read."""
    unread = await Notification.objects.filter(is_read=False)

    count = 0
    for notification in unread:
        await Notification.objects.update_by_id(notification.id, is_read=True)
        count += 1

    logger.info(f"User {current_user.username} marked {count} notifications as read")

    return {"status": "success", "marked_count": count}


@router.delete("/notifications/{notification_id}")
async def delete_notification(
    notification_id: int, current_user: User = Depends(get_authenticated_user)
):
    """
    Delete a notification.
    
    Admin access required.
    """
    if not current_user.is_superuser:
        logger.warning(
            f"User {current_user.username} attempted to delete notification without permission"
        )
        raise HTTPException(status_code=403, detail="Admin access required")

    notification = await Notification.objects.get(id=notification_id)

    if not notification:
        logger.warning(f"Notification {notification_id} not found")
        raise HTTPException(status_code=404, detail="Notification not found")

    await Notification.objects.delete(notification_id)
    logger.info(f"User {current_user.username} deleted notification {notification_id}")

    return {"status": "deleted", "notification_id": notification_id}


@router.post("/notifications/cleanup")
async def cleanup_old_notifications(
    days: int = Query(30, ge=1, le=365, description="Delete notifications older than X days"),
    current_user: User = Depends(get_authenticated_user),
):
    """
    Delete old notifications.
    
    Admin access required. Deletes read notifications older than specified days.
    """
    if not current_user.is_superuser:
        logger.warning(
            f"User {current_user.username} attempted cleanup without permission"
        )
        raise HTTPException(status_code=403, detail="Admin access required")

    cutoff_date = datetime.now(UTC) - timedelta(days=days)

    old_notifications = await Notification.objects.filter(
        is_read=True, created_at__lt=cutoff_date
    )

    count = 0
    for notification in old_notifications:
        await Notification.objects.delete(notification.id)
        count += 1

    logger.info(
        f"User {current_user.username} cleaned up {count} notifications older than {days} days"
    )

    return {"status": "cleaned", "deleted_count": count, "older_than_days": days}
