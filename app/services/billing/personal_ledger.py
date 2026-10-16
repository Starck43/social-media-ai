"""Internal service facade for personal reservations and persisted outcome settlement."""

from __future__ import annotations

from typing import Any, Mapping


async def reserve_personal_dispatch(
    model: Any,
    *,
    purpose: str,
    stage: str,
    execution: Mapping[str, Any] | None = None,
    request_shape: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Commit the legacy receipt and personal reserve before a provider call."""
    from app.models import PersonalLLMBudgetDay

    return await PersonalLLMBudgetDay.objects.reserve_dispatch(
        model,
        purpose=purpose,
        stage=stage,
        execution=execution,
        request_shape=request_shape,
    )


async def observe_personal_outcome(*, tenant_id: int, event_id: str) -> bool:
    """Settle from immutable stored evidence; provider cost is never accepted here."""
    from app.models import PersonalLLMBudgetDay

    return await PersonalLLMBudgetDay.objects.observe_event(tenant_id=tenant_id, event_id=event_id)
