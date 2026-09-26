"""migrate monitored_users to params + drop source_user_relationships

Revision ID: 0058
Revises: 0057
Create Date: 2026-09-26

Migrates monitored_users from the junction table into Source.params["monitored_users"]
as a JSON array of external_id (username) strings, then drops the junction table.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Aggregate existing relationships into params["monitored_users"]
    # For each source_id, collect all external_ids of the user sources it tracks
    op.execute("""
        WITH pairs AS (
            SELECT sur.source_id, s.external_id
            FROM social_manager.source_user_relationships sur
            JOIN social_manager.sources s ON s.id = sur.user_id
        ),
        aggregated AS (
            SELECT source_id, jsonb_agg(external_id ORDER BY external_id) AS usernames
            FROM pairs
            GROUP BY source_id
        )
        UPDATE social_manager.sources src
        SET params = (
            jsonb_set(
                COALESCE(params::jsonb, '{}'::jsonb),
                '{monitored_users}',
                ag.usernames
            )
        )::json
        FROM aggregated ag
        WHERE src.id = ag.source_id
    """)

    # Step 2: Drop the junction table
    op.drop_table("source_user_relationships", schema="social_manager")


def downgrade() -> None:
    # Step 1: Recreate the junction table
    op.create_table(
        "source_user_relationships",
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["social_manager.sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["social_manager.sources.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_id", "user_id"),
        schema="social_manager",
    )

    # Step 2: Reverse — split params["monitored_users"] back into rows
    # This is lossy if multiple sources tracked the same user (we can't know which
    # source_id each username belonged to), so we do our best:
    # For each source that has params["monitored_users"], find the user sources
    # matching those external_ids and create relationships.
    op.execute("""
        INSERT INTO social_manager.source_user_relationships (source_id, user_id)
        SELECT
            src.id AS source_id,
            us.id AS user_id
        FROM social_manager.sources src
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(src.params::jsonb->'monitored_users', '[]'::jsonb)
        ) AS mu(external_id)
        JOIN social_manager.sources us ON us.external_id = mu.external_id
        WHERE us.source_type = 'USER'
          AND (src.params->'monitored_users' IS NOT NULL
               AND jsonb_array_length(src.params->'monitored_users') > 0)
        ON CONFLICT DO NOTHING
    """)
