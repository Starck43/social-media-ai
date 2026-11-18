"""Stable caller-facing interface for personal account and task payer operations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.billing import PersonalBillingAccount, TaskBillingPayer


@dataclass(frozen=True)
class OwnerModePayer:
    """Resolved catalog owner when personal billing is disabled.

    A single-owner deployment has no personal account or payer consent. The
    catalog owner is the authenticated actor at launch and the job's stored
    initiator at execution; both boundaries recheck active workspace rights.
    """

    user_id: int
    payer_user_id: int
    agent_task_id: int | None = None
    consent_version: int = 0


def _owner_mode_enabled() -> bool:
    from app.core.config import settings

    return not bool(settings.PERSONAL_BILLING_ENABLED)


async def _active_actor_in_tenant(user_id: int, tenant_id: int) -> int:
    """Reject inactive users and memberships; never infer an owner from a payload."""
    if type(user_id) is not int or user_id <= 0 or type(tenant_id) is not int or tenant_id <= 0:
        raise PermissionError("An active workspace actor is required")
    from app.models import User
    from app.models.managers.tenant_manager import tenant_users

    user = await User.objects.get(id=user_id, is_active=True)
    if user is None:
        raise PermissionError("An active workspace actor is required")
    memberships = await tenant_users.web_memberships(user_id)
    if not any(member.tenant_id == tenant_id for member in memberships):
        raise PermissionError("An active workspace actor is required")
    return user_id


async def _owner_mode_task_initiator(task_id: int, tenant_id: int) -> int:
    """Take the catalog owner from the admitted job's stored initiator."""
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models import Job

    async with async_session_maker() as session:
        value = (
            await session.execute(
                select(Job.initiated_by_user_id)
                .where(Job.agent_task_id == task_id, Job.tenant_id == tenant_id)
                .order_by(Job.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return await _active_actor_in_tenant(value, tenant_id)


async def configure_current_personal_account(
    daily_limit_usd: Decimal | int | str,
) -> "PersonalBillingAccount":
    """Set the authenticated user's cap; zero explicitly means unlimited."""
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.configure_self(daily_limit_usd)


async def current_personal_account() -> "PersonalBillingAccount | None":
    """Read the authenticated user's account without provisioning a row."""
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.self_account()


async def current_personal_spend_summary(*, utc_day=None):
    """Return the caller's own payer-bound daily spend split by workspace."""
    from app.models.billing import PersonalBillingAccount

    return await PersonalBillingAccount.objects.self_spend_summary(utc_day=utc_day)


async def consent_current_user_as_task_payer(task_id: int):
    """Bind the authenticated user as payer, or resolve the owner in owner mode."""
    if _owner_mode_enabled():
        from app.core.permissions import get_current_user
        from app.core.tenant_context import current_tenant_id

        actor = get_current_user()
        user_id = await _active_actor_in_tenant(getattr(actor, "id", None), current_tenant_id())
        return OwnerModePayer(user_id=user_id, payer_user_id=user_id, agent_task_id=task_id)
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.consent_task_payer(task_id)


async def resolve_scheduled_task_payer(*, task_id: int, tenant_id: int):
    """Resolve a worker's stored payer, or the job's trusted initiator in owner mode."""
    if _owner_mode_enabled():
        user_id = await _owner_mode_task_initiator(task_id, tenant_id)
        return OwnerModePayer(user_id=user_id, payer_user_id=user_id)
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.resolve_task_payer(task_id=task_id, tenant_id=tenant_id)


async def consent_legacy_identity(*, source_kind: str, source_identity: str):
    """Record account-owner consent for one exact, reporting-only legacy identity."""
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.consent_legacy_attribution(
        source_kind=source_kind, source_identity=source_identity
    )


async def review_legacy_identity_consent(consent_id: int):
    """Approve an existing owner consent as an active platform administrator."""
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.review_legacy_attribution(consent_id)


async def unassigned_legacy_inventory(*, source_kind: str, limit: int = 100):
    """List only exact unmapped receipts or attempts; never guesses a payer."""
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.legacy_attribution_inventory(source_kind=source_kind, limit=limit)
