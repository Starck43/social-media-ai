"""Notifications model domain package.

Canonical home of the Notification declaration. The legacy module
`app.models.notification` remains the compatibility facade and the
manager-binding entry point; it is not removed in this package.
"""

from __future__ import annotations

from .notification import Notification

__all__ = ["Notification"]
