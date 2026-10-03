"""make tenants.plan a real tier with a CHECK constraint

`plan` was a free-text column that nothing read: no row could say which tier it
was on, no limit followed from it, and a workspace's actual quotas lived only in
`max_sources` / `daily_cost_limit`, which the admin could set to anything. This
migration turns the column into the billing tier the code enforces
(`app/models/tenant.py::PLAN_LIMITS`, `app/services/tenancy/limits.py`).

Order matters: every existing value is normalised to a legal tier *before* the
constraint is added, or the ALTER fails on the first legacy row.

The legacy labels were `personal` (the default before this) and `owner` (written
by `get_or_create_owner`). They do **not** map alike:

* `owner` → `business`. That row is the bootstrap workspace — the one
  `get_or_create_owner` creates to hold every pre-existing (legacy) row, and the
  one every operator lands in. Capping it at `pro`'s 5 team seats would break
  the install on upgrade, and its `max_sources=100` only makes sense on an
  unlimited tier.
* `personal` → `pro`. A client workspace that was running with no limits gets
  the middle tier, which is a safe default rather than a gift.

Anything else is normalised to `pro` too: a hand-edited row must not be allowed
to abort the ALTER that adds the constraint.

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-03

"""
from typing import Sequence, Union

from alembic import op

import sqlalchemy as sa

from app.core.config import settings

# revision identifiers, used by Alembic.
revision: str = "0071"
down_revision: Union[str, None] = "0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The tiers the column may hold. Mirrors `Tenant.PLAN_LIMITS`; the CHECK
#: constraint below is the database's copy of the same list.
PLANS = ("starter", "pro", "business")

#: What a workspace lands on when its stored plan is not a tier.
DEFAULT_PLAN = "pro"

#: The bootstrap workspace is the one row that must not be capped: it owns every
#: pre-existing row, and it is where the operator's own memberships live. Keyed
#: by the *legacy* plan value, so it applies before the normalising UPDATE.
BOOTSTRAP_SLUG = "owner"
BOOTSTRAP_PLAN = "business"


def upgrade() -> None:
    table = sa.table(
        "tenants",
        sa.column("id", sa.Integer),
        sa.column("slug", sa.String),
        sa.column("plan", sa.String),
        schema=settings.DB_SCHEMA,
    )

    # The bootstrap workspace first, and separately: it is recognised by slug,
    # not by its plan, because its plan is exactly the value being replaced.
    op.execute(
        table.update()
        .where(table.c.slug == BOOTSTRAP_SLUG)
        .values(plan=BOOTSTRAP_PLAN)
    )

    # Then everything that is not a tier. Done before the constraint is added,
    # or the ALTER fails on the first legacy row.
    op.execute(
        table.update()
        .where(table.c.plan.notin_(list(PLANS)))
        .values(plan=DEFAULT_PLAN)
    )

    op.alter_column(
        "tenants",
        "plan",
        existing_type=sa.String(length=30),
        type_=sa.String(length=20),
        existing_nullable=False,
        server_default=DEFAULT_PLAN,
        schema=settings.DB_SCHEMA,
    )
    op.create_check_constraint(
        "ck_tenants_plan",
        "tenants",
        f"plan IN ({', '.join(repr(p) for p in PLANS)})",
        schema=settings.DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_tenants_plan", "tenants", schema=settings.DB_SCHEMA, type_="check")
    op.alter_column(
        "tenants",
        "plan",
        existing_type=sa.String(length=20),
        type_=sa.String(length=30),
        existing_nullable=False,
        server_default="personal",
        schema=settings.DB_SCHEMA,
    )