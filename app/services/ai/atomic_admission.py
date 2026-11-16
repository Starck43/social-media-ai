"""Atomic token + payer-USD reservation for one strict dispatch attempt."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Any, Mapping

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError
from app.models import (
    LLMDispatchLease,
    LLMDispatchReservationEvent,
    LLMDispatchReservationState,
    LLMTokenAllowanceCycle,
    LLMTokenAllowanceReservation,
    User,
)
from app.services.tenancy.service_plan_policy import ServicePlanPolicyError, resolve_holder_context

from .budget_admission import BudgetAdmissionDenied, BudgetAdmissionUnavailable, exact_usd
from .dispatch_seam import (
    CODE_ALLOWANCE_UNAVAILABLE,
    CODE_TOKEN_ALLOWANCE_EXHAUSTED,
    FUNDING_CLASS_PERSONAL,
    FUNDING_CLASS_PLATFORM,
    DispatchContext,
    DispatchDenial,
    DispatchRequest,
    DispatchReservation,
)

TOKEN_ALLOWANCE_POOL = "provider_reported_billable_tokens"
TOKEN_CYCLE = timedelta(days=30)
MAX_BIGINT = 2**63 - 1
_USD_QUANTUM = Decimal("0.000000001")


class TokenAllowanceExhausted(BudgetAdmissionDenied):
    """The owner/pool/cycle balance cannot cover this attempt's token ceiling."""


