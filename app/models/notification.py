"""Deprecated compatibility path for ``Notification``.

Canonical import: ``from app.models import Notification`` (or the domain package
``app.models.notifications``). The class is declared once in
``app.models.notifications.notification``; managers are bound only by
``app.models.bootstrap``. This module re-exports the same objects and declares
nothing; removal of the root compatibility modules is tracked by P10-T28.
"""

from __future__ import annotations

from .managers.notification_manager import NotificationManager
from .notifications.notification import Notification

__all__ = ["Notification", "NotificationManager"]
