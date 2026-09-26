"""unified-llm-provider-model

Refactors LLM provider/model tables to match the single-provider-per-type
architecture described in AGENTS.md:

llm_providers:
  - rename api_url → base_url (and clean known endpoint suffixes)
  - add api_format (openai|anthropic), auth_header, encrypted_api_key, is_default
  - drop config JSON column; keep api_key_env as deprecated transitional column

llm_models:
  - add model_id (API model string, defaults from name), model_type (text|image|embedding)
  - add max_tokens, default_temperature as explicit columns
  - rename input_cost → input_cost_per_1k, output_cost → output_cost_per_1k
  - migrate cost values (old was per 1M → /1000 → per 1K)
  - drop capabilities JSON list and config JSON column

Revision ID: 0045
Revises: 0044
"""
from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    # ──────────────────────────────────────────────
    # llm_providers
    # ──────────────────────────────────────────────

    # 1.  Rename api_url → base_url
    op.alter_column("llm_providers", "api_url", new_column_name="base_url", schema="social_manager")

    # 2.  Clean endpoint suffixes so the column stores a true base URL
    op.execute("""
        UPDATE social_manager.llm_providers
           SET base_url = regexp_replace(base_url, '/chat/completions$', '')
         WHERE base_url LIKE '%/chat/completions'
    """)
    op.execute("""
        UPDATE social_manager.llm_providers
           SET base_url = regexp_replace(base_url, '/messages$', '')
         WHERE base_url LIKE '%/messages'
    """)

    # 3.  Add new columns
    op.add_column(
        "llm_providers",
        sa.Column("api_format", sa.String(20), nullable=False, server_default="openai"),
        schema="social_manager",
    )
    op.add_column(
        "llm_providers",
        sa.Column("auth_header", sa.String(200), nullable=True),
        schema="social_manager",
    )
    op.add_column(
        "llm_providers",
        sa.Column("encrypted_api_key", sa.Text, nullable=True),
        schema="social_manager",
    )
    op.add_column(
        "llm_providers",
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        schema="social_manager",
    )

    # 4.  Tag known Anthropic providers
    op.execute("""
        UPDATE social_manager.llm_providers
           SET api_format = 'anthropic'
         WHERE lower(name) = 'anthropic'
    """)

    # 5.  Drop old config column
    op.drop_column("llm_providers", "config", schema="social_manager")

    # ──────────────────────────────────────────────
    # llm_models
    # ──────────────────────────────────────────────

    # 1.  Rename cost columns first (before adding new ones to avoid conflicts)
    op.alter_column("llm_models", "input_cost", new_column_name="input_cost_per_1k", schema="social_manager")
    op.alter_column("llm_models", "output_cost", new_column_name="output_cost_per_1k", schema="social_manager")

    # 2.  Convert cost values from per‑1M to per‑1K
    op.execute("""
        UPDATE social_manager.llm_models
           SET input_cost_per_1k = input_cost_per_1k / 1000,
               output_cost_per_1k = output_cost_per_1k / 1000
         WHERE input_cost_per_1k > 10 OR output_cost_per_1k > 10
    """)

    # 3.  Add new columns (nullable first, backfill, then NOT NULL)
    op.add_column(
        "llm_models",
        sa.Column("model_id", sa.String(100), nullable=True),
        schema="social_manager",
    )
    op.execute("""
        UPDATE social_manager.llm_models
           SET model_id = name
         WHERE model_id IS NULL
    """)
    op.alter_column("llm_models", "model_id", nullable=False, schema="social_manager")

    op.add_column(
        "llm_models",
        sa.Column("model_type", sa.String(20), nullable=False, server_default="text"),
        schema="social_manager",
    )

    op.add_column(
        "llm_models",
        sa.Column("max_tokens", sa.Integer, nullable=False, server_default=sa.text("4096")),
        schema="social_manager",
    )
    op.add_column(
        "llm_models",
        sa.Column("default_temperature", sa.Float, nullable=False, server_default=sa.text("0.3")),
        schema="social_manager",
    )

    # 4.  Drop old JSON columns
    op.drop_column("llm_models", "capabilities", schema="social_manager")
    op.drop_column("llm_models", "config", schema="social_manager")


def downgrade():
    # ──────────────────────────────────────────────
    # llm_models — reverse
    # ──────────────────────────────────────────────
    op.add_column(
        "llm_models",
        sa.Column("config", sa.JSON, nullable=True, server_default=sa.text("'{}'::json")),
        schema="social_manager",
    )
    op.add_column(
        "llm_models",
        sa.Column("capabilities", sa.JSON, nullable=False, server_default=sa.text("'[\"text\"]'::json")),
        schema="social_manager",
    )
    op.drop_column("llm_models", "default_temperature", schema="social_manager")
    op.drop_column("llm_models", "max_tokens", schema="social_manager")
    op.drop_column("llm_models", "model_type", schema="social_manager")
    op.drop_column("llm_models", "model_id", schema="social_manager")

    op.alter_column("llm_models", "input_cost_per_1k", new_column_name="input_cost", schema="social_manager")
    op.alter_column("llm_models", "output_cost_per_1k", new_column_name="output_cost", schema="social_manager")

    # ──────────────────────────────────────────────
    # llm_providers — reverse
    # ──────────────────────────────────────────────
    op.add_column(
        "llm_providers",
        sa.Column("config", sa.JSON, nullable=True, server_default=sa.text("'{}'::json")),
        schema="social_manager",
    )
    op.drop_column("llm_providers", "is_default", schema="social_manager")
    op.drop_column("llm_providers", "encrypted_api_key", schema="social_manager")
    op.drop_column("llm_providers", "auth_header", schema="social_manager")
    op.drop_column("llm_providers", "api_format", schema="social_manager")
    op.alter_column("llm_providers", "base_url", new_column_name="api_url", schema="social_manager")