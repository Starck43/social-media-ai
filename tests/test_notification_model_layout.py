"""Static layout checks for the notifications model domain package.

Mirrors tests/test_tenancy_model_layout.py: class identity across public,
legacy, domain and leaf import paths, exactly one mapper registration,
preserved manager binding and legacy manager exports, unchanged schema
declarations and fresh-process import entry points. New test bodies issue
no SQL of their own; standard conftest fixtures still apply.
"""

from __future__ import annotations

import ast
import hashlib
import json
import pickle
import subprocess
import sys
from pathlib import Path

from app.core.config import settings
from app.models import Base
from app.models import Notification as notification_public
from app.models.managers import NotificationManager as notification_manager_export
from app.models.managers.notification_manager import (
	NotificationManager as notification_manager_direct, NotificationManager,
)
from app.models.notification import Notification as notification_legacy
from app.models.notifications import Notification as notification_domain
from app.models.notifications.notification import Notification as notification_leaf


ROOT = Path(__file__).resolve().parents[1]


def _canonical_ast(node):
    """Include empty/None fields explicitly; do not depend on ast.dump defaults.

    Python 3.13 omits optional empty fields by default; 3.12 does not. The
    selected schema-expression nodes have the same fields on both versions.
    Ignore source locations, but retain node types, field names and all values.
    """
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[field, _canonical_ast(value)] for field, value in ast.iter_fields(node)]]
    if isinstance(node, list):
        return [_canonical_ast(value) for value in node]
    return node


def test_class_identity_across_all_paths():
	assert notification_public is notification_leaf
	assert notification_legacy is notification_leaf
	assert notification_domain is notification_leaf
	assert notification_leaf.__name__ == "Notification"
	assert notification_leaf.__module__ == "app.models.notifications.notification"


def test_exactly_one_mapper_registration():
	mappers = [m for m in Base.registry.mappers if m.class_ is notification_leaf]
	assert sum(mapper.class_.__name__ == "Notification" for mapper in Base.registry.mappers) == 1
	assert len(mappers) == 1, (
		f"expected exactly one mapper for Notification, got {len(mappers)}"
	)


def test_manager_binding_preserved():
	assert isinstance(notification_leaf.objects, NotificationManager)
	assert isinstance(notification_leaf.objects, notification_manager_direct)
	assert notification_manager_direct is notification_manager_export
	assert notification_leaf.objects.model is notification_leaf
	from app.models.notification import NotificationManager as legacy_manager_export
	assert legacy_manager_export is NotificationManager


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
			cwd=ROOT,
			timeout=60,
		)
		assert result.returncode == 0, (
			f"fresh-process import failed: {snippet}\n{result.stderr}"
		)


def test_schema_expressions_preserve_types_defaults_and_enum_contract():
    # Original dev b4fd0f0b, still identical at cb1a0a04. Includes enum arguments,
    # defaults, nullability and inherited mixin bases; not merely column names.
    source = (ROOT / "app/models/notifications/notification.py").read_text(encoding="utf-8")
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "Notification")
    assert [ast.unparse(base) for base in cls.bases] == ["Base", "TenantScopedMixin", "TimestampMixin"]
    statements = [
        node for node in cls.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in ("__tablename__", "__table_args__")
                    for target in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.annotation, ast.Subscript)
            and isinstance(node.annotation.value, ast.Name)
            and node.annotation.value.id == "Mapped"
        )
    ]
    payload = _canonical_ast(ast.Module(body=statements, type_ignores=[]))
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert fingerprint == "9cebf3e014a6604febfb3488d5aac26c348cf3a75b74b67669706f982368bb60"


def test_legacy_serialized_notification_class_reference_still_resolves():
    # Only locally constructed trusted class references, never external input.
    assert pickle.loads(b"capp.models.notification\nNotification\n.") is notification_leaf
    assert pickle.loads(pickle.dumps(notification_leaf)) is notification_leaf


def test_schema_snapshot_encoding_preserves_explicit_empty_fields():
    assert _canonical_ast(ast.Module(body=[], type_ignores=[])) == [
        "Module", [["body", []], ["type_ignores", []]],
    ]
