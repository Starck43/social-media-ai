"""Compatibility facade for the notifications model domain.

The canonical Notification declaration now lives in
`app.models.notifications.notification`. This module keeps the legacy
`app.models.notification` import path working and remains the place where
`Notification.objects` is bound, so the manager import order is unchanged.

Do not move manager creation into the leaf module: NotificationManager
resolves the model by importing it back from this facade.
"""

from __future__ import annotations

from .notifications.notification import Notification  # noqa: F401

# Manager binding stays at this legacy entry point (import order preserved).
from .managers.notification_manager import NotificationManager  # noqa: E402,F401

Notification.objects = NotificationManager()
