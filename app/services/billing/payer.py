"""Stable caller-facing interface for personal account and task payer operations."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.billing import PersonalBillingAccount, TaskBillingPayer


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


async def consent_current_user_as_task_payer(task_id: int) -> "TaskBillingPayer":
    """Bind the authenticated user as payer after current workspace authorization."""
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.consent_task_payer(task_id)


async def resolve_scheduled_task_payer(*, task_id: int, tenant_id: int) -> "PersonalBillingAccount":
    """Resolve a worker's explicit stored payer; no ambient actor is consulted."""
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
