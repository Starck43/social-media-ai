"""Default-off private consent, read-attempt ledger and encrypted retention.

Revision ID: 0128
Revises: 0127
No backfill, consent grant, provider activation, scheduler or public-route change.
Deploy matching manager code; validate upgrade/downgrade on an exclusive test schema.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None


def _common():
    s = settings.DB_SCHEMA
    return [
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _mount_fk(s):
    return sa.ForeignKeyConstraint(
        ["tenant_id", "connection_id"],
        [f"{s}.file_insight_mounts.tenant_id", f"{s}.file_insight_mounts.connection_id"],
        ondelete="CASCADE",
    )


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "file_insight_mounts",
        *_common(),
        sa.Column("connection_id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retention_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        *(sa.Column(n, sa.String(32), nullable=False) for n in ("owner_epoch", "connection_epoch", "consent_revision")),
        sa.Column("policy_revision", sa.String(64), nullable=False),
        sa.Column("clock_highwater", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "connection_id", name="uq_file_insight_mount_scope"),
        sa.CheckConstraint("NOT retention_allowed OR processing_allowed", name="ck_file_insight_consent"),
        schema=s,
    )
    op.create_table(
        "file_insight_object_states",
        *_common(),
        sa.Column("object_id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("epoch", sa.String(32), nullable=False),
        _mount_fk(s),
        sa.ForeignKeyConstraint(
            ["tenant_id", "object_id"],
            [f"{s}.file_catalog_objects.tenant_id", f"{s}.file_catalog_objects.id"],
            ondelete="CASCADE",
        ),
        schema=s,
    )
    op.create_table(
        "file_insight_attempts",
        *_common(),
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column(
            "object_id",
            sa.Integer(),
            sa.ForeignKey(f"{s}.file_insight_object_states.object_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("binding_data", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("generation_id", sa.String(32), nullable=True),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.false()),
        _mount_fk(s),
        sa.CheckConstraint("expires_at = captured_at + interval '24 hours'", name="ck_file_insight_attempt_expiry"),
        sa.CheckConstraint(
            "jsonb_typeof(binding_data) = 'object' AND octet_length(binding_data::text) <= 4096",
            name="ck_file_insight_attempt_binding",
        ),
        sa.CheckConstraint(
            "provenance IS NULL OR (jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 4096)",
            name="ck_file_insight_attempt_provenance",
        ),
        schema=s,
    )
    op.create_index("ix_file_insight_attempt_expiry", "file_insight_attempts", ["tenant_id", "expires_at"], schema=s)
    op.create_index(
        "ix_file_insight_attempt_scope", "file_insight_attempts", ["tenant_id", "connection_id", "object_id"], schema=s
    )
    op.create_table(
        "file_private_insights",
        *_common(),
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.String(32),
            sa.ForeignKey(f"{s}.file_insight_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column(
            "object_id", sa.Integer(), sa.ForeignKey(f"{s}.file_catalog_objects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("charged_size", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _mount_fk(s),
        sa.UniqueConstraint("attempt_id", name="uq_file_private_insight_attempt"),
        sa.CheckConstraint("charged_size BETWEEN 1 AND 65536", name="ck_file_private_insight_charge"),
        sa.CheckConstraint("octet_length(payload_encrypted) <= 90000", name="ck_file_private_insight_cipher"),
        sa.CheckConstraint("expires_at = captured_at + interval '24 hours'", name="ck_file_private_insight_expiry"),
        schema=s,
    )
    op.create_index(
        "ix_file_private_insight_quota",
        "file_private_insights",
        ["tenant_id", "owner_id", "connection_id", "object_id"],
        schema=s,
    )
    op.create_index("ix_file_private_insight_expiry", "file_private_insights", ["tenant_id", "expires_at"], schema=s)
    # Trigger changes are in the SAME transaction as the underlying revocation.
    # Parent row locks taken by existing authority serialize active admissions.
    op.execute(f"""
    CREATE FUNCTION "{s}".invalidate_file_insights(t integer, o integer, c integer)
    RETURNS void LANGUAGE plpgsql AS $$
    DECLARE r record;
    BEGIN
      FOR r IN SELECT connection_id FROM "{s}".file_insight_mounts
        WHERE (t IS NULL OR tenant_id=t) AND (o IS NULL OR owner_id=o)
          AND (c IS NULL OR connection_id=c) ORDER BY connection_id FOR UPDATE
      LOOP
        UPDATE "{s}".file_insight_mounts SET processing_allowed=false, retention_allowed=false,
          owner_epoch=replace(gen_random_uuid()::text,'-',''),
          connection_epoch=replace(gen_random_uuid()::text,'-',''),
          consent_revision=replace(gen_random_uuid()::text,'-','') WHERE connection_id=r.connection_id;
        DELETE FROM "{s}".file_insight_attempts WHERE connection_id=r.connection_id;
      END LOOP;
    END; $$;
    CREATE FUNCTION "{s}".file_insight_revoke_hook() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME='workspace_connections' THEN
        PERFORM "{s}".invalidate_file_insights(OLD.tenant_id,NULL,OLD.id);
      ELSIF TG_TABLE_NAME='workspace_connection_secrets' THEN
        IF TG_OP='INSERT' THEN
          PERFORM "{s}".invalidate_file_insights(NULL,NULL,NEW.connection_id);
        ELSE
          PERFORM "{s}".invalidate_file_insights(NULL,NULL,OLD.connection_id);
        END IF;
      ELSIF TG_TABLE_NAME='tenant_users' THEN
        PERFORM "{s}".invalidate_file_insights(OLD.tenant_id,OLD.user_id,NULL);
      ELSIF TG_TABLE_NAME='users' THEN
        PERFORM "{s}".invalidate_file_insights(NULL,OLD.id,NULL);
      ELSIF TG_TABLE_NAME='tenants' THEN
        PERFORM "{s}".invalidate_file_insights(OLD.id,NULL,NULL);
      ELSIF TG_TABLE_NAME='workspace_roles' THEN
        PERFORM "{s}".invalidate_file_insights(m.tenant_id,m.user_id,NULL)
          FROM "{s}".tenant_users m WHERE m.role_id=OLD.id;
      ELSIF TG_TABLE_NAME='workspace_role_permission' THEN
        IF TG_OP='INSERT' THEN
          PERFORM "{s}".invalidate_file_insights(m.tenant_id,m.user_id,NULL)
            FROM "{s}".tenant_users m WHERE m.role_id=NEW.role_id;
        ELSE
          PERFORM "{s}".invalidate_file_insights(m.tenant_id,m.user_id,NULL)
            FROM "{s}".tenant_users m WHERE m.role_id=OLD.role_id;
        END IF;
      END IF;
      RETURN NULL;
    END; $$;
    CREATE FUNCTION "{s}".file_insight_object_hook() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      UPDATE "{s}".file_insight_object_states SET epoch=replace(gen_random_uuid()::text,'-','') WHERE object_id=OLD.id;
      DELETE FROM "{s}".file_insight_attempts WHERE object_id=OLD.id;
      RETURN NULL;
    END; $$;
    """)
    hooks = {
        "workspace_connections": "AFTER UPDATE OF tenant_id,owner_user_id,config,account_key,is_active,state,provider_id,platform,auth_method,schema_version",
        "workspace_connection_secrets": "AFTER INSERT OR UPDATE OR DELETE",
        "tenant_users": "AFTER DELETE OR UPDATE OF is_active,role_id,user_id,tenant_id,channel",
        "users": "AFTER UPDATE OF is_active,role_id",
        "tenants": "AFTER UPDATE OF is_active",
        "workspace_roles": "AFTER UPDATE OR DELETE",
        "workspace_role_permission": "AFTER INSERT OR UPDATE OR DELETE",
    }
    for table, events in hooks.items():
        op.execute(
            f'CREATE TRIGGER file_insight_revoke {events} ON "{s}"."{table}" FOR EACH ROW EXECUTE FUNCTION "{s}".file_insight_revoke_hook()'
        )
    op.execute(
        f'CREATE TRIGGER file_insight_object_changed AFTER UPDATE OF path,name,kind,root_key,path_key,observation_number ON "{s}".file_catalog_objects FOR EACH ROW EXECUTE FUNCTION "{s}".file_insight_object_hook()'
    )


def downgrade():
    s = settings.DB_SCHEMA
    op.execute(f'DROP TRIGGER file_insight_object_changed ON "{s}".file_catalog_objects')
    for table in (
        "workspace_connections",
        "workspace_connection_secrets",
        "tenant_users",
        "users",
        "tenants",
        "workspace_roles",
        "workspace_role_permission",
    ):
        op.execute(f'DROP TRIGGER file_insight_revoke ON "{s}"."{table}"')
    op.execute(f'DROP FUNCTION "{s}".file_insight_object_hook()')
    op.execute(f'DROP FUNCTION "{s}".file_insight_revoke_hook()')
    op.execute(f'DROP FUNCTION "{s}".invalidate_file_insights(integer,integer,integer)')
    for table in (
        "file_private_insights",
        "file_insight_attempts",
        "file_insight_object_states",
        "file_insight_mounts",
    ):
        op.drop_table(table, schema=s)
