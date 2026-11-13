"""Add monotonic personal-provider credential/configuration freshness.

Revision ID: p21t11_credential_version
Revises: p17t08_onec_history_status
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p21t11_credential_version"
down_revision = "p17t08_onec_history_status"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    quote = op.get_bind().dialect.identifier_preparer.quote_schema
    s = quote(schema)
    op.add_column(
        "llm_providers",
        sa.Column("credential_version", sa.BigInteger(), nullable=False, server_default="1"),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_llm_provider_credential_version", "llm_providers", "credential_version >= 1", schema=schema
    )
    op.execute(f"""
        CREATE FUNCTION {s}.guard_llm_credential_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.credential_version <> 1 THEN
                    RAISE EXCEPTION 'Credential version must start at one';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.credential_version IS DISTINCT FROM OLD.credential_version THEN
                IF pg_trigger_depth() = 2 AND NEW.credential_version = OLD.credential_version + 1 THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'Credential version is database managed';
            END IF;
            IF ROW(NEW.encrypted_api_key, NEW.base_url, NEW.auth_header, NEW.api_format, NEW.is_active, NEW.owner_user_id)
                IS DISTINCT FROM
                ROW(OLD.encrypted_api_key, OLD.base_url, OLD.auth_header, OLD.api_format, OLD.is_active, OLD.owner_user_id)
            THEN
                NEW.credential_version := OLD.credential_version + 1;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER guard_llm_credential_version BEFORE INSERT OR UPDATE ON {s}.llm_providers
            FOR EACH ROW EXECUTE FUNCTION {s}.guard_llm_credential_version();
        CREATE FUNCTION {s}.invalidate_llm_endpoint_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(NEW.custom_endpoint_path, NEW.model_id, NEW.is_active)
                IS DISTINCT FROM ROW(OLD.custom_endpoint_path, OLD.model_id, OLD.is_active)
            THEN
                UPDATE {s}.llm_providers SET credential_version = credential_version + 1
                    WHERE id = NEW.provider_id AND owner_user_id = NEW.owner_user_id;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER invalidate_llm_endpoint_version AFTER UPDATE ON {s}.llm_models
            FOR EACH ROW EXECUTE FUNCTION {s}.invalidate_llm_endpoint_version();
    """)


def downgrade():
    schema = settings.DB_SCHEMA
    s = op.get_bind().dialect.identifier_preparer.quote_schema(schema)
    # A rollback with used freshness metadata would erase the stale-request fence.
    if (
        op.get_bind()
        .execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {s}.llm_providers WHERE credential_version > 1)"))
        .scalar()
    ):
        raise RuntimeError("Credential freshness has been used; freeze dispatch before an approved rollback")
    op.execute(f"DROP TRIGGER invalidate_llm_endpoint_version ON {s}.llm_models")
    op.execute(f"DROP FUNCTION {s}.invalidate_llm_endpoint_version()")
    op.execute(f"DROP TRIGGER guard_llm_credential_version ON {s}.llm_providers")
    op.execute(f"DROP FUNCTION {s}.guard_llm_credential_version()")
    op.drop_constraint("ck_llm_provider_credential_version", "llm_providers", schema=schema)
    op.drop_column("llm_providers", "credential_version", schema=schema)
