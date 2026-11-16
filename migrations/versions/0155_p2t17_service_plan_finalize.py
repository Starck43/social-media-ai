"""Finalize sponsorship only after an explicit, recorded operator backfill.

Revision 0155 follows the additive storage cut; no reassignment or activation.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0155"
down_revision = "0154"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot verify service-plan backfill offline")
    schema = settings.DB_SCHEMA
    connection = op.get_bind()
    quoted = connection.dialect.identifier_preparer.quote_schema(schema)
    connection.execute(
        sa.text(
            f"LOCK TABLE {quoted}.tenants, {quoted}.user_service_plans, "
            f"{quoted}.service_plan_backfill_resolutions IN ACCESS EXCLUSIVE MODE"
        )
    )
    unresolved = connection.scalar(sa.text(f"""
        SELECT count(*) FROM {quoted}.tenants t
        LEFT JOIN {quoted}.user_service_plans p ON p.user_id = t.plan_holder_user_id
        LEFT JOIN {quoted}.service_plan_backfill_resolutions r ON r.tenant_id = t.id
        WHERE t.plan_holder_user_id IS NULL OR p.user_id IS NULL
           OR r.tenant_id IS NULL OR r.holder_user_id <> t.plan_holder_user_id
    """))
    if unresolved:
        raise RuntimeError("Service-plan finalization refused: unresolved sponsorship or missing backfill evidence")
    op.alter_column("tenants", "plan_holder_user_id", existing_type=sa.Integer, nullable=False, schema=schema)


def downgrade():
    op.alter_column(
        "tenants", "plan_holder_user_id", existing_type=sa.Integer, nullable=True, schema=settings.DB_SCHEMA
    )
