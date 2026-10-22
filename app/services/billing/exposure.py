"""Read-only financial exposure for the operator surface.

Three independent reads, no writes and no projections:
- today's payer-bound spend inside one workspace (personal ledger aggregates);
- provider/model pairs with dispatched attempts whose price is undefined;
- active catalog models carrying the ambiguous 0/0 ordinary tariff.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


async def workspace_spend_summary(tenant_id: int, *, utc_day: date | None = None) -> dict:
    """Aggregate one workspace's personal dispatching; no payer identities."""
    from app.models import PersonalBillingAccount

    return await PersonalBillingAccount.objects.workspace_spend_summary(tenant_id, utc_day=utc_day)


async def unpriced_attempt_models(tenant_id: int, *, days: int = 7, limit: int = 10) -> list[dict]:
    """Models with undefined-priced dispatches observed in the recent window."""
    from app.models import LLMOutcomeEvent

    if type(days) is not int or not 0 < days <= 90:
        raise ValueError("Unpriced evidence window must be between 1 and 90 days")
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return await LLMOutcomeEvent.objects.unpriced_models(tenant_id, since=since, limit=limit)


async def zero_price_active_models(limit: int = 10) -> dict:
    """Active catalog models whose ordinary tariff is 0/0 (free or unset)."""
    from app.models import LLMModel

    return await LLMModel.objects.zero_price_active_models(limit=limit)
