"""Atomic personal dispatch reservation and append-only settlement operations."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, localcontext
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id
from app.models.managers.base_manager import BaseManager
from app.services.ai.budget_admission import (
    BudgetAdmissionDenied,
    BudgetAdmissionUnavailable,
    exact_usd,
)
from app.services.ai.dispatch_receipts import RECEIPT_FIELDS, build_dispatch_receipt
from app.services.ai.outcome_events import EVENT_FIELDS, validate_outcome_event

from .personal_billing_manager import (
    POLICY_VERSION,
    PersonalBillingError,
    PersonalBillingUnavailable,
)

CACHE_RATE_FIELDS = (
    "cache_read_cost_per_1m",
    "cache_write_5m_cost_per_1m",
    "cache_write_1h_cost_per_1m",
)
SUPPORTED_PRICING_UNITS = (1000, 1_000_000)

UNDEFINED_POLICY_VERSION = "owner-undefined-allow-v1"
BOUND_POLICY_VERSION = "reviewed-request-envelope-v1"
BASELINE_VERSION = "reviewed-clean-midnight-v1"


class PersonalDispatchConflict(RuntimeError):
    """Dispatch identity or outcome evidence conflicts with stored accounting."""


class PersonalLLMBillingManager(BaseManager):
    def __init__(self):
        from ..billing.ledger import PersonalLLMBudgetDay

        super().__init__(PersonalLLMBudgetDay)

    @staticmethod
    def _actor_id() -> int | None:
        actor = get_current_user()
        if actor is None:
            return None
        actor_id = getattr(actor, "id", None)
        return actor_id if type(actor_id) is int and actor_id > 0 else None

    @staticmethod
    def _request_bound(policy, request_shape: Mapping[str, Any] | None, receipt: Mapping[str, Any]):
        expected = {"adapter", "provider_name", "model_id", "has_media", "uses_tools", "requested_output_tokens"}
        if not isinstance(request_shape, Mapping) or set(request_shape) != expected:
            raise BudgetAdmissionUnavailable("A supported provider request envelope is required")
        if (
            request_shape["adapter"] not in {"openai_chat_completions", "anthropic_messages"}
            or request_shape["provider_name"] != receipt["provider_name"]
            or request_shape["model_id"] != receipt["model_id"]
            or type(request_shape["has_media"]) is not bool
            or type(request_shape["uses_tools"]) is not bool
            or type(request_shape["requested_output_tokens"]) is not int
            or request_shape["requested_output_tokens"] <= 0
        ):
            raise BudgetAdmissionUnavailable("Provider request mode has no reviewed token enforcement")
        if receipt["tariff_state"] == "undefined":
            return "undefined", Decimal(0), None
        if receipt["tariff_state"] == "free":
            return "bounded", Decimal(0), None
        if policy is None:
            return "unbounded", None, None
        if request_shape["has_media"]:
            raise BudgetAdmissionDenied("Reviewed bound supports text-only request inputs")
        uses_tools = request_shape["uses_tools"]
        if uses_tools and policy.request_mode != "text_and_tools":
            raise BudgetAdmissionDenied("Reviewed bound does not support tool inputs")
        pricing_policy = receipt.get("pricing_policy_version", "provider-price-v1")
        if pricing_policy == "cache-tariff-v2":
            unit = receipt.get("pricing_token_unit")
            if type(unit) is not int or unit not in SUPPORTED_PRICING_UNITS:
                return "unbounded", None, None
            cache_rates = [receipt.get(field) for field in CACHE_RATE_FIELDS]
            # A configured prompt-cache mode can add cost to any request. Missing
            # cache tariffs cannot support a bounded reservation claim.
            if any(rate is None for rate in cache_rates):
                return "unbounded", None, None
            divisor = Decimal(unit)
            rates_per_token = [
                Decimal(receipt["input_cost_per_1k"]) / divisor,
                Decimal(receipt["output_cost_per_1k"]) / divisor,
                *(Decimal(rate) / Decimal(1_000_000) for rate in cache_rates),
            ]
        elif pricing_policy == "provider-price-v1":
            divisor = Decimal(1000)
            rates_per_token = [
                Decimal(receipt["input_cost_per_1k"]) / divisor,
                Decimal(receipt["output_cost_per_1k"]) / divisor,
            ]
        else:
            return "unbounded", None, None
        with localcontext() as context:
            context.prec = 64
            # The provider enforces its reviewed context window; without a
            # model tokenizer, price every context token at the higher rate.
            ceiling = (
                Decimal(policy.max_input_tokens + policy.max_output_tokens)
                * max(rates_per_token)
            )
            ceiling = ceiling.quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)
        return "bounded", exact_usd(ceiling), policy.version

    @staticmethod
    def _selected_model_matches_snapshot(fresh_model, model, selected_provider) -> bool:
        """Bind admission to every raw pricing input plus the selected identity."""
        return bool(
            fresh_model is not None
            and selected_provider is not None
            and fresh_model.model_id == getattr(model, "model_id", None)
            and fresh_model.max_tokens == getattr(model, "max_tokens", None)
            and fresh_model.input_cost_per_1k == getattr(model, "input_cost_per_1k", None)
            and fresh_model.output_cost_per_1k == getattr(model, "output_cost_per_1k", None)
            and getattr(fresh_model, "pricing_token_unit", None) == getattr(model, "pricing_token_unit", None)
            and all(
                getattr(fresh_model, field, None) == getattr(model, field, None) for field in CACHE_RATE_FIELDS
            )
            and fresh_model.provider.id == getattr(selected_provider, "id", None)
            and fresh_model.provider.name == getattr(selected_provider, "name", None)
            and fresh_model.provider.api_format == getattr(selected_provider, "api_format", None)
        )

    async def reserve_dispatch(
        self,
        model: Any,
        *,
        purpose: str,
        stage: str,
        execution: Mapping[str, Any] | None = None,
        request_shape: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Atomically store the legacy receipt and personal reservation before transport."""
        from ..billing.billing import PersonalBillingAccount, TaskBillingPayer
        from ..billing.dispatch import PersonalLLMDispatch
        from ..billing.ledger import (
            PersonalBillingCutover,
            PersonalLLMBoundPolicy,
            PersonalLLMBudgetDay,
            PersonalLLMLedgerEntry,
            PersonalLLMReservation,
            PersonalLLMReservationState,
        )
        from ..identity.user import User
        from ..identity.role import PlatformRole
        from ..llm.llm_model import LLMModel
        from ..managers.llm_dispatch_receipt_manager import LLMDispatchReceiptManager
        from ..scheduling.agent_task import AgentTask
        from ..tenancy.tenant import Tenant
        from ..tenancy.tenant_user import TenantUser

        if not settings.PERSONAL_BILLING_ENABLED:
            raise PersonalBillingUnavailable("Personal billing is not enabled")
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise PersonalBillingError("Workspace execution scope is required")
        actor_id = self._actor_id()
        execution_values = dict(execution or {})
        task_id = execution_values.get("agent_task_id")
        initiated_by_id = execution_values.get("initiated_by_user_id")
        if task_id is not None and (type(task_id) is not int or task_id <= 0):
            raise PersonalBillingError("Invalid task execution identity")
        if initiated_by_id is not None and (type(initiated_by_id) is not int or initiated_by_id <= 0):
            raise PersonalBillingError("Invalid trusted task initiator identity")
        now = None
        dispatch_id = uuid4().hex
        async with self._get_owned_session() as session:
            workspace = (
                await session.execute(
                    select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            if workspace is None:
                raise PersonalBillingUnavailable("Workspace is inactive")
            payer_hint = actor_id
            if task_id is not None:
                consent_hint = (
                    await session.execute(
                        select(TaskBillingPayer)
                        .where(TaskBillingPayer.agent_task_id == task_id, TaskBillingPayer.tenant_id == tenant_id)
                        .order_by(TaskBillingPayer.consent_version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if consent_hint is None:
                    raise PersonalBillingUnavailable("Task payer consent is missing")
                payer_hint = consent_hint.payer_user_id
            user_ids = sorted({value for value in (payer_hint, initiated_by_id or actor_id) if value is not None})
            active_user_ids = set(
                (
                    await session.execute(
                        select(User.id)
                        .where(User.id.in_(user_ids), User.is_active.is_(True))
                        .order_by(User.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            ) if user_ids else set()
            fresh_model = (
                await session.execute(
                    select(LLMModel)
                    .where(LLMModel.id == getattr(model, "id", None), LLMModel.is_active.is_(True))
                    .options(selectinload(LLMModel.provider))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if fresh_model is None or not fresh_model.provider.is_active:
                raise PersonalBillingUnavailable("Selected model or provider is inactive")
            now = datetime.now(timezone.utc)
            selected_provider = getattr(model, "provider", None)
            if not self._selected_model_matches_snapshot(fresh_model, model, selected_provider):
                raise PersonalBillingUnavailable("Selected model or provider changed; reload configuration before retry")
            receipt = build_dispatch_receipt(
                fresh_model,
                tenant_id=tenant_id,
                purpose=purpose,
                stage=stage,
                dispatch_id=dispatch_id,
                prepared_at=now,
                execution=execution_values,
            )
            if initiated_by_id is not None:
                receipt["initiated_by_user_id"] = initiated_by_id
            if task_id is not None:
                task = (
                    await session.execute(
                        select(AgentTask)
                        .where(AgentTask.id == task_id, AgentTask.tenant_id == tenant_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                consent = (
                    await session.execute(
                        select(TaskBillingPayer)
                        .where(TaskBillingPayer.agent_task_id == task_id, TaskBillingPayer.tenant_id == tenant_id)
                        .order_by(TaskBillingPayer.consent_version.desc())
                        .limit(1)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if task is None or consent is None:
                    raise PersonalBillingUnavailable("Task payer consent is missing")
                payer_id = consent.payer_user_id
                if payer_id not in active_user_ids or (
                    initiated_by_id is not None and initiated_by_id not in active_user_ids
                ):
                    raise PersonalBillingUnavailable("Task payer or initiating user is inactive")
                membership = (
                    await session.execute(
                        select(TenantUser.id).where(
                            TenantUser.tenant_id == tenant_id,
                            TenantUser.user_id == payer_id,
                            TenantUser.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if membership is None:
                    raise PersonalBillingUnavailable("Task payer workspace membership was revoked")
                if actor_id is not None and initiated_by_id is not None and actor_id != initiated_by_id:
                    raise PersonalBillingUnavailable("Task dispatch actor does not match its trusted enqueue initiator")
                effective_initiator_id = actor_id if actor_id is not None else initiated_by_id
                receipt["initiated_by_user_id"] = effective_initiator_id
                if effective_initiator_id is not None:
                    initiator_membership = (
                        await session.execute(
                            select(TenantUser.id).where(
                                TenantUser.tenant_id == tenant_id,
                                TenantUser.user_id == effective_initiator_id,
                                TenantUser.is_active.is_(True),
                            )
                        )
                    ).scalar_one_or_none()
                    trusted_operator = None
                    if initiator_membership is None:
                        trusted_operator = (
                            await session.execute(
                                select(PlatformRole.codename)
                                .join(User, User.role_id == PlatformRole.id)
                                .where(
                                    User.id == effective_initiator_id,
                                    User.is_active.is_(True),
                                    PlatformRole.codename.in_(("operator", "admin")),
                                )
                            )
                        ).scalar_one_or_none()
                    if (
                        effective_initiator_id not in active_user_ids
                        or (initiator_membership is None and trusted_operator is None)
                    ):
                        raise PersonalBillingUnavailable("Interactive task initiator is no longer an active member")
                initiated_by_user_id = effective_initiator_id
            else:
                if actor_id is None:
                    raise PersonalBillingUnavailable("Interactive dispatch requires an authenticated payer")
                initiating_user = (
                    await session.execute(
                        select(User).where(User.id == actor_id, User.is_active.is_(True)).with_for_update()
                    )
                ).scalar_one_or_none()
                membership = (
                    await session.execute(
                        select(TenantUser.id).where(
                            TenantUser.tenant_id == tenant_id,
                            TenantUser.user_id == actor_id,
                            TenantUser.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                admin_probe = purpose == "admin_probe"
                platform_admin = None
                if admin_probe:
                    platform_admin = (
                        await session.execute(
                            select(PlatformRole.codename)
                            .join(User, User.role_id == PlatformRole.id)
                            .where(User.id == actor_id, User.is_active.is_(True), PlatformRole.codename == "admin")
                        )
                    ).scalar_one_or_none()
                if initiating_user is None or (admin_probe and platform_admin is None) or (not admin_probe and membership is None):
                    raise PersonalBillingUnavailable("Active workspace member is required")
                payer_id = actor_id
                initiated_by_user_id = actor_id

            owner = (
                await session.execute(
                    select(User).where(User.id == payer_id, User.is_active.is_(True)).with_for_update()
                )
            ).scalar_one_or_none()
            account = (
                await session.execute(
                    select(PersonalBillingAccount)
                    .where(
                        PersonalBillingAccount.user_id == payer_id,
                        PersonalBillingAccount.is_active.is_(True),
                        PersonalBillingAccount.configured_at.is_not(None),
                        PersonalBillingAccount.policy_version == POLICY_VERSION,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if owner is None or account is None or account.daily_limit_usd is None:
                raise PersonalBillingUnavailable("Payer has no configured personal daily limit")
            cutover = (
                await session.execute(
                    select(PersonalBillingCutover)
                    .where(PersonalBillingCutover.account_id == account.id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if now.date() != receipt["utc_day"]:
                raise PersonalBillingUnavailable("UTC billing day changed while waiting for account locks; retry")
            if cutover is None or cutover.effective_at > now:
                raise PersonalBillingUnavailable("Reviewed clean-day billing boundary is not active")

            frozen_day = (
                await session.execute(
                    select(PersonalLLMBudgetDay.id)
                    .where(PersonalLLMBudgetDay.account_id == account.id, PersonalLLMBudgetDay.frozen.is_(True))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if frozen_day is not None:
                raise BudgetAdmissionDenied("Personal account is frozen by an unresolved billing discrepancy")

            policy = (
                await session.execute(
                    select(PersonalLLMBoundPolicy)
                    .where(
                        PersonalLLMBoundPolicy.provider_row_id == receipt["llm_provider_row_id"],
                        PersonalLLMBoundPolicy.model_row_id == receipt["llm_model_row_id"],
                        PersonalLLMBoundPolicy.provider_name == receipt["provider_name"],
                        PersonalLLMBoundPolicy.model_id == receipt["model_id"],
                    )
                    .order_by(PersonalLLMBoundPolicy.created_at.desc(), PersonalLLMBoundPolicy.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            ceiling_state, ceiling, bound_version = self._request_bound(policy, request_shape, receipt)
            cap = account.daily_limit_usd
            if cap == 0:
                cap_snapshot = None
            else:
                cap_snapshot = cap
                if ceiling_state == "unbounded":
                    raise BudgetAdmissionDenied("Capped payer requires a reviewed provider/model request bound")

            day = now.date()
            if day < cutover.effective_at.date():
                raise PersonalBillingUnavailable("Billing boundary has not started")
            budget_day = (
                await session.execute(
                    select(PersonalLLMBudgetDay)
                    .where(PersonalLLMBudgetDay.account_id == account.id, PersonalLLMBudgetDay.utc_day == day)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if budget_day is None:
                budget_day = PersonalLLMBudgetDay(
                    account_id=account.id,
                    utc_day=day,
                    settled_usd=Decimal(0),
                    reserved_usd=Decimal(0),
                    undefined_count=0,
                    unbounded_count=0,
                    unresolved_count=0,
                    frozen=False,
                    baseline_version=f"{BASELINE_VERSION}:{cutover.id}",
                )
                session.add(budget_day)
                await session.flush()
            if budget_day.frozen:
                raise BudgetAdmissionDenied("Personal daily ledger is frozen for review")
            previous_open = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(PersonalLLMReservationState.reserved_usd), 0),
                        func.count(PersonalLLMReservationState.id),
                        func.count(PersonalLLMReservationState.id).filter(
                            PersonalLLMReservation.ceiling_state == "unbounded"
                        ),
                    ).where(
                        PersonalLLMReservationState.account_id == account.id,
                        PersonalLLMReservationState.utc_day < day,
                        PersonalLLMReservationState.status == "reserved",
                    ).join(
                        PersonalLLMReservation,
                        PersonalLLMReservation.dispatch_id == PersonalLLMReservationState.dispatch_id,
                    )
                )
            ).one()
            previous_reserved, previous_count, previous_unbounded = (
                Decimal(previous_open[0]),
                int(previous_open[1]),
                int(previous_open[2]),
            )
            if cap_snapshot is not None and ceiling_state != "undefined" and previous_count:
                if (
                    ceiling_state == "unbounded"
                    or previous_unbounded
                    or previous_reserved + budget_day.settled_usd + budget_day.reserved_usd + (ceiling or 0)
                    > cap_snapshot
                ):
                    raise BudgetAdmissionDenied("Prior-day unresolved liability consumes capped capacity")
            if cap_snapshot is not None and ceiling_state != "undefined":
                if budget_day.unbounded_count or (
                    ceiling_state != "undefined"
                    and budget_day.settled_usd + budget_day.reserved_usd + previous_reserved + (ceiling or 0)
                    > cap_snapshot
                ):
                    raise BudgetAdmissionDenied("Personal daily limit would be exceeded")

            receipt_record = dict(receipt)
            if not await LLMDispatchReceiptManager()._record_in_session(receipt_record, session):
                raise PersonalDispatchConflict("Dispatch receipt identity already exists")
            dispatch = PersonalLLMDispatch(
                **{field: receipt[field] for field in RECEIPT_FIELDS},
                account_id=account.id,
                payer_user_id=payer_id,
                initiated_by_user_id=initiated_by_user_id,
            )
            session.add(dispatch)
            await session.flush()
            reservation = PersonalLLMReservation(
                dispatch_id=dispatch_id,
                account_id=account.id,
                utc_day=day,
                ceiling_state=ceiling_state,
                ceiling_usd=ceiling,
                bound_version=bound_version,
                undefined_policy_version=UNDEFINED_POLICY_VERSION,
                cap_usd=cap_snapshot,
            )
            session.add(reservation)
            state = PersonalLLMReservationState(
                dispatch_id=dispatch_id,
                account_id=account.id,
                utc_day=day,
                status="reserved",
                reserved_usd=ceiling or Decimal(0),
                settled_usd=Decimal(0),
            )
            session.add(state)
            await session.flush()
            session.add(
                PersonalLLMLedgerEntry(
                    entry_key=uuid4().hex,
                    dispatch_id=dispatch_id,
                    account_id=account.id,
                    utc_day=day,
                    entry_kind="reserve",
                    reserved_delta_usd=ceiling or Decimal(0),
                    settled_delta_usd=Decimal(0),
                )
            )
            budget_day.reserved_usd += ceiling or Decimal(0)
            budget_day.unresolved_count += 1
            if ceiling_state == "undefined":
                budget_day.undefined_count += 1
            if ceiling_state == "unbounded":
                budget_day.unbounded_count += 1
            await session.flush()
            admission = dict(receipt)
            if policy is not None and ceiling_state == "bounded":
                admission["__personal_max_output_tokens"] = policy.max_output_tokens
            return admission

    async def observe_event(self, *, tenant_id: int, event_id: str) -> bool:
        """Settle only a persisted receipt-bound event; never accept caller costs."""
        from ..billing.dispatch import PersonalLLMDispatch
        from ..billing.ledger import (
            PersonalLLMBillingIncident,
            PersonalLLMBudgetDay,
            PersonalLLMLedgerEntry,
            PersonalLLMReservation,
            PersonalLLMReservationState,
        )
        from ..llm.llm_dispatch_receipt import LLMDispatchReceipt
        from ..llm.llm_outcome_event import LLMOutcomeEvent

        if type(tenant_id) is not int or tenant_id <= 0 or type(event_id) is not str or len(event_id) != 32:
            raise ValueError("Explicit event and workspace identities are required")
        async with self._get_owned_session() as session:
            event = (
                await session.execute(
                    select(LLMOutcomeEvent).where(
                        LLMOutcomeEvent.event_id == event_id,
                        LLMOutcomeEvent.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if event is None:
                raise PersonalDispatchConflict("Stored outcome event is unavailable")
            personal = (
                await session.execute(
                    select(PersonalLLMDispatch).where(
                        PersonalLLMDispatch.dispatch_id == event.dispatch_id,
                        PersonalLLMDispatch.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if personal is None:
                raise PersonalDispatchConflict("Outcome is not bound to a personal dispatch")
            from ..billing.billing import PersonalBillingAccount

            account = (
                await session.execute(
                    select(PersonalBillingAccount)
                    .where(PersonalBillingAccount.id == personal.account_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if account is None:
                raise PersonalDispatchConflict("Personal account snapshot is missing")
            receipt = (
                await session.execute(
                    select(LLMDispatchReceipt).where(
                        LLMDispatchReceipt.dispatch_id == event.dispatch_id,
                        LLMDispatchReceipt.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if receipt is None:
                raise PersonalDispatchConflict("Legacy receipt bridge is missing")
            receipt_mapping = {field: getattr(receipt, field) for field in RECEIPT_FIELDS}
            event_mapping = {field: getattr(event, field) for field in EVENT_FIELDS}
            validate_outcome_event(event_mapping, receipt_mapping)

            reservation = (
                await session.execute(
                    select(PersonalLLMReservation).where(PersonalLLMReservation.dispatch_id == event.dispatch_id)
                )
            ).scalar_one_or_none()
            if reservation is None:
                raise PersonalDispatchConflict("Personal reservation state is missing")
            state = (
                await session.execute(
                    select(PersonalLLMReservationState)
                    .where(PersonalLLMReservationState.dispatch_id == event.dispatch_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if state is None:
                raise PersonalDispatchConflict("Personal reservation state is missing")
            prior = (
                await session.execute(
                    select(PersonalLLMLedgerEntry).where(
                        PersonalLLMLedgerEntry.event_id == event_id,
                        PersonalLLMLedgerEntry.entry_kind.in_(["observation", "settlement", "release"]),
                    )
                )
            ).scalar_one_or_none()
            if prior is not None:
                return False
            day = (
                await session.execute(
                    select(PersonalLLMBudgetDay)
                    .where(
                        PersonalLLMBudgetDay.account_id == personal.account_id,
                        PersonalLLMBudgetDay.utc_day == reservation.utc_day,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if day is None:
                raise PersonalDispatchConflict("Personal reservation day is missing")
            if state.status != "reserved":
                if (
                    event.event_kind == "reconciliation"
                    and state.status == "settled"
                    and event.dispatch_state == "response_received"
                    and event.outcome in {"succeeded", "failed_output"}
                    and event.cost_certainty in {"priced", "free"}
                    and event.cost_usd is not None
                ):
                    reconciled = exact_usd(Decimal(event.cost_usd))
                    settled_delta = reconciled - Decimal(state.settled_usd)
                    day.settled_usd += settled_delta
                    state.settled_usd = reconciled
                    state.latest_observed_at = event.observed_at
                    state.latest_event_id = event_id
                    breach = reservation.ceiling_usd is not None and reconciled > reservation.ceiling_usd
                    overrun = reservation.cap_usd is not None and day.settled_usd + day.reserved_usd > reservation.cap_usd
                    if breach or overrun:
                        day.frozen = True
                        session.add(
                            PersonalLLMBillingIncident(
                                event_id=event_id,
                                dispatch_id=event.dispatch_id,
                                account_id=personal.account_id,
                                utc_day=reservation.utc_day,
                                incident_kind=(
                                    "bound_and_cap_overrun" if breach and overrun
                                    else "bound_breach" if breach else "cap_overrun"
                                ),
                                reserved_usd=reservation.ceiling_usd or Decimal(0),
                                actual_usd=reconciled,
                            )
                        )
                    session.add(
                        PersonalLLMLedgerEntry(
                            entry_key=uuid4().hex,
                            dispatch_id=event.dispatch_id,
                            account_id=personal.account_id,
                            utc_day=reservation.utc_day,
                            event_id=event_id,
                            entry_kind="settlement",
                            reserved_delta_usd=Decimal(0),
                            settled_delta_usd=settled_delta,
                        )
                    )
                    await session.flush()
                    return True
                day.frozen = True
                existing_incident = (
                    await session.execute(
                        select(PersonalLLMBillingIncident).where(PersonalLLMBillingIncident.event_id == event_id)
                    )
                ).scalar_one_or_none()
                if existing_incident is not None:
                    await session.commit()
                    return False
                session.add(
                    PersonalLLMBillingIncident(
                        event_id=event_id,
                        dispatch_id=event.dispatch_id,
                        account_id=personal.account_id,
                        utc_day=reservation.utc_day,
                        incident_kind="terminal_conflict",
                        reserved_usd=reservation.ceiling_usd or Decimal(0),
                        actual_usd=event.cost_usd,
                    )
                )
                await session.flush()
                await session.commit()
                raise PersonalDispatchConflict("Terminal billing state requires explicit reconciliation")
            if state.latest_observed_at is not None and event.observed_at < state.latest_observed_at:
                raise PersonalDispatchConflict("Outcome event predates the latest accepted observation")

            if event.cost_certainty == "not_dispatched":
                new_settled = Decimal(0)
                new_reserved = Decimal(0)
                status = "released"
                kind = "release"
                resolved = True
            elif (
                event.dispatch_state == "response_received"
                and event.outcome in {"succeeded", "failed_output"}
                and event.cost_certainty in {"priced", "free"}
                and event.cost_usd is not None
            ):
                new_settled = exact_usd(Decimal(event.cost_usd))
                new_reserved = Decimal(0)
                status = "settled"
                kind = "settlement"
                resolved = True
            else:
                # Keep the committed ceiling until a final priced/free or no-dispatch event arrives.
                new_settled = state.settled_usd
                new_reserved = state.reserved_usd
                status = state.status if state.status != "reserved" else "reserved"
                kind = "observation"
                resolved = False
                if state.status != "reserved":
                    day.frozen = True

            old_settled, old_reserved = Decimal(state.settled_usd), Decimal(state.reserved_usd)
            settled_delta, reserved_delta = new_settled - old_settled, new_reserved - old_reserved
            day.settled_usd += settled_delta
            day.reserved_usd += reserved_delta
            if resolved and state.status == "reserved":
                day.unresolved_count = max(0, day.unresolved_count - 1)
                if reservation.ceiling_state == "undefined":
                    day.undefined_count = max(0, day.undefined_count - 1)
                if reservation.ceiling_state == "unbounded":
                    day.unbounded_count = max(0, day.unbounded_count - 1)
            state.status = status
            state.reserved_usd = new_reserved
            state.settled_usd = new_settled
            state.latest_observed_at = event.observed_at
            state.latest_event_id = event_id
            bound_breach = resolved and reservation.ceiling_usd is not None and new_settled > reservation.ceiling_usd
            cap_overrun = (
                resolved
                and reservation.cap_usd is not None
                and day.settled_usd + day.reserved_usd > reservation.cap_usd
            )
            if bound_breach or cap_overrun:
                day.frozen = True
            if (bound_breach or cap_overrun) and event.cost_usd is not None:
                incident_kind = (
                    "bound_and_cap_overrun"
                    if bound_breach and cap_overrun
                    else "bound_breach" if bound_breach else "cap_overrun"
                )
                session.add(
                    PersonalLLMBillingIncident(
                        event_id=event_id,
                        dispatch_id=event.dispatch_id,
                        account_id=personal.account_id,
                        utc_day=reservation.utc_day,
                        incident_kind=incident_kind,
                        reserved_usd=reservation.ceiling_usd or Decimal(0),
                        actual_usd=new_settled,
                    )
                )
            session.add(
                PersonalLLMLedgerEntry(
                    entry_key=uuid4().hex,
                    dispatch_id=event.dispatch_id,
                    account_id=personal.account_id,
                    utc_day=reservation.utc_day,
                    event_id=event_id,
                    entry_kind=kind,
                    reserved_delta_usd=reserved_delta,
                    settled_delta_usd=settled_delta,
                )
            )
            await session.flush()
            return True
