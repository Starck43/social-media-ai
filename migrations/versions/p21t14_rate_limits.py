"""P21-T14 rate buckets + dispatch leases (dormant).

Revision: p21t14_rate_limits. `down_revision` is the live `dev` head
`p21t13_catalog_storage`. No feature flag, HTTP or dispatch code is touched; the
tables exist but nothing writes them until the `RateAcquirer` seam is enabled by
a later revision/commit.

`llm_rate_buckets` is a global token-bucket scope table shared across tenants by
design (the upstream quota pool is shared). `llm_dispatch_leases` bounds in-flight
admission slots and is reclaimable by expiry; it never refunds bucket units and
never releases token/USD liability.

Unique bucket identity includes NULL-tolerant model family: `COALESCE(model_family, '')`
so a NULL family cannot admit duplicate buckets.
"""

from alembic import op
import sqlalchemy as sa

from app.core.config import settings

revision = "p21t14_rate_limits"
down_revision = "p21t13_catalog_storage"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA
s = '"' + SCHEMA + '"'

BUCKET_CHECKS = (
    ("ck_llm_rate_buckets_scope", "scope IN ('upstream', 'holder', 'actor', 'workspace')"),
    ("ck_llm_rate_buckets_unit", "unit IN ('request', 'token')"),
    ("ck_llm_rate_buckets_scope_key", "length(scope_key) > 0"),
    ("ck_llm_rate_buckets_capacity", "capacity_units > 0"),
    ("ck_llm_rate_buckets_refill", "refill_units_per_second > 0"),
    ("ck_llm_rate_buckets_available", "available_units >= 0"),
    ("ck_llm_rate_buckets_available_bounded", "available_units <= capacity_units"),
    ("ck_llm_rate_buckets_family", "model_family IS NULL OR length(model_family) > 0"),
)

LEASE_CHECKS = (
    ("ck_llm_dispatch_leases_identity", "lease_id ~ '^[0-9a-f]{32}$'"),
    ("ck_llm_dispatch_leases_scope", "scope IN ('upstream', 'holder', 'actor', 'workspace')"),
    ("ck_llm_dispatch_leases_scope_key", "length(scope_key) > 0"),
    ("ck_llm_dispatch_leases_family", "model_family IS NULL OR length(model_family) > 0"),
)


def upgrade() -> None:
    op.create_table(
        "llm_rate_buckets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(200), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("model_family", sa.String(64), nullable=True),
        sa.Column("capacity_units", sa.BigInteger, nullable=False),
        sa.Column(
            "refill_units_per_second",
            sa.Numeric(18, 6),
            nullable=False,
        ),
        sa.Column(
            "available_units",
            sa.Numeric(18, 6),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "blocked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        *[
            sa.CheckConstraint(cond, name=name)
            for name, cond in BUCKET_CHECKS
        ],
        schema=SCHEMA,
    )
    op.create_index(
        "uq_llm_rate_buckets_scope",
        "llm_rate_buckets",
        [
            sa.text("scope"),
            sa.text("scope_key"),
            sa.text("unit"),
            sa.text("(COALESCE(model_family, ''))"),
        ],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_rate_buckets_lookup",
        "llm_rate_buckets",
        ["scope", "scope_key", "unit"],
        schema=SCHEMA,
    )

    op.create_table(
        "llm_dispatch_leases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lease_id", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(200), nullable=False),
        sa.Column("model_family", sa.String(64), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("lease_id", name="uq_llm_dispatch_leases_identity"),
        *[
            sa.CheckConstraint(cond, name=name)
            for name, cond in LEASE_CHECKS
        ],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_dispatch_leases_scope",
        "llm_dispatch_leases",
        ["scope", "scope_key", "model_family", "expires_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_dispatch_leases_reclaim",
        "llm_dispatch_leases",
        ["expires_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_dispatch_leases_reclaim",
        table_name="llm_dispatch_leases",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_llm_dispatch_leases_scope",
        table_name="llm_dispatch_leases",
        schema=SCHEMA,
    )
    op.drop_table("llm_dispatch_leases", schema=SCHEMA)
    op.drop_index("ix_llm_rate_buckets_lookup", table_name="llm_rate_buckets", schema=SCHEMA)
    op.drop_index("uq_llm_rate_buckets_scope", table_name="llm_rate_buckets", schema=SCHEMA)
    op.drop_table("llm_rate_buckets", schema=SCHEMA)
