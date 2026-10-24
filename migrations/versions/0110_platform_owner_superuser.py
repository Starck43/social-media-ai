"""Platform-owner role `superuser` and the reviewed owner account.

The platform keeps exactly one owner account, promoted here from `admin`. The
CHECK on `roles.codename` admits the new codename, the catalog gains the row, and
the owner account moves onto it at the same time. Nothing infers an owner: the
target account is supplied explicitly with `-x superuser_user_id=<id>` and must
already be an active `admin` — the migration never picks an account by name,
email or "first admin".

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

    owner = context.get_x_argument(as_dictionary=True).get("superuser_user_id")
    if owner is None:
        return
    if not owner.isdigit():
        raise RuntimeError("Reviewed owner id must be numeric")
    current = bind.execute(
        sa.text(
            f"SELECT r.codename FROM {users} u JOIN {roles} r ON r.id=u.role_id "
            "WHERE u.id=:owner AND u.is_active=true"
        ),
        {"owner": int(owner)},
    ).scalar_one_or_none()
    if current is None:
        raise RuntimeError(f"Reviewed owner account {owner} must exist and be active")
    if current == "superuser":
        return
    if current != "admin":
        raise RuntimeError(f"Reviewed owner account {owner} must already hold the admin role, got {current!r}")
    bind.execute(
        sa.text(f"UPDATE {users} SET role_id=(SELECT id FROM {roles} WHERE codename='superuser') WHERE id=:owner"),
        {"owner": int(owner)},
    )


def downgrade():
    bind = op.get_bind()
    _, roles, _users = _schema_objects(bind)
    # Give any owner account the previous top role before the codename disappears.
    bind.execute(
        sa.text(
            "UPDATE users SET role_id=(SELECT id FROM roles WHERE codename='admin') "
            "WHERE role_id IN (SELECT id FROM roles WHERE codename='superuser')"
        )
    )
    bind.execute(sa.text(f"DELETE FROM {roles} WHERE codename='superuser'"))
    op.drop_constraint("ck_platform_role_codename", "roles", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        "ck_platform_role_codename", "roles", "codename IN ('user', 'operator', 'admin')", schema=SCHEMA
    )
