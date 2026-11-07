"""P19-T01 document management, preserving revision invalidation.

Revision ID: 0142
Revises: 0141

This draft currently implements only the status constraint. Do not integrate
until the remaining model, API and migration work and required DB checks pass.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0142"
down_revision = "0141"
branch_labels = None
depends_on = None

OLD_STATUSES = "'draft','approved','draft_invalidated','rejected'"
NEW_STATUSES = "'draft','in_review','approved','rejected','revision_requested','draft_invalidated'"


def _replace_status_constraint(statuses):
    op.drop_constraint("ck_document_revision_status", "document_revisions", schema=settings.DB_SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_document_revision_status", "document_revisions", f"status IN ({statuses})", schema=settings.DB_SCHEMA
    )


def upgrade():
    # Legacy draft_invalidated remains stored: the append path still produces it.
    # No revision content or approval state is rewritten.
    _replace_status_constraint(NEW_STATUSES)


def downgrade():
    # Take the lock before inspection so another transaction cannot insert a
    # new-only status between the check and narrowing the constraint. Refusing
    # downgrade preserves its meaning rather than silently rewriting history.
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    bind.execute(sa.text(f"LOCK TABLE {schema}.document_revisions IN ACCESS EXCLUSIVE MODE"))
    new_status_exists = bind.execute(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {schema}.document_revisions WHERE status NOT IN ({OLD_STATUSES}))")
    ).scalar_one()
    if new_status_exists:
        raise RuntimeError("Downgrade refused: document revisions have statuses unsupported by 0141")
    _replace_status_constraint(OLD_STATUSES)
