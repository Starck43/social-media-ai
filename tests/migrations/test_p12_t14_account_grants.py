"""Actual new revision DDL in a separate rollback-only synthetic schema.

This does not pretend that a model-created predecessor proves the full chain.
The outer test command additionally exercises real Alembic upgrade/downgrade on
this branch's separately prepared synthetic test_schema.
"""

import importlib.util
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import engine

pytestmark = pytest.mark.tenancy


def test_new_revision_constraints_and_rollback_preserve_legacy_rows():
    schema = "test_p12t14_reuse_" + secrets.token_hex(6)
    spec = importlib.util.spec_from_file_location(
        "_p12t14_grants_migration",
        Path(__file__).resolve().parents[2] / "migrations/versions/p12t14_connector_authorizations.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.settings = SimpleNamespace(DB_SCHEMA=schema)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.schema.CreateSchema(schema))
            # Minimal predecessor identities and an untouched legacy-vault sentinel.
            connection.execute(sa.text(f'CREATE TABLE "{schema}".users (id integer primary key)'))
            connection.execute(sa.text(f'CREATE TABLE "{schema}".tenants (id integer primary key)'))
            connection.execute(
                sa.text(f'CREATE TABLE "{schema}".user_credentials (id integer primary key, secret text)')
            )
            connection.execute(sa.text(f'INSERT INTO "{schema}".users VALUES (1),(2)'))
            connection.execute(sa.text(f'INSERT INTO "{schema}".tenants VALUES (1),(2)'))
            connection.execute(sa.text(f"INSERT INTO \"{schema}\".user_credentials VALUES (1,'synthetic-untouched')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names(schema=schema)) == {
                "users",
                "tenants",
                "user_credentials",
                "personal_connector_authorizations",
                "workspace_authorization_grants",
            }
            metadata = sa.MetaData(schema=schema)
            metadata.reflect(connection)
            accounts = metadata.tables[f"{schema}.personal_connector_authorizations"]
            grants = metadata.tables[f"{schema}.workspace_authorization_grants"]
            connection.execute(
                accounts.insert(),
                dict(
                    id=1,
                    owner_user_id=1,
                    provider_id="telegram",
                    auth_method="mtproto",
                    subject_key="123",
                    secret_encrypted="synthetic-ciphertext",
                ),
            )
            connection.execute(
                grants.insert(), dict(id=1, tenant_id=1, owner_user_id=1, authorization_id=1, authorization_revision=1)
            )
            assert set(
                c["name"] for c in inspector.get_unique_constraints("personal_connector_authorizations", schema=schema)
            ) == {"uq_personal_connector_subject", "uq_personal_connector_owner"}
            assert {
                c["name"] for c in inspector.get_check_constraints("workspace_authorization_grants", schema=schema)
            } == {"ck_workspace_authorization_revision"}
            for values in (
                dict(id=2, tenant_id=2, owner_user_id=2, authorization_id=1, authorization_revision=1),
                dict(id=2, tenant_id=1, owner_user_id=1, authorization_id=1, authorization_revision=1),
                dict(id=2, tenant_id=2, owner_user_id=1, authorization_id=1, authorization_revision=0),
            ):
                with pytest.raises(sa.exc.IntegrityError):
                    with connection.begin_nested():
                        connection.execute(grants.insert(), values)
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert set(sa.inspect(connection).get_table_names(schema=schema)) == {
                "users",
                "tenants",
                "user_credentials",
            }
            assert (
                connection.execute(sa.text(f'SELECT secret FROM "{schema}".user_credentials')).scalar_one()
                == "synthetic-untouched"
            )
        finally:
            transaction.rollback()
        assert (
            connection.execute(
                sa.text("SELECT count(*) FROM pg_namespace WHERE nspname=:schema"), {"schema": schema}
            ).scalar_one()
            == 0
        )
