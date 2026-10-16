"""Notification and digest persistence. Managers bind in app.models.bootstrap."""

from __future__ import annotations

from .notification import Notification
from .notification_preference import NotificationPreference

__all__ = ["Notification", "NotificationPreference", "DigestRun"]

from .digest_run import DigestRun
