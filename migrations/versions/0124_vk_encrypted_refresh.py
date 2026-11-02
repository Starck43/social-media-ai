"""Encrypt VK refresh secrets and forbid plaintext metadata writes.

Revision ID: 0124
Revises: 0123

This is an online, transactional data migration. Stop old application writers
before applying it; old binaries cannot write VK credentials after the guard is
installed. Never run with SQL/result logging enabled. Public apply and backups
require separate owner authorization. Old database pages and backups may still
contain previous plaintext and need separate owner-managed retention handling.
"""

import logging

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings
from app.utils.crypto import decrypt_secret, encrypt_secret

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_user_credentials_vk_refresh_secret"


def _table():
    return sa.table(
        "user_credentials",
        sa.column("id", sa.Integer),
        sa.column("platform", sa.String),
        sa.column("kind", sa.String),
        sa.column("meta", sa.JSON),
        sa.column("secret_encrypted", sa.Text),
        sa.column("refresh_secret_encrypted", sa.Text),
        schema=settings.DB_SCHEMA,
    )


def _connection():
    if context.is_offline_mode():
        raise RuntimeError("VK refresh migration requires an online transaction")
    bind = op.get_bind()
    if bind.engine.echo or logging.getLogger("sqlalchemy.engine").isEnabledFor(logging.DEBUG):
        raise RuntimeError("Disable SQL and result logging before VK refresh migration")
    # Freeze old and new writers until all rows and the check constraint commit.
    quote = bind.dialect.identifier_preparer.quote
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    bind.exec_driver_sql(f"LOCK TABLE {schema}.{quote('user_credentials')} IN ACCESS EXCLUSIVE MODE")
    return bind


def upgrade():
    bind = _connection()
    op.add_column(
        "user_credentials", sa.Column("refresh_secret_encrypted", sa.Text(), nullable=True), schema=settings.DB_SCHEMA
    )
    table = _table()
    rows = bind.execute(
        sa.select(table.c.id, table.c.meta, table.c.secret_encrypted).where(
            table.c.platform == "vk", table.c.kind == "user_token"
        )
    )
    for row in rows:
        if row.meta is not None and not isinstance(row.meta, dict):
            raise RuntimeError("Malformed VK credential metadata; migration aborted")
        meta = dict(row.meta or {})
        refresh = meta.pop("refresh_token", None)
        if refresh is not None and not isinstance(refresh, str):
            raise RuntimeError("Malformed VK refresh secret; migration aborted")
        if refresh:
            # Reject a wrong key before encrypting anything under it. The access
            # token and refresh token must remain readable by the same key.
            try:
                decrypt_secret(row.secret_encrypted)
                encrypted = encrypt_secret(refresh)
            except (ValueError, RuntimeError, TypeError) as exc:
                raise RuntimeError("Cannot validate or encrypt VK credential; migration aborted") from exc
        else:
            encrypted = None
        meta["refresh_available"] = bool(encrypted)
        # Only sanitized metadata and ciphertext are SQL parameters.
        bind.execute(table.update().where(table.c.id == row.id).values(meta=meta, refresh_secret_encrypted=encrypted))
    op.create_check_constraint(
        CONSTRAINT,
        "user_credentials",
        "platform <> 'vk' OR kind <> 'user_token' OR ("
        "(meta IS NULL OR NOT (CAST(meta AS jsonb) ? 'refresh_token')) AND "
        "((COALESCE(CAST(meta AS jsonb) -> 'refresh_available', 'false'::jsonb) = 'true'::jsonb) "
        "= (refresh_secret_encrypted IS NOT NULL)))",
        schema=settings.DB_SCHEMA,
    )


def downgrade():
    bind = _connection()
    table = _table()
    if bind.execute(sa.select(sa.exists().where(table.c.refresh_secret_encrypted.is_not(None)))).scalar():
        raise RuntimeError("Downgrade would discard encrypted refresh credentials; use the approved recovery plan")
    op.drop_constraint(CONSTRAINT, "user_credentials", schema=settings.DB_SCHEMA, type_="check")
    rows = bind.execute(
        sa.select(table.c.id, table.c.meta).where(table.c.platform == "vk", table.c.kind == "user_token")
    )
    for row in rows:
        if isinstance(row.meta, dict) and "refresh_available" in row.meta:
            meta = dict(row.meta)
            meta.pop("refresh_available", None)
            bind.execute(table.update().where(table.c.id == row.id).values(meta=meta))
    op.drop_column("user_credentials", "refresh_secret_encrypted", schema=settings.DB_SCHEMA)
