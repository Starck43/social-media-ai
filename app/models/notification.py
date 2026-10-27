"""Compatibility aliases; canonical declarations live in domain packages."""

from .managers.notification_manager import NotificationManager
from .notifications.notification import Notification

__all__ = ["Notification", "NotificationManager"]
