"""Static contracts only: no database and no Alembic execution."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.core.access_catalog import resource_for, workspace_grants
from app.models.knowledge import DocumentApproval
from app.models.types.encrypted import EncryptedText
from app.types import ActionType

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "p19t04_migration", ROOT / "migrations/versions/0148_document_approvals.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_one_head_and_reserved_parent():
    graph = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = graph.get_heads()
    assert len(heads) == 1
    assert "0148" in {revision.revision for revision in graph.walk_revisions(base="base", head=heads[0])}
    assert migration.revision == "0148" and migration.down_revision == "0147"


def test_model_has_only_allocated_columns_and_guards():
    t = DocumentApproval.__table__
    assert set(t.c.keys()) == {
        "id",
        "tenant_id",
        "document_id",
        "revision_id",
        "revision_hash",
        "requester_user_id",
        "approver_user_id",
        "status",
        "comment_encrypted",
        "request_idempotency_key",
        "request_hash",
        "decision_hash",
        "requested_at",
        "responded_at",
        "expires_at",
        "created_at",
        "updated_at",
        "pii_updated_at",
    }
    assert isinstance(t.c.comment_encrypted.type, EncryptedText)
    assert t.c.id.identity is not None
    checks = {c.name for c in t.constraints if isinstance(c, CheckConstraint)}
    assert checks == {
        "ck_approval_status",
        "ck_approval_revision_hash",
        "ck_approval_request_hash",
        "ck_approval_decision_hash",
        "ck_approval_expiry",
        "ck_approval_decision",
    }
    fks = {c.name: list(c.column_keys) for c in t.constraints if isinstance(c, ForeignKeyConstraint)}
    assert fks["fk_approval_revision"] == ["tenant_id", "document_id", "revision_id"]
    assert fks["fk_approval_document"] == ["tenant_id", "document_id"]
    assert any(c.name == "uq_approval_request" for c in t.constraints if isinstance(c, UniqueConstraint))
    indexes = {i.name: i for i in t.indexes}
    assert indexes["uq_approval_pending_document"].unique
    assert str(indexes["uq_approval_pending_document"].dialect_options["postgresql"]["where"]) == "status = 'pending'"
    assert set(indexes) == {"uq_approval_pending_document", "ix_approval_inbox_expiry"}


def test_dedicated_right_and_rotation_registration():
    from cli.commands.pii import REGISTRY
    from scripts.schema_guards import GUARD_TRIGGERS

    resource = resource_for("documents.approval")
    assert resource.table_name == "document_approvals" and resource.actions == {ActionType.CONFIGURE}
    assert ("documentapproval", ActionType.CONFIGURE) in workspace_grants("manager")
    assert ("documentapproval", ActionType.CONFIGURE) not in workspace_grants("viewer")
    assert ("document_approvals", "comment_encrypted", None) in REGISTRY
    assert any(t.table == "document_approvals" and t.name == "contact_pii_marker" for t in GUARD_TRIGGERS)


@pytest.mark.parametrize("has_data", [True, False])
def test_downgrade_locks_and_refuses_data_before_changes(monkeypatch, has_data):
    bind = SimpleNamespace(
        dialect=SimpleNamespace(identifier_preparer=SimpleNamespace(quote_schema=lambda v: f'"{v}"')),
        execute=Mock(),
        scalar=Mock(return_value=has_data),
    )
    op = Mock()
    op.get_bind.return_value = bind
    registry = Mock()
    monkeypatch.setattr(migration, "op", op)
    monkeypatch.setattr(migration, "_catalog", registry)
    if has_data:
        with pytest.raises(RuntimeError, match="Downgrade refused"):
            migration.downgrade()
        op.drop_table.assert_not_called()
        registry.assert_not_called()
    else:
        migration.downgrade()
        registry.assert_called_once_with(False)
        op.drop_table.assert_called_once()
    assert "ACCESS EXCLUSIVE" in str(bind.execute.call_args.args[0])
    assert "SELECT EXISTS" in str(bind.scalar.call_args.args[0])
