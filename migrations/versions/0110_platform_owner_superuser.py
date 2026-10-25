"""Platform-owner role `superuser` and the owner account.

The platform keeps exactly one owner account, promoted here from `admin`. The
CHECK on `roles.codename` admits the new codename, the catalog gains the row, and
the owner account moves onto it at the same time.

`-x superuser_user_id=<id>` pins the reviewed account. It is optional on purpose:
the deploy path is a shell on the production host, so the person running the
upgrade already holds that access. Without the argument the migration promotes
the sole active `admin` and refuses when there is none or when several exist —
it never picks an account by name, email or "first admin".

Definition only: this revision does not deploy, and applying it needs separate
authorization for the target schema.

Revision ID: 0110
Revises: 0109
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA

CODENAMES = "('user', 'operator', 'admin', 'superuser')"
OWNER_DESCRIPTION = "Собственник платформы: полный доступ ко всем данным"


def _schema_objects(bind):
    q = bind.dialect.identifier_preparer.quote
    schema = q(SCHEMA)
    return schema, f"{schema}.roles", f"{schema}.users"


def upgrade():
    bind = op.get_bind()
    schema, roles, users = _schema_objects(bind)

    op.drop_constraint("ck_platform_role_codename", "roles", type_="check", schema=SCHEMA)
    op.create_check_constraint("ck_platform_role_codename", "roles", f"codename IN {CODENAMES}", schema=SCHEMA)
    if bind.execute(sa.text(f"SELECT count(*) FROM {roles} WHERE codename='superuser'")).scalar_one() == 0:
        bind.execute(
            sa.text(f"INSERT INTO {roles} (codename, description) VALUES ('superuser', :description)"),
            {"description": OWNER_DESCRIPTION},
        )

    # Promote the owner together with the catalog row, so the revision never
    # lands with `superuser` present but nobody holding it.
    owner = context.get_x_argument(as_dictionary=True).get("superuser_user_id")
    if owner is not None:
        if not owner.isdigit():
            raise RuntimeError("Reviewed owner id (-x superuser_user_id) must be numeric")
        owner_id = int(owner)
        current = bind.execute(
            sa.text(
                f"SELECT r.codename FROM {users} u JOIN {roles} r ON r.id=u.role_id "
                "WHERE u.id=:owner AND u.is_active=true"
            ),
            {"owner": owner_id},
        ).scalar_one_or_none()
        if current is None:
            raise RuntimeError(f"Reviewed owner account {owner_id} must exist and be active")
        if current == "superuser":
            return
        if current != "admin":
            raise RuntimeError(f"Reviewed owner account {owner_id} must already hold the admin role, got {current!r}")
    else:
        admins = (
            bind.execute(
                sa.text(
                    f"SELECT id FROM {users} WHERE is_active=true AND role_id=(SELECT id FROM {roles} WHERE codename='admin')"
                )
            )
            .scalars()
            .all()
        )
        if not admins:
            raise RuntimeError(
                "No active admin to promote to the platform owner: create one or pass -x superuser_user_id=<id>"
            )
        if len(admins) > 1:
            raise RuntimeError(
                f"{len(admins)} active admins ({sorted(admins)}): pass -x superuser_user_id=<id> to name the owner"
            )
        owner_id = admins[0]
    bind.execute(
        sa.text(f"UPDATE {users} SET role_id=(SELECT id FROM {roles} WHERE codename='superuser') WHERE id=:owner"),
        {"owner": owner_id},
    )


def downgrade():
    bind = op.get_bind()
    _, roles, users = _schema_objects(bind)
    # Give any owner account the previous top role before the codename disappears.
    bind.execute(
        sa.text(
            f"UPDATE {users} SET role_id=(SELECT id FROM {roles} WHERE codename='admin') "
            f"WHERE role_id IN (SELECT id FROM {roles} WHERE codename='superuser')"
        )
    )
    bind.execute(sa.text(f"DELETE FROM {roles} WHERE codename='superuser'"))
    op.drop_constraint("ck_platform_role_codename", "roles", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        "ck_platform_role_codename", "roles", "codename IN ('user', 'operator', 'admin')", schema=SCHEMA
    )
