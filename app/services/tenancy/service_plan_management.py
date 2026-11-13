"""Guarded operator packaging and explicit retained-data sponsorship backfill.

Workspace roles never grant this authority. These functions use a caller-owned
transaction; no checkout, provider call, automatic grant or runtime DDL occurs.
"""

from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.models import (
    ServicePlanBackfillResolution,
    ServicePlanPolicy,
    Tenant,
    TenantUser,
    User,
    UserServicePlan,
    WorkspaceRole,
)

from .service_plan_backfill import (
    BackfillMappingError,
    HolderPlanSelection,
    HolderSelection,
    LegacyWorkspace,
    validate_mapping,
)
from .service_plan_policy import ServicePlanPolicyError, policy_snapshot


async def _locked_operator(session, actor_user_id: int, other_user_ids=()):
    if type(actor_user_id) is not int or actor_user_id <= 0:
        raise PermissionError("Platform owner required for tariff packaging")
    ids = {actor_user_id, *other_user_ids}
    if any(type(value) is not int or value <= 0 for value in ids):
        raise ValueError("Invalid account identity")
    accounts = (
        await session.scalars(
            select(User)
            .where(User.id.in_(ids))
            .order_by(User.id)
            .options(selectinload(User.role))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    by_id = {account.id: account for account in accounts}
    actor = by_id.get(actor_user_id)
    if actor is None or not actor.is_superuser:
        raise PermissionError("Platform owner required for tariff packaging")
    if set(by_id) != ids:
        raise ValueError("Unknown account")
    return by_id


async def create_policy(session, *, actor_user_id: int, values: dict):
    """Insert a validated immutable revision; never update existing packaging."""
    await _locked_operator(session, actor_user_id)
    allowed = {column.name for column in ServicePlanPolicy.__table__.c} - {"created_at"}
    if set(values) != allowed:
        raise ServicePlanPolicyError("incomplete_policy_definition")
    row = ServicePlanPolicy(**values)
    policy_snapshot(row)
    session.add(row)
    await session.flush()
    return row


async def assign_user_plan(
    session,
    *,
    actor_user_id: int,
    user_id: int,
    plan_code: str,
    policy_version: int,
    is_active: bool = True,
    plan_expires_at: datetime | None = None,
    preserve_state: bool = False,
):
    """Platform-curated account assignment, not a self-service purchase."""
    await _locked_operator(session, actor_user_id, (user_id,))
    if (
        not isinstance(plan_code, str)
        or plan_code not in Tenant.PLANS
        or type(policy_version) is not int
        or not 0 < policy_version <= 2**31 - 1
    ):
        raise ServicePlanPolicyError("invalid_plan_identity")
    if (
        type(preserve_state) is not bool
        or type(is_active) is not bool
        or (
            plan_expires_at is not None
            and (not isinstance(plan_expires_at, datetime) or plan_expires_at.utcoffset() is None)
        )
    ):
        raise ValueError("Invalid plan state or expiry")
    policy = await session.get(ServicePlanPolicy, (plan_code, policy_version))
    validated = policy_snapshot(policy)
    row = await session.scalar(
        select(UserServicePlan).where(UserServicePlan.user_id == user_id).execution_options(populate_existing=True)
    )
    if preserve_state:
        if row is None:
            raise ServicePlanPolicyError("missing_plan")
        is_active, plan_expires_at = row.is_active, row.plan_expires_at
    if row is None:
        row = UserServicePlan(user_id=user_id)
        session.add(row)
    row.plan_code = validated.plan_code
    row.policy_version = validated.policy_version
    row.is_active = is_active
    row.plan_expires_at = plan_expires_at
    row.policy = policy
    await session.flush()
    # Mirrors are display/compatibility only; every owned workspace changes together.
    await session.execute(
        update(Tenant)
        .where(Tenant.plan_holder_user_id == user_id)
        .values(plan=plan_code, plan_expires_at=plan_expires_at)
    )
    return row


async def apply_backfill(
    session,
    *,
    actor_user_id: int,
    holders: Mapping[int, HolderSelection],
    plans: Mapping[int, HolderPlanSelection],
    now: datetime | None = None,
):
    """Apply a complete, operator-confirmed mapping atomically and idempotently."""
    # Backfill is an offline maintenance operation: lock existing users before
    # workspaces, then memberships. Do not expose it as a workspace write route.
    from sqlalchemy import text

    schema = session.bind.dialect.identifier_preparer.quote_schema(Tenant.__table__.schema)
    # Existing-row locks alone do not prevent phantom users/workspaces during
    # complete-map validation. This exclusive maintenance lock blocks inserts too.
    await session.execute(
        text(f"LOCK TABLE {schema}.users, {schema}.tenants, {schema}.tenant_users " "IN ACCESS EXCLUSIVE MODE")
    )
    user_ids = (await session.scalars(select(User.id).order_by(User.id))).all()
    accounts = await _locked_operator(session, actor_user_id, user_ids)
    tenants = (
        await session.scalars(
            select(Tenant).order_by(Tenant.id).with_for_update().execution_options(populate_existing=True)
        )
    ).all()
    members = (
        await session.execute(
            select(TenantUser.tenant_id, TenantUser.user_id)
            .join(WorkspaceRole, TenantUser.role_id == WorkspaceRole.id)
            .where(WorkspaceRole.codename == "owner", TenantUser.user_id.is_not(None))
            .with_for_update(of=TenantUser)
        )
    ).all()
    candidates = {}
    for tenant_id, user_id in members:
        candidates.setdefault(tenant_id, set()).add(user_id)
    rows = [
        LegacyWorkspace(
            tenant.id,
            tenant.plan,
            tenant.plan_expires_at,
            tuple(sorted(candidates.get(tenant.id, ()))),
            tenant.plan_holder_user_id,
        )
        for tenant in tenants
    ]
    policy_rows = (await session.scalars(select(ServicePlanPolicy))).all()
    for policy in policy_rows:
        policy_snapshot(policy)
    resolutions = validate_mapping(
        rows,
        holders,
        plans,
        verified_user_ids=accounts,
        policy_keys={(row.plan_code, row.policy_version) for row in policy_rows},
        operator_user_id=actor_user_id,
        resolved_at=now or datetime.now(timezone.utc),
    )
    for user_id, selection in plans.items():
        existing = await session.get(UserServicePlan, user_id, populate_existing=True)
        fields = dict(
            plan_code=selection.plan_code,
            policy_version=selection.policy_version,
            is_active=selection.is_active,
            plan_expires_at=selection.plan_expires_at,
        )
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in fields.items()):
                raise BackfillMappingError("Backfill cannot overwrite an existing account plan")
        else:
            session.add(UserServicePlan(user_id=user_id, **fields))
    for resolution in resolutions:
        workspace, holder, plan = resolution.workspace, resolution.holder, resolution.plan
        fields = dict(
            holder_user_id=holder.holder_user_id,
            owner_user_ids=list(workspace.owner_user_ids),
            legacy_plan=workspace.plan,
            legacy_plan_expires_at=workspace.plan_expires_at,
            plan_code=plan.plan_code,
            policy_version=plan.policy_version,
            plan_is_active=plan.is_active,
            plan_expires_at=plan.plan_expires_at,
            plan_decision_ref=plan.decision_ref or None,
            operator_user_id=actor_user_id,
            decision_ref=holder.decision_ref,
        )
        existing = await session.get(ServicePlanBackfillResolution, workspace.tenant_id)
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in fields.items()):
                raise BackfillMappingError("Backfill evidence conflicts with the requested mapping")
        else:
            session.add(
                ServicePlanBackfillResolution(
                    tenant_id=workspace.tenant_id,
                    resolved_at=resolution.resolved_at,
                    **fields,
                )
            )
        await session.execute(
            update(Tenant).where(Tenant.id == workspace.tenant_id).values(plan_holder_user_id=holder.holder_user_id)
        )
    await session.flush()
    return resolutions
