"""Retain the accepted P22-T06 migration identity and advance the numeric head.

The schema bridge was already delivered under ``p22t06_legacy_identity``. This
revision is intentionally empty: databases stamped at that accepted revision
advance without replaying its DDL.
"""

revision = "0162"
down_revision = "p22t06_legacy_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Advance databases that already carry the accepted schema bridge."""


def downgrade() -> None:
    """Return to the accepted P22-T06 revision without dropping its tables."""
