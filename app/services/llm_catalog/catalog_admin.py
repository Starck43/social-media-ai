"""Owner-only plan/model grant management in a caller-owned transaction.

Platform reads are explicit metadata projections, never an owner-OR-platform
query through the personal managers. No credential, tariff mutation, provider
request or dispatch activation belongs here.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.permissions import get_current_user
from app.models import LLMCatalog, LLMModel, LLMProvider, PlanModelGrant, Tenant, User
from app.models.llm.plan_model_grant import GRANT_CAPABILITIES


def _identifier(value: int) -> int:
    if type(value) is not int or not 0 < value <= 2**31 - 1:
        raise ValueError("A positive numeric record ID is required")
    return value


async def require_platform_owner(session, *, actor_user_id: int):
    """A posted ID or a stale superuser snapshot is not management authority."""
    actor = get_current_user()
    if (
        type(actor_user_id) is not int
        or actor_user_id <= 0
        or actor is None
        or actor.id != actor_user_id
        or not actor.is_active
    ):
        raise PermissionError("Platform owner required for catalog management")
    fresh = await session.scalar(
        select(User)
        .where(User.id == actor_user_id)
        .options(selectinload(User.role))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if fresh is None or not fresh.is_active or not fresh.is_superuser:
        raise PermissionError("Platform owner required for catalog management")
    return fresh


async def _platform_catalog(session, *, lock=False, active=True):
    stmt = select(LLMCatalog).where(LLMCatalog.kind == "platform", LLMCatalog.owner_user_id.is_(None))
    if lock:
        stmt = stmt.with_for_update()
    row = await session.scalar(stmt.execution_options(populate_existing=True))
    if row is None or (active and not row.is_active):
        raise ValueError("Active platform catalog required")
    return row


def _validate_grant(*, plan, capability, priority, is_active, is_default):
    if not isinstance(plan, str) or plan not in Tenant.PLANS:
        raise ValueError("Unknown service plan")
    if not isinstance(capability, str) or capability not in GRANT_CAPABILITIES:
        raise ValueError("Unknown model capability")
    if type(priority) is not int or not 0 <= priority <= 2**31 - 1:
        raise ValueError("Grant priority must be a nonnegative integer")
    if type(is_active) is not bool or type(is_default) is not bool:
        raise ValueError("Grant flags must be boolean")
    if is_default and not is_active:
        raise ValueError("An inactive grant cannot be the default")


async def recheck_platform_model(session, *, model_id: int, capability: str, catalog_id: int, lock=False):
    """Fresh active/capability proof for management and the later admission seam.

    This proves catalog eligibility only, not actor/holder/tariff or payer access.
    Callers must perform the remaining P21-T12 gates; A has no runtime caller.
    """
    _identifier(model_id)
    if capability not in GRANT_CAPABILITIES:
        raise ValueError("Unknown model capability")
    stmt = (
        select(
            LLMModel.id,
            LLMModel.name,
            LLMModel.model_id,
            LLMModel._capabilities_column.label("capabilities"),
            LLMProvider.id.label("provider_id"),
            LLMProvider.name.label("provider_name"),
        )
        .join(LLMProvider, (LLMProvider.id == LLMModel.provider_id) & (LLMProvider.catalog_id == LLMModel.catalog_id))
        .join(LLMCatalog, LLMCatalog.id == LLMModel.catalog_id)
        .where(
            LLMModel.id == model_id,
            LLMModel.catalog_id == catalog_id,
            LLMModel.owner_user_id.is_(None),
            LLMProvider.owner_user_id.is_(None),
            LLMCatalog.kind == "platform",
            LLMCatalog.owner_user_id.is_(None),
            LLMCatalog.is_active.is_(True),
            LLMProvider.is_active.is_(True),
            LLMModel.is_active.is_(True),
        )
    )
    if lock:
        stmt = stmt.with_for_update(of=(LLMProvider, LLMModel))
    row = (await session.execute(stmt)).mappings().one_or_none()
    if row is None or capability not in (row["capabilities"] or ()):
        raise ValueError("Active platform model supporting the capability required")
    return dict(row)


async def platform_model_choices(session, *, actor_user_id: int, include_inactive: bool = False):
    """Safe console options; never select a ciphertext or a secret reference."""
    await require_platform_owner(session, actor_user_id=actor_user_id)
    stmt = (
        select(LLMModel.id, LLMModel.name, LLMModel.model_id, LLMProvider.name.label("provider_name"))
        .join(LLMProvider, (LLMProvider.id == LLMModel.provider_id) & (LLMProvider.catalog_id == LLMModel.catalog_id))
        .join(LLMCatalog, LLMCatalog.id == LLMModel.catalog_id)
        .where(
            LLMCatalog.kind == "platform",
            LLMCatalog.owner_user_id.is_(None),
            LLMProvider.owner_user_id.is_(None),
            LLMModel.owner_user_id.is_(None),
        )
        .order_by(LLMProvider.name, LLMModel.name, LLMModel.id)
    )
    if not include_inactive:
        stmt = stmt.where(LLMCatalog.is_active.is_(True), LLMProvider.is_active.is_(True), LLMModel.is_active.is_(True))
    rows = await session.execute(stmt)
    return [dict(row) for row in rows.mappings()]


async def retire_platform_model(session, *, actor_user_id: int, model_id: int):
    """Deactivation only: never delete/rebind tenant or scenario selections."""
    await require_platform_owner(session, actor_user_id=actor_user_id)
    _identifier(model_id)
    catalog = await _platform_catalog(session, lock=True, active=False)
    identity = await session.scalar(
        select(LLMModel.id)
        .where(LLMModel.id == model_id, LLMModel.catalog_id == catalog.id, LLMModel.owner_user_id.is_(None))
        .with_for_update()
    )
    if identity is None:
        raise ValueError("Platform model required")
    await session.execute(update(LLMModel).where(LLMModel.id == identity).values(is_active=False))
    await session.flush()


async def save_grant(
    session,
    *,
    actor_user_id: int,
    plan: str,
    capability: str,
    model_id: int,
    priority: int = 100,
    is_active: bool = True,
    is_default: bool = False,
    grant_id: int | None = None,
):
    """Assign/order/default atomically; membership identity is immutable on edit.

    All service writers lock the platform catalog before grants, including an
    initially empty plan/capability group. The partial unique index remains the
    final protection against raw concurrent writers. No nested commit occurs.
    """
    await require_platform_owner(session, actor_user_id=actor_user_id)
    _validate_grant(plan=plan, capability=capability, priority=priority, is_active=is_active, is_default=is_default)
    _identifier(model_id)
    catalog = await _platform_catalog(session, lock=True, active=is_active)
    row = None
    if grant_id is not None:
        _identifier(grant_id)
        row = await session.scalar(
            select(PlanModelGrant)
            .where(PlanModelGrant.id == grant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ValueError("Unknown model grant")
        if (row.plan, row.capability, row.llm_model_id) != (plan, capability, model_id):
            raise ValueError("Grant membership is immutable; retire and assign a new grant")
    if is_active:
        await recheck_platform_model(
            session, model_id=model_id, capability=capability, catalog_id=catalog.id, lock=True
        )
    elif row is None:
        raise ValueError("New grants must target an active platform model")
    if row is None:
        row = PlanModelGrant(plan=plan, capability=capability, llm_model_id=model_id)
        session.add(row)
    if is_default:
        # Clear first, even if SQLAlchemy would otherwise order the new insert
        # before updates. Do not change defaults in other plans/capabilities.
        with session.no_autoflush:
            stmt = update(PlanModelGrant).where(
                PlanModelGrant.plan == plan,
                PlanModelGrant.capability == capability,
                PlanModelGrant.is_active.is_(True),
                PlanModelGrant.is_default.is_(True),
            )
            if row.id is not None:
                stmt = stmt.where(PlanModelGrant.id != row.id)
            await session.execute(stmt.values(is_default=False))
    row.priority = priority
    row.is_active = is_active
    row.is_default = is_default
    await session.flush()
    return row


async def ordered_grants(session, *, actor_user_id: int, plan: str, capability: str):
    """Console-only dormant configuration; not a runtime model resolver."""
    await require_platform_owner(session, actor_user_id=actor_user_id)
    _validate_grant(plan=plan, capability=capability, priority=0, is_active=True, is_default=False)
    return (
        await session.scalars(
            select(PlanModelGrant)
            .where(PlanModelGrant.plan == plan, PlanModelGrant.capability == capability)
            .order_by(PlanModelGrant.priority, PlanModelGrant.llm_model_id)
        )
    ).all()
