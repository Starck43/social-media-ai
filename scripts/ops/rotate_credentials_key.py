"""Rotate the shared Fernet key across all persisted credential stores.

Default is a read/verify dry run. Apply requires a reviewed backup and every
writer stopped until the new key is installed on all instances. Supply the old
key via CREDENTIALS_KEY and the new key via P12T14_NEW_CREDENTIALS_KEY in the
operator's protected environment; never pass key values on a command line.
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sys

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken


class RotationError(RuntimeError):
    """A bounded error which never contains key, ciphertext or plaintext."""


def _fernet(key: str | None) -> Fernet:
    if not key:
        raise RotationError("Both old and new credential keys are required")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        raise RotationError("A credential key is invalid") from None


def rotate_in_transaction(
    conn, *, schema: str, old_key: str, new_key: str, apply: bool, allow_private_results_purge: bool = False
) -> dict[str, int]:
    """Validate/re-encrypt all known stores within a caller-owned transaction.

    This administrative operation assumes a maintenance window: database locks
    protect the transaction, not the subsequent process-configuration switch.
    Failure must propagate to the caller, which rolls the transaction back.
    """
    if not conn.in_transaction():
        raise RotationError("Rotation requires a transaction")
    if conn.engine.echo or conn.engine.logger.isEnabledFor(logging.INFO):
        raise RotationError("Disable SQL and result logging before rotation")
    if not isinstance(schema, str) or not schema or old_key == new_key:
        raise RotationError("A valid schema and two different keys are required")
    old, new = _fernet(old_key), _fernet(new_key)
    if base64.urlsafe_b64decode(old_key.encode("ascii")) == base64.urlsafe_b64decode(new_key.encode("ascii")):
        raise RotationError("Old and new keys must be different")
    quote = conn.dialect.identifier_preparer.quote
    schema_sql = conn.dialect.identifier_preparer.quote_schema(schema)
    qualified = lambda name: f"{schema_sql}.{quote(name)}"  # noqa: E731
    # Fixed global order protects both reads and writes across all processes
    # during this transaction. Services must remain stopped after commit.
    conn.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
    revisions = conn.exec_driver_sql(f"SELECT version_num FROM {qualified('alembic_version')}").scalars().all()
    # Explicit schema generations: a future migration may add another encrypted
    # store. Never silently rotate only the stores known to an older utility.
    generations = {
        "0124": (False, False, False),
        "p12t14_accounts": (True, False, False),
        "p12t14_tg_login": (True, True, False),
        "p12t14_tg_runtime": (True, True, False),
        "p12t14_tg_recovery": (True, True, False),
        "p12t14_tg_retention": (True, True, True),
        "p12t12_catalog": (True, True, True),
        "0125": (True, True, True),
        "0126": (True, True, True),
        "0127": (True, True, True),
        "0128": (True, True, True),
    }
    if len(revisions) != 1 or revisions[0] not in generations:
        raise RotationError("Apply the reviewed encrypted-refresh migration first and use a supported single head")
    inspector = sa.inspect(conn)
    has_authorizations = inspector.has_table("personal_connector_authorizations", schema=schema)
    has_logins = inspector.has_table("telegram_login_attempts", schema=schema)
    has_keys = inspector.has_table("telegram_login_request_keys", schema=schema)
    if (has_authorizations, has_logins, has_keys) != generations[revisions[0]]:
        raise RotationError("Credential store layout does not match the reviewed migration head")
    private_tables = ("file_insight_mounts", "file_insight_object_states", "file_insight_attempts", "file_private_insights")
    private_layout = tuple(inspector.has_table(name, schema=schema) for name in private_tables)
    has_private_results = revisions[0] == "0128"
    if private_layout != (has_private_results,) * len(private_tables):
        raise RotationError("Private result store layout does not match the reviewed migration head")
    names = ["llm_providers", "user_credentials", "workspace_connection_secrets"]
    if has_authorizations:
        names.append("personal_connector_authorizations")
    if has_logins:
        names.append("telegram_login_attempts")
    if has_private_results:
        names.extend(private_tables)
    for name in names:
        conn.exec_driver_sql(f"LOCK TABLE {qualified(name)} IN ACCESS EXCLUSIVE MODE")
    private_counts = {}
    if has_private_results:
        for label, name in (
            ("private_consent_mounts", "file_insight_mounts"),
            ("private_attempts", "file_insight_attempts"),
            ("private_results", "file_private_insights"),
        ):
            private_counts[label] = conn.exec_driver_sql(f"SELECT count(*) FROM {qualified(name)}").scalar_one()
        # Connection-secret re-encryption intentionally uses the normal revoke
        # trigger. Never disable that fence or silently lose retained results.
        if apply and private_counts["private_consent_mounts"] and allow_private_results_purge is not True:
            raise RotationError("Private consent and result purge requires explicit approval before rotation")
    guard = conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conname = 'ck_user_credentials_vk_refresh_secret' AND convalidated "
            "AND conrelid = to_regclass(:qualified_table))"
        ),
        {"qualified_table": qualified("user_credentials")},
    ).scalar_one()
    if not guard:
        raise RotationError("The encrypted-refresh database guard is missing")
    has_legacy = conn.exec_driver_sql(
        f"SELECT EXISTS (SELECT 1 FROM {qualified('user_credentials')} "
        "WHERE platform = 'vk' AND kind = 'user_token' "
        "AND meta IS NOT NULL AND CAST(meta AS jsonb) ? 'refresh_token')"
    ).scalar_one()
    if has_legacy:
        raise RotationError("Legacy plaintext refresh metadata remains; rotation refused")

    metadata = sa.MetaData()
    users = sa.Table(
        "user_credentials",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("secret_encrypted", sa.Text),
        sa.Column("refresh_secret_encrypted", sa.Text),
        schema=schema,
    )
    connections = sa.Table(
        "workspace_connection_secrets",
        metadata,
        sa.Column("connection_id", sa.Integer, primary_key=True),
        sa.Column("secret_encrypted", sa.Text),
        schema=schema,
    )
    providers = sa.Table(
        "llm_providers",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("encrypted_api_key", sa.Text),
        schema=schema,
    )
    stores = (
        ("personal_access", users, users.c.id, users.c.secret_encrypted, False, False),
        ("personal_refresh", users, users.c.id, users.c.refresh_secret_encrypted, True, False),
        (
            "workspace_connections",
            connections,
            connections.c.connection_id,
            connections.c.secret_encrypted,
            False,
            True,
        ),
        ("llm_providers", providers, providers.c.id, providers.c.encrypted_api_key, True, True),
    )
    if has_authorizations:
        authorizations = sa.Table(
            "personal_connector_authorizations",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("secret_encrypted", sa.Text),
            schema=schema,
        )
        stores += (
            (
                "personal_authorizations",
                authorizations,
                authorizations.c.id,
                authorizations.c.secret_encrypted,
                False,
                True,
            ),
        )
    if has_logins:
        logins = sa.Table(
            "telegram_login_attempts",
            metadata,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("secret_encrypted", sa.Text),
            schema=schema,
        )
        stores += (("telegram_login_attempts", logins, logins.c.id, logins.c.secret_encrypted, False, True),)
    counts = {}
    for label, table, identity, column, allow_none, allow_empty in stores:
        count = 0
        for row in conn.execute(sa.select(identity, column)):
            row_id, ciphertext = row
            if ciphertext is None:
                if allow_none:
                    continue
                raise RotationError("A required encrypted credential is missing")
            if ciphertext == "":
                if allow_empty:
                    continue
                raise RotationError("A required encrypted credential is empty")
            if not isinstance(ciphertext, str):
                raise RotationError("An encrypted credential has an invalid type")
            try:
                plaintext = old.decrypt(ciphertext.encode("ascii"))
                replacement = new.encrypt(plaintext).decode("ascii")
                # Verify every generated ciphertext before writing.
                if new.decrypt(replacement.encode("ascii")) != plaintext:
                    raise RotationError("Credential re-encryption verification failed")
            except (InvalidToken, ValueError, TypeError, UnicodeError):
                raise RotationError("A credential cannot be decrypted with the old key") from None
            if apply:
                conn.execute(table.update().where(identity == row_id).values({column.name: replacement}))
            count += 1
        counts[label] = count
    if not any(counts.values()):
        raise RotationError("No encrypted credentials are available to verify the old key")
    if apply and has_private_results and private_counts["private_consent_mounts"]:
        # Include inactive mounts or mounts whose secret was already removed:
        # every old-key envelope must be gone before the key switch. This is
        # the same normal invalidation fence, never a disabled trigger.
        conn.exec_driver_sql(
            f"SELECT {qualified('invalidate_file_insights')}(NULL::integer, NULL::integer, NULL::integer)"
        )
    counts.update(private_counts)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify or rotate encrypted credentials in a maintenance window.")
    parser.add_argument(
        "--target-approved", action="store_true", help="Confirm inspection of this target is authorized"
    )
    parser.add_argument("--apply", action="store_true", help="Apply rotation instead of the default dry run")
    parser.add_argument("--backup-confirmed", action="store_true", help="Confirm a reviewed restorable backup exists")
    parser.add_argument("--writers-stopped", action="store_true", help="Confirm all application writers are stopped")
    parser.add_argument(
        "--private-results-purge-approved",
        action="store_true",
        help="Approve clearing private file consent and retained results during credential rotation",
    )
    args = parser.parse_args(argv)
    if not args.target_approved:
        parser.error("--target-approved is required, including for a dry run")
    if not args.writers_stopped:
        parser.error("--writers-stopped is required, including for a dry run")
    if args.apply and not args.backup_confirmed:
        parser.error("--apply requires --backup-confirmed")
    from app.core.config import settings
    from app.core.database import engine

    try:
        with engine.begin() as conn:
            counts = rotate_in_transaction(
                conn,
                schema=settings.DB_SCHEMA,
                old_key=settings.CREDENTIALS_KEY,
                new_key=os.environ.get("P12T14_NEW_CREDENTIALS_KEY"),
                apply=args.apply,
                allow_private_results_purge=args.private_results_purge_approved,
            )
    except Exception:  # Do not format exception text or parameters containing secrets.
        print(
            "Credential rotation did not complete; verify transaction outcome before restarting services.",
            file=sys.stderr,
        )
        return 1
    mode = "applied" if args.apply else "dry run"
    print(f"Credential rotation {mode}: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
    if args.apply:
        print("Keep writers stopped until the new key is installed and verified on every instance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