async def reserve_token_ceiling(
    session: Any,
    *,
    holder_user_id: int,
    policy_version: int,
    token_limit: int | None,
    reserved_tokens: int,
    now: datetime,
) -> LLMTokenAllowanceCycle:
    """Lock/update the holder's active 30-day cycle and reserve one attempt."""
    cycle = (
        await session.execute(
            select(LLMTokenAllowanceCycle)
            .where(
                LLMTokenAllowanceCycle.plan_holder_user_id == holder_user_id,
                LLMTokenAllowanceCycle.allowance_pool == TOKEN_ALLOWANCE_POOL,
                LLMTokenAllowanceCycle.cycle_ends_at > now,
            )
            .order_by(LLMTokenAllowanceCycle.cycle_started_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if cycle is None:
        cycle = LLMTokenAllowanceCycle(
            plan_holder_user_id=holder_user_id,
            allowance_pool=TOKEN_ALLOWANCE_POOL,
            cycle_started_at=now,
            cycle_ends_at=now + TOKEN_CYCLE,
            policy_version=policy_version,
            token_limit=token_limit,
            reserved_tokens=0,
            consumed_tokens=0,
        )
        session.add(cycle)
        await session.flush()
    else:
        # A plan change updates the active cap/version while retaining both
        # consumed and reserved totals for this cycle.
        cycle.policy_version = policy_version
        cycle.token_limit = token_limit
    total = int(cycle.consumed_tokens) + int(cycle.reserved_tokens) + reserved_tokens
    if total > MAX_BIGINT or (token_limit is not None and total > token_limit):
        raise TokenAllowanceExhausted("token_allowance_exhausted")
    cycle.reserved_tokens += reserved_tokens
    return cycle


def _token_ceiling(request: DispatchRequest) -> int:
    """Bound total billable tokens by the reviewed provider context window.

    Context windows cover prompt, tool schema/result and generated tokens. Media
    adapters have provider-specific non-token billing dimensions, so this bound
    deliberately rejects media until those dimensions have a reviewed envelope.
    """
    model = request.model
    context = getattr(model, "context_window_tokens", None)
    output = getattr(model, "max_output_tokens", None)
    if request.has_media or type(context) is not int or context <= 0 or type(output) is not int or output <= 0:
        raise BudgetAdmissionUnavailable("A reviewed total-token context bound is unavailable")
    requested_output = request.requested_output_tokens if request.requested_output_tokens is not None else output
    if (
        type(requested_output) is not int
        or requested_output <= 0
        or requested_output > output
        or requested_output > context
    ):
        raise BudgetAdmissionDenied("Requested output exceeds the reviewed model envelope")
    if context > MAX_BIGINT:
        raise BudgetAdmissionUnavailable("Token context bound is not representable")
    return context


def _tenant_usd_ceiling(receipt: Mapping[str, Any], token_ceiling: int) -> tuple[Decimal | None, str | None]:
    """Price every possible billable token at the highest snapshotted unit rate."""
    tariff = receipt["tariff_state"]
    if tariff in {"undefined", "free"}:
        return Decimal(0), None
    if tariff != "known":
        raise BudgetAdmissionUnavailable("Tariff state is unavailable")
    unit = receipt.get("pricing_token_unit") or 1000
    if type(unit) is not int or unit not in {1000, 1_000_000}:
        return None, None
    fields = ["input_cost_per_1k", "output_cost_per_1k"]
    if receipt.get("pricing_policy_version") == "cache-tariff-v2":
        fields.extend(("cache_read_cost_per_1m", "cache_write_5m_cost_per_1m", "cache_write_1h_cost_per_1m"))
        if any(receipt.get(field) is None for field in fields):
            return None, None
    elif receipt.get("pricing_policy_version") != "provider-price-v1":
        return None, None
    divisor = Decimal(unit)
    rates: list[Decimal] = []
    for field in fields:
        raw = receipt.get(field)
        if raw is None:
            return None, None
        # Legacy model tariffs may be Float; decimal text conversion is the
        # explicit normalization policy and never treats missing values as zero.
        value = Decimal(str(raw))
        if not value.is_finite() or value < 0:
            raise BudgetAdmissionUnavailable("Tariff snapshot is invalid")
        rates.append(value / (Decimal(1_000_000) if field.endswith("_per_1m") else divisor))
    with localcontext() as context:
        context.prec = 64
        amount = (Decimal(token_ceiling) * max(rates)).quantize(_USD_QUANTUM, rounding=ROUND_CEILING)
    return exact_usd(amount), "provider-context-window-v1"


def _request_shape(request: DispatchRequest) -> dict[str, Any]:
    provider = getattr(request.model, "provider", None)
    api_format = getattr(provider, "api_format", None)
    adapter = {
        "openai": "openai_chat_completions",
        "openai_chat_completions": "openai_chat_completions",
        "anthropic": "anthropic_messages",
        "anthropic_messages": "anthropic_messages",
    }.get(api_format)
    if adapter is None:
        raise BudgetAdmissionUnavailable("Provider adapter has no reviewed request bound")
    output = request.requested_output_tokens or getattr(request.model, "max_output_tokens", None)
    if type(output) is not int or output <= 0:
        raise BudgetAdmissionUnavailable("Output token ceiling is unavailable")
    return {
        "adapter": adapter,
        "provider_name": getattr(provider, "name", None),
        "model_id": getattr(request.model, "model_id", None),
        "has_media": request.has_media,
        "uses_tools": request.uses_tools,
        "requested_output_tokens": output,
    }


class AtomicLLMAdmissionService:
    """Implements the seam admission interface; all durable writes share one UOW."""

    async def check_allowance(self, request: DispatchRequest, context: DispatchContext) -> bool:
        """Cheap pre-rate check only; reserve() rechecks while holding the pool lock."""
        plan = getattr(context.workspace_plan, "policy", None)
        token_policy = getattr(plan, "token_policy", None)
        if token_policy is None or token_policy.unit != "token":
            raise DispatchDenial(CODE_ALLOWANCE_UNAVAILABLE, "step7_allowance")
        if token_policy.token_limit == 0:
            raise DispatchDenial(CODE_TOKEN_ALLOWANCE_EXHAUSTED, "token_allowance_exhausted")
        if context.tenant_id is None or context.plan_holder_user_id is None:
            raise DispatchDenial(CODE_ALLOWANCE_UNAVAILABLE, "step7_allowance")
        try:
            ceiling = _token_ceiling(request)
        except (BudgetAdmissionDenied, BudgetAdmissionUnavailable, TypeError, ValueError):
            raise DispatchDenial(CODE_ALLOWANCE_UNAVAILABLE, "step7_allowance")
        async with async_session_maker() as session:
            active = (
                (
                    await session.execute(
                        select(LLMTokenAllowanceCycle).where(
                            LLMTokenAllowanceCycle.plan_holder_user_id == context.plan_holder_user_id,
                            LLMTokenAllowanceCycle.allowance_pool == TOKEN_ALLOWANCE_POOL,
                            LLMTokenAllowanceCycle.cycle_ends_at > datetime.now(timezone.utc),
                        )
                    )
                )
                .scalars()
                .all()
            )
        if not active:
            return True
        cycle = max(active, key=lambda row: row.cycle_started_at)
        limit = token_policy.token_limit
        if limit is not None and int(cycle.consumed_tokens) + int(cycle.reserved_tokens) + ceiling > limit:
            raise DispatchDenial(CODE_TOKEN_ALLOWANCE_EXHAUSTED, "token_allowance_exhausted")
        return True

    async def reserve(self, request: DispatchRequest, context: DispatchContext) -> DispatchReservation:
        dispatch_id = request.dispatch_id
        if (
            type(dispatch_id) is not str
            or len(dispatch_id) != 32
            or any(char not in "0123456789abcdef" for char in dispatch_id)
        ):
            raise DispatchDenial("admission_unavailable", "step8_admission")
        if context.funding_class not in {FUNDING_CLASS_PLATFORM, FUNDING_CLASS_PERSONAL}:
            raise DispatchDenial("funding_class_unsupported", "class_unknown")
        if (
            type(context.actor_user_id) is not int
            or context.actor_user_id <= 0
            or type(context.tenant_id) is not int
            or context.tenant_id <= 0
            or type(context.plan_holder_user_id) is not int
            or context.plan_holder_user_id <= 0
            or type(context.credential_version) is not int
            or context.credential_version <= 0
            or type(context.payer) is not str
            or not context.payer.strip()
            or len(context.payer) > 200
        ):
            raise DispatchDenial("admission_unavailable", "step8_admission")
        try:
            token_ceiling = _token_ceiling(request)
        except (BudgetAdmissionDenied, BudgetAdmissionUnavailable, TypeError, ValueError) as exc:
            raise DispatchDenial(CODE_ALLOWANCE_UNAVAILABLE, "step7_allowance") from exc
        now = datetime.now(timezone.utc)
        record: Mapping[str, Any]
        async with async_session_maker() as session:
            async with session.begin():
                execution = dict(request.execution or {})
                user_ids_set = {context.actor_user_id, context.plan_holder_user_id}
                payer_hint = None
                if context.funding_class == FUNDING_CLASS_PERSONAL:
                    initiated_by = execution.get("initiated_by_user_id")
                    if type(initiated_by) is int and initiated_by > 0:
                        user_ids_set.add(initiated_by)
                    task_id = execution.get("agent_task_id")
                    if type(task_id) is int and task_id > 0:
                        from app.models.billing.billing import TaskBillingPayer

                        payer_hint = await session.scalar(
                            select(TaskBillingPayer.payer_user_id)
                            .where(
                                TaskBillingPayer.agent_task_id == task_id,
                                TaskBillingPayer.tenant_id == context.tenant_id,
                            )
                            .order_by(TaskBillingPayer.consent_version.desc())
                            .limit(1)
                        )
                        if type(payer_hint) is int and payer_hint > 0:
                            user_ids_set.add(payer_hint)
                # Stable lock order for cross-workspace reservations: every
                # known actor/holder/payer user in ascending ID order, then the
                # workspace and its one USD ledger, finally the token cycle.
                user_ids = sorted(user_ids_set)
                locked = set(
                    (
                        await session.execute(
                            select(User.id).where(User.id.in_(user_ids)).order_by(User.id).with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                if locked != set(user_ids):
                    raise TenantContextError("Admission actor or allowance holder is unavailable")
                try:
                    fresh_plan = await resolve_holder_context(
                        session,
                        context.tenant_id,
                        context.actor_user_id,
                        now=now,
                        lock_holder=True,
                    )
                except ServicePlanPolicyError as exc:
                    raise BudgetAdmissionUnavailable("Holder allowance policy is unavailable") from exc
                if (
                    fresh_plan.plan_holder_user_id != context.plan_holder_user_id
                    or fresh_plan.plan_policy_version != context.plan_policy_version
                    or fresh_plan.actor_user_id != context.actor_user_id
                ):
                    raise BudgetAdmissionDenied("Holder policy changed before reservation")
                token_policy = fresh_plan.policy.token_policy
                if token_policy is None or token_policy.unit != "token":
                    raise BudgetAdmissionUnavailable("Token allowance policy is unconfigured")
                if token_policy.token_limit == 0:
                    raise DispatchDenial(CODE_TOKEN_ALLOWANCE_EXHAUSTED, "token_allowance_exhausted")

                from app.models.managers.llm_budget_manager import LLMBudgetManager
                from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager
                from app.services.ai.dispatch_receipts import build_dispatch_receipt

                if context.funding_class == FUNDING_CLASS_PERSONAL:
                    admission = await PersonalLLMBillingManager().reserve_dispatch(
                        request.model,
                        purpose=request.purpose,
                        stage=request.stage,
                        execution=execution,
                        request_shape=_request_shape(request),
                        dispatch_id=dispatch_id,
                        session=session,
                        undefined_outside_cap=True,
                    )
                    accepted_version = admission.get("__credential_version")
                    if accepted_version != context.credential_version:
                        raise BudgetAdmissionDenied("Credential version changed during reservation")
                    from app.models.billing.dispatch import PersonalLLMDispatch

                    personal_dispatch = (
                        await session.execute(
                            select(PersonalLLMDispatch).where(PersonalLLMDispatch.dispatch_id == dispatch_id)
                        )
                    ).scalar_one_or_none()
                    if personal_dispatch is None:
                        raise BudgetAdmissionUnavailable("Personal payer evidence is unavailable")
                    if payer_hint is not None and personal_dispatch.payer_user_id != payer_hint:
                        raise BudgetAdmissionDenied("Personal payer consent changed before reservation")
                    payer_ref = f"personal:{personal_dispatch.payer_user_id}"
                    if context.payer != payer_ref:
                        raise BudgetAdmissionDenied("Personal payer changed during reservation")
                    record = {key: value for key, value in admission.items() if not key.startswith("__")}
                else:
                    payer_ref = f"tenant:{context.tenant_id}"
                    if context.payer != payer_ref:
                        raise BudgetAdmissionDenied("Workspace payer changed before reservation")
                    record = build_dispatch_receipt(
                        request.model,
                        tenant_id=context.tenant_id,
                        purpose=request.purpose,
                        stage=request.stage,
                        dispatch_id=dispatch_id,
                        prepared_at=now,
                        execution=execution,
                    )
                    ceiling_usd, bound_version = _tenant_usd_ceiling(record, token_ceiling)
                    created = await LLMBudgetManager().reserve_receipt(
                        record,
                        ceiling_usd=ceiling_usd,
                        bound_version=bound_version,
                        session=session,
                    )
                    if created is not True:
                        raise BudgetAdmissionDenied("Exact receipt replay is not a fresh admission")

                try:
                    cycle = await reserve_token_ceiling(
                        session,
                        holder_user_id=context.plan_holder_user_id,
                        policy_version=token_policy.policy_version,
                        token_limit=token_policy.token_limit,
                        reserved_tokens=token_ceiling,
                        now=now,
                    )
                except TokenAllowanceExhausted as exc:
                    raise DispatchDenial(CODE_TOKEN_ALLOWANCE_EXHAUSTED, "token_allowance_exhausted") from exc

                provider = getattr(request.model, "provider", None)
                model_row_id = getattr(request.model, "id", None)
                provider_row_id = getattr(provider, "id", None)
                catalog_id = context.catalog_id
                if (
                    type(model_row_id) is not int
                    or type(provider_row_id) is not int
                    or type(catalog_id) is not str
                    or not catalog_id
                    or type(context.payer) is not str
                    or any(ord(char) < 32 for char in context.payer)
                ):
                    raise BudgetAdmissionUnavailable("Dispatch identity snapshot is incomplete")
                consent_version = context.delegation_consent_version
                if context.actor_user_id != context.plan_holder_user_id and type(consent_version) is not int:
                    raise BudgetAdmissionDenied("Delegated consent version is unavailable")
                session.add(
                    LLMTokenAllowanceReservation(
                        dispatch_id=dispatch_id,
                        tenant_id=context.tenant_id,
                        actor_user_id=context.actor_user_id,
                        plan_holder_user_id=context.plan_holder_user_id,
                        allowance_pool=TOKEN_ALLOWANCE_POOL,
                        cycle_id=cycle.id,
                        model_row_id=model_row_id,
                        provider_row_id=provider_row_id,
                        model_id=str(getattr(request.model, "model_id", "")),
                        catalog_id=catalog_id,
                        funding_class=context.funding_class,
                        payer_ref=payer_ref,
                        credential_version=context.credential_version,
                        consent_version=consent_version,
                        policy_version=token_policy.policy_version,
                        reserved_tokens=token_ceiling,
                    )
                )
                lease = (
                    await session.execute(
                        select(LLMDispatchLease).where(
                            LLMDispatchLease.lease_id == dispatch_id,
                            LLMDispatchLease.released_at.is_(None),
                            LLMDispatchLease.expires_at > now,
                        )
                    )
                ).scalar_one_or_none()
                if lease is None:
                    raise BudgetAdmissionUnavailable("Dispatch capacity lease is unavailable")
                session.add(
                    LLMDispatchReservationState(
                        dispatch_id=dispatch_id,
                        status="reserved",
                        lease_until=lease.expires_at,
                    )
                )
                session.add(
                    LLMDispatchReservationEvent(
                        dispatch_id=dispatch_id,
                        event_key="admitted",
                        event_kind="admitted",
                        observed_at=now,
                    )
                )
                await session.flush()
        return DispatchReservation(
            dispatch_id=dispatch_id,
            credential_version=context.credential_version,
            receipt=record,
        )
