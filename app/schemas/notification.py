"""
Pydantic schemas for Notification API endpoints.

These schemas define the structure for creating, updating,
and returning notification data.
"""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.types import NotificationType


class NotificationResponse(BaseModel):
    """
    Notification response schema.
    
    Used for returning notification data in API responses.
    """
    
    id: int
    title: str
    message: str
    notification_type: Any
    is_read: bool = Field(..., description="Whether notification has been read")
    related_entity_type: Optional[str] = Field(None, description="Type of related entity")
    related_entity_id: Optional[int] = Field(None, description="ID of related entity")
    created_at: str = Field(..., description="Creation timestamp (ISO format)")

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("notification_type", check_fields=False)
    def _serialize_notification_type(self, value: Any) -> str:
        if isinstance(value, Enum):
            return value.name
        return str(value) if value else ""


class NotificationCreate(BaseModel):
    """
    Notification creation schema.
    
    Used for creating new notifications via API.
    """
    
    title: str = Field(..., min_length=1, max_length=255, description="Notification title")
    message: str = Field(..., min_length=1, description="Notification message")
    notification_type: NotificationType = Field(..., description="Type of notification")
    related_entity_type: Optional[str] = Field(None, description="Type of related entity")
    related_entity_id: Optional[int] = Field(None, description="ID of related entity")


class NotificationStats(BaseModel):
    """
    Notification statistics response.
    
    Provides overview of notifications grouped by type and status.
    """
    
    total: int = Field(..., description="Total number of notifications")
    unread: int = Field(..., description="Number of unread notifications")
    by_type: dict = Field(..., description="Notification count grouped by type")
