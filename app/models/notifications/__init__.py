"""Notification and digest persistence. Managers bind in app.models.bootstrap."""

from __future__ import annotations

from .notification import Notification

__all__ = ["Notification", "DigestRun"]

from .digest_run import DigestRun
