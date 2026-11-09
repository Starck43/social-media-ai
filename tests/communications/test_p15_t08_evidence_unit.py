"""DB-free checks for the allocated reply-evidence contract."""

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint

from app.models.communications.outbox import OutboxMessage, OutboxReplyEvidence
from app.models.types.encrypted import EncryptedText
from cli.commands.pii import REGISTRY


def test_reply_evidence_revision_has_one_head_and_fixed_parent():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(script.get_heads()) == 1
    assert "0146" in {revision.revision for revision in script.walk_revisions()}
    assert script.get_revision("0146").down_revision == "0143"


def test_reply_evidence_model_has_approved_columns_and_encryption():
    table = OutboxReplyEvidence.__table__
    assert set(table.c.keys()) == {
        "id",
        "tenant_id",
        "outbox_message_id",
        "thread_id",
        "inbound_message_id",
        "headers_encrypted",
        "created_at",
        "updated_at",
        "pii_updated_at",
    }
    assert isinstance(table.c.headers_encrypted.type, EncryptedText)
    assert ("outbox_reply_evidence", "headers_encrypted", None) in REGISTRY
    assert OutboxMessage.__table__.c.reply_evidence_required.nullable is False


def test_reply_evidence_relationships_are_tenant_scoped():
    table = OutboxReplyEvidence.__table__
    composite = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint) and len(constraint.columns) == 2
    ]
    assert {tuple(constraint.column_keys) for constraint in composite} == {
        ("tenant_id", "outbox_message_id"),
        ("tenant_id", "thread_id"),
        ("tenant_id", "inbound_message_id"),
    }
    assert all(constraint.ondelete == "CASCADE" for constraint in composite)
