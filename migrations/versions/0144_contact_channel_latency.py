"""P15-T07 observed response-latency aggregates.

Revision ID: 0144
Revises: 0142

Existing statistics are unmeasured (zero samples, never a zero-latency sample).
Downgrade refuses to erase observations or outstanding sample state, including
those held in encrypted merge journals. It must run online with PII keys.
"""

import json

import sqlalchemy as sa
from alembic import op

from app.core.config import settings
from app.models.types.encrypted import EncryptedText

revision = "0144"
down_revision = "0142"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.add_column(
        "contact_channel_stats",
        sa.Column("latency_total_ms", sa.BigInteger(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column(
        "contact_channel_stats",
        sa.Column("latency_sample_count", sa.BigInteger(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column(
        "contact_channel_stats",
        sa.Column("latency_pending_outbound_at", sa.DateTime(timezone=True), nullable=True),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_contact_channel_stats_latency_total_nonneg", "contact_channel_stats", "latency_total_ms >= 0", schema=schema
    )
    op.create_check_constraint(
        "ck_contact_channel_stats_latency_count_nonneg",
        "contact_channel_stats",
        "latency_sample_count >= 0",
        schema=schema,
    )


def _journal_has_observations(raw):
    if raw is None:
        return False
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Cannot safely inspect an encrypted contact merge journal") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("snapshot"), dict):
        raise RuntimeError("Cannot safely inspect an encrypted contact merge journal")
    snapshot = payload["snapshot"]
    for key in ("source_stats_before", "target_stats_before"):
        rows = snapshot.get(key, [])
        if not isinstance(rows, list):
            raise RuntimeError("Cannot safely inspect an encrypted contact merge journal")
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("Cannot safely inspect an encrypted contact merge journal")
            if row.get("latency_total_ms") or row.get("latency_sample_count") or row.get("latency_pending_outbound_at"):
                return True
    return False


def downgrade():
    bind = op.get_bind()
    schema = settings.DB_SCHEMA
    quote = bind.dialect.identifier_preparer.quote_schema
    stats = f'{quote(schema)}.{quote("contact_channel_stats")}'
    journals = f'{quote(schema)}.{quote("contact_merge_journal")}'
    # Stop concurrent ingest/merge during the check and schema change. Use the
    # actual configured schema, never public. The enclosing Alembic transaction
    # keeps both locks until the whole downgrade commits or rolls back.
    bind.execute(sa.text(f"LOCK TABLE {stats}, {journals} IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {stats} WHERE latency_total_ms <> 0 OR latency_sample_count <> 0 "
            "OR latency_pending_outbound_at IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("Downgrade would discard channel latency observations")
    table = sa.Table(
        "contact_merge_journal", sa.MetaData(), sa.Column("reason_encrypted", EncryptedText()), schema=schema
    )
    try:
        result = bind.execute(sa.select(table.c.reason_encrypted))
        for (raw,) in result:
            if _journal_has_observations(raw):
                raise RuntimeError("Downgrade would discard journaled channel latency observations")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Cannot safely inspect encrypted contact merge journals") from exc
    op.drop_constraint(
        "ck_contact_channel_stats_latency_count_nonneg", "contact_channel_stats", type_="check", schema=schema
    )
    op.drop_constraint(
        "ck_contact_channel_stats_latency_total_nonneg", "contact_channel_stats", type_="check", schema=schema
    )
    op.drop_column("contact_channel_stats", "latency_pending_outbound_at", schema=schema)
    op.drop_column("contact_channel_stats", "latency_sample_count", schema=schema)
    op.drop_column("contact_channel_stats", "latency_total_ms", schema=schema)
