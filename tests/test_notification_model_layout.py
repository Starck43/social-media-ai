"""Static layout checks for the notifications model domain package.

Mirrors tests/test_tenancy_model_layout.py: class identity across public,
legacy, domain and leaf import paths, exactly one mapper registration,
preserved manager binding and legacy manager exports, unchanged schema
declarations and fresh-process import entry points. New test bodies issue
no SQL of their own; standard conftest fixtures still apply.
"""

from __future__ import annotations

import subprocess
import sys

from app.core.config import settings
from app.models import Base
from app.models import Notification as notification_public
from app.models.managers import NotificationManager as notification_manager_export
from app.models.managers.notification_manager import (
    NotificationManager as notification_manager_direct,
)
from app.models.notification import Notification as notification_legacy
from app.models.notifications import Notification as notification_domain
from app.models.notifications.notification import Notification as notification_leaf


def test_class_identity_across_all_paths():
    assert notification_public is notification_leaf
    assert notification_legacy is notification_leaf
    assert notification_domain is notification_leaf
    assert notification_leaf.__name__ == "Notification"
    assert notification_leaf.__module__ == "app.models.notifications.notification"


def test_exactly_one_mapper_registration():
    registered = [
        obj
        for obj in Base.registry._class_registry.get("Notification", ())
        if obj is not None
    ]
    assert len(registered) == 1
    assert registered[0] is notification_leaf


def test_manager_binding_preserved():
    assert isinstance(notification_leaf.objects, NotificationManager)
    assert isinstance(notification_leaf.objects, notification_manager_direct)
    assert notification_manager_direct is notification_manager_export
    assert notification_leaf.objects.model is notification_leaf


def test_table_declaration_unchanged():
    table = notification_leaf.__table__
    assert table.name == "notifications"
    assert table.schema == settings.DB_SCHEMA
    assert "ix_notifications_tenant_id" in {index.name for index in table.indexes}
    assert set(table.columns.keys()) == {
        "id",
        "title",
        "message",
        "notification_type",
        "is_read",
        "related_entity_type",
        "related_entity_id",
        "tenant_id",
        "created_at",
        "updated_at",
    }


def test_fresh_process_import_entry_points():
    snippets = [
        "from app.models import Notification; assert Notification.__table__.name == 'notifications'",
        "from app.models.notification import Notification; assert Notification.objects is not None",
        "from app.models.notifications import Notification",
        "from app.models.notifications.notification import Notification",
        "from app.models.managers import NotificationManager",
        "import app.models; import app.models.notification; import app.models.notifications",
    ]
    for snippet in snippets:
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"fresh-process import failed: {snippet}\n{result.stderr}"
        )
