"""Atomic idempotent token and USD settlement for admitted LLM dispatches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, current_tenant_id, tenant_scope
from app.models import (
    LLMBudgetDay,
    LLMBudgetReservation,
    LLMDispatchReceipt,
    LLMDispatchReservationEvent,
    LLMDispatchReservationState,
    LLMOutcomeEvent,
    LLMTokenAllowanceCycle,
    LLMTokenAllowanceReservation,
)
from app.services.ai.dispatch_receipts import RECEIPT_FIELDS
from app.services.ai.dispatch_seam import DispatchAdmission
from app.services.ai.outcome_events import EVENT_FIELDS, build_outcome_event, validate_outcome_event

logger = logging.getLogger(__name__)

MAX_RECONCILE_BATCH = 100
RECONCILE_RETRY = timedelta(minutes=5)
_UNSET = object()


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    return repr(value)


def _field(event: Any, name: str) -> Any:
    return event.get(name) if isinstance(event, Mapping) else getattr(event, name, None)


def _tokens(event: Any, cache_usage: Any = None) -> int | None:
    if _field(event, "dispatch_state") == "not_dispatched":
        return 0
    prompt, completion = _field(event, "prompt_tokens"), _field(event, "completion_tokens")
    if prompt is None or completion is None:
        return None
    total = int(prompt) + int(completion)
    cache = cache_usage if cache_usage is not None else _field(event, "cache_usage")
    if isinstance(cache, Mapping):
        from app.services.ai.cache_pricing import validate_cache_usage

        cache = validate_cache_usage(cache)
        if cache is None:
            return None
        if cache.get("format") == "anthropic":
            reads, writes = cache.get("cache_read_tokens"), cache.get("cache_write_tokens")
            # Anthropic input_tokens excludes separately reported cache buckets.
            if type(reads) is not int or reads < 0 or type(writes) is not int or writes < 0:
                return None
            total += int(reads) + int(writes)
        elif cache.get("format") == "openai":
            # OpenAI prompt_tokens already includes cache reads/writes.
            pass
        elif cache.get("format") == "unknown":
            return None
        else:
            return None
    return total


def _event_row(event: Any) -> dict[str, Any]:
    return {field: getattr(event, field) for field in EVENT_FIELDS}


class LLMSettlementService:
    """Settle one receipt identity; provider evidence is frozen before balances move."""

    async def settle(
        self,
        admission: DispatchAdmission,
        *,
        outcome: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(admission, DispatchAdmission) or not admission.reserved:
            raise ValueError("A committed dispatch admission is required")
        tenant_id = admission.context.tenant_id
        dispatch_id = admission.dispatch_id
        if type(tenant_id) is not int or type(dispatch_id) is not str:
            raise TenantContextError("Settlement requires the admitted workspace and dispatch")
        if current_tenant_id() != tenant_id:
            raise TenantContextError("Settlement workspace differs from the active tenant scope")
        if outcome not in {"succeeded", "failed_output", "failed_transport", "unknown", "not_dispatched"}:
            outcome = "unknown"
        envelope = dict(evidence or {})
        if set(envelope) - {
            "accounting_usage",
            "accounting_cache_usage",
            "accounting_cost_reported",
            "accounting_cost_usd",
        }:
            raise ValueError("Settlement accepts accounting evidence only")
        evidence_hash = hashlib.sha256(
            json.dumps(
                _canonical({"outcome": outcome, "evidence": envelope}), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        event_id = hashlib.sha256(f"{dispatch_id}:{evidence_hash}".encode()).hexdigest()[:32]
        async with async_session_maker() as session:
            async with session.begin():
                receipt = (
                    await session.execute(
                        select(LLMDispatchReceipt).where(
                            LLMDispatchReceipt.dispatch_id == dispatch_id,
                            LLMDispatchReceipt.tenant_id == tenant_id,
                        )
                    )
                ).scalar_one_or_none()
                state = (
                    await session.execute(
                        select(LLMDispatchReservationState)
                        .where(LLMDispatchReservationState.dispatch_id == dispatch_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                token_reservation = (
                    await session.execute(
                        select(LLMTokenAllowanceReservation).where(
                            LLMTokenAllowanceReservation.dispatch_id == dispatch_id
                        )
                    )
                ).scalar_one_or_none()
                if receipt is None or state is None or token_reservation is None:
                    raise ValueError("Dispatch reservation evidence is incomplete")
                receipt_map = {field: getattr(receipt, field) for field in RECEIPT_FIELDS}
                prior_event = (
                    await session.execute(
                        select(LLMOutcomeEvent).where(
                            LLMOutcomeEvent.event_id == event_id,
                            LLMOutcomeEvent.tenant_id == tenant_id,
                        )
                    )
                ).scalar_one_or_none()
                event_record = build_outcome_event(
                    receipt_map,
                    event_id=event_id,
                    observed_at=prior_event.observed_at if prior_event is not None else datetime.now(timezone.utc),
                    dispatch_state=(
                        "not_dispatched"
                        if outcome == "not_dispatched"
                        else ("response_received" if outcome in {"succeeded", "failed_output"} else "possible")
                    ),
                    outcome=outcome,
                    event_kind=(
                        prior_event.event_kind
                        if prior_event is not None
                        else "observation" if state.latest_event_key is None else "reconciliation"
                    ),
                    accounting_envelope=envelope,
                )
                event_record["tenant_id"] = tenant_id
                validated = validate_outcome_event(event_record, receipt_map)
                usage = envelope.get("accounting_usage")
                reported_total = usage.get("total_tokens") if isinstance(usage, Mapping) else None
                component_total = _tokens(validated, envelope.get("accounting_cache_usage"))
                if type(reported_total) is not int or not 0 <= reported_total <= 2**63 - 1:
                    reported_total = component_total
                elif (
                    isinstance(envelope.get("accounting_cache_usage"), Mapping)
                    and envelope["accounting_cache_usage"].get("format") == "anthropic"
                    and component_total is None
                ):
                    reported_total = None
                elif component_total is not None and reported_total < component_total:
                    raise ValueError("Provider total token evidence conflicts with its reported components")
                if outcome == "not_dispatched":
                    reported_total = 0
                inserted = (
                    await session.execute(
                        insert(LLMOutcomeEvent.__table__)
                        .values(**validated)
                        .on_conflict_do_nothing(constraint="uq_llm_outcome_events_identity")
                        .returning(LLMOutcomeEvent.id)
                    )
                ).scalar_one_or_none()
                if inserted is None:
                    prior = (
                        await session.execute(
                            select(LLMOutcomeEvent).where(
                                LLMOutcomeEvent.event_id == event_id,
                                LLMOutcomeEvent.tenant_id == tenant_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if prior is None or any(getattr(prior, key) != validated[key] for key in EVENT_FIELDS):
                        raise ValueError("Outcome event identity conflicts with stored evidence")
                await self._apply_event(
                    session,
                    event_record,
                    state,
                    token_reservation,
                    evidence_hash,
                    actual_tokens_override=reported_total,
                )
                if token_reservation.funding_class == "personal":
                    # Same transaction: personal USD manager consumes this persisted immutable event.
                    from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager

                    await PersonalLLMBillingManager().observe_event(
                        tenant_id=tenant_id, event_id=event_id, session=session
                    )

    async def _apply_event(
        self, session, record, state, token_reservation, evidence_hash, *, actual_tokens_override=_UNSET
    ):
        from app.services.ai.budget_admission import exact_usd

        dispatch_id = record["dispatch_id"]
        tenant_id = record["tenant_id"]
        now = record["observed_at"]
        event = (
            await session.execute(
                select(LLMOutcomeEvent).where(
                    LLMOutcomeEvent.event_id == record["event_id"], LLMOutcomeEvent.tenant_id == tenant_id
                )
            )
        ).scalar_one()
        actual_tokens = _tokens(event) if actual_tokens_override is _UNSET else actual_tokens_override
        component_tokens = _tokens(event)
        if actual_tokens is not None and component_tokens is not None and actual_tokens < component_tokens:
            raise ValueError("Provider total token evidence conflicts with its reported components")
        actual_usd = (
            Decimal(event.cost_usd)
            if event.cost_usd is not None
            else (Decimal(0) if event.cost_certainty == "not_dispatched" else None)
        )
        settled = event.cost_certainty in {"priced", "free", "not_dispatched"}
        old_tokens = state.actual_tokens
        old_usd = state.actual_usd
        if old_tokens is not None and actual_tokens is not None and old_tokens != actual_tokens:
            raise ValueError("Conflicting final token evidence for dispatch")
        if old_usd is not None and settled and actual_usd is not None and Decimal(old_usd) != actual_usd:
            raise ValueError("Conflicting final USD evidence for dispatch")
        row = (
            await session.execute(
                select(LLMDispatchReservationEvent).where(
                    LLMDispatchReservationEvent.dispatch_id == dispatch_id,
                    LLMDispatchReservationEvent.event_key == record["event_id"],
                )
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(
                LLMDispatchReservationEvent(
                    dispatch_id=dispatch_id,
                    event_key=record["event_id"],
                    event_kind=(
                        "not_dispatched"
                        if event.cost_certainty == "not_dispatched"
                        else ("settled" if settled else "unresolved")
                    ),
                    actual_tokens=actual_tokens,
                    actual_usd=actual_usd,
                    evidence_fingerprint=evidence_hash,
                    observed_at=now,
                )
            )
        elif (row.actual_tokens, row.actual_usd, row.evidence_fingerprint) != (
            actual_tokens,
            actual_usd,
            evidence_hash,
        ):
            raise ValueError("Settlement event key conflicts with stored evidence")

        # Token and USD legs settle independently. Unknown usage/cost retains only
        # that leg's original reservation; UTC rollover never changes its cycle/day.
        reservation = day = None
        if token_reservation.funding_class == "platform":
            reservation = (
                await session.execute(
                    select(LLMBudgetReservation).where(LLMBudgetReservation.dispatch_id == dispatch_id)
                )
            ).scalar_one()
            day = (
                await session.execute(
                    select(LLMBudgetDay)
                    .where(LLMBudgetDay.tenant_id == tenant_id, LLMBudgetDay.utc_day == reservation.utc_day)
                    .with_for_update()
                )
            ).scalar_one()
        else:
            from app.models.billing.billing import PersonalBillingAccount
            from app.models.billing.dispatch import PersonalLLMDispatch
            from app.models.billing.ledger import PersonalLLMReservation

            personal = (
                await session.execute(
                    select(PersonalLLMDispatch).where(
                        PersonalLLMDispatch.dispatch_id == dispatch_id,
                        PersonalLLMDispatch.tenant_id == tenant_id,
                    )
                )
            ).scalar_one()
            await session.execute(
                select(PersonalBillingAccount).where(PersonalBillingAccount.id == personal.account_id).with_for_update()
            )
            reservation = (
                await session.execute(
                    select(PersonalLLMReservation).where(PersonalLLMReservation.dispatch_id == dispatch_id)
                )
            ).scalar_one()
            day = (
                await session.execute(
                    select(LLMDispatchReceipt.utc_day).where(LLMDispatchReceipt.dispatch_id == dispatch_id)
                )
            ).scalar_one()
            from app.models.billing.ledger import PersonalLLMBudgetDay

            await session.execute(
                select(PersonalLLMBudgetDay)
                .where(PersonalLLMBudgetDay.account_id == personal.account_id, PersonalLLMBudgetDay.utc_day == day)
                .with_for_update()
            )

        cycle = (
            await session.execute(
                select(LLMTokenAllowanceCycle)
                .where(LLMTokenAllowanceCycle.id == token_reservation.cycle_id)
                .with_for_update()
            )
        ).scalar_one()
        previous_tokens = state.actual_tokens
        if actual_tokens is not None and previous_tokens is None:
            if int(cycle.reserved_tokens) < int(token_reservation.reserved_tokens):
                raise ValueError("Token reservation balance underflow")
            cycle.reserved_tokens -= int(token_reservation.reserved_tokens)
            cycle.consumed_tokens += actual_tokens
            state.actual_tokens = actual_tokens
            if actual_tokens > token_reservation.reserved_tokens:
                logger.warning("llm_dispatch_token_bound_breach dispatch_id=%s", dispatch_id)

        if settled and actual_usd is not None and old_usd is None:
            state.actual_usd = exact_usd(actual_usd)

        if token_reservation.funding_class == "platform" and settled and actual_usd is not None and old_usd is None:
            amount_reserved = reservation.ceiling_usd or Decimal(0)
            if Decimal(day.reserved_usd) < amount_reserved:
                raise ValueError("Workspace USD reservation balance underflow")
            if reservation.ceiling_usd is not None and actual_usd > reservation.ceiling_usd:
                day.frozen = True
            day.reserved_usd -= amount_reserved
            day.settled_usd += exact_usd(actual_usd)
            if day.unbounded_count < int(reservation.ceiling_state == "unbounded"):
                raise ValueError("Workspace unbounded count underflow")
            if day.undefined_count < int(reservation.ceiling_state == "undefined"):
                raise ValueError("Workspace undefined count underflow")
            day.undefined_count -= int(reservation.ceiling_state == "undefined")
            day.unbounded_count -= int(reservation.ceiling_state == "unbounded")
            if reservation.cap_usd is not None and day.settled_usd + day.reserved_usd > reservation.cap_usd:
                day.frozen = True
        elif (
            token_reservation.funding_class == "platform" and settled and actual_usd is not None and old_usd is not None
        ):
            day.settled_usd += exact_usd(actual_usd) - Decimal(old_usd)
            if reservation.cap_usd is not None and day.settled_usd + day.reserved_usd > reservation.cap_usd:
                day.frozen = True
        if (
            settled
            and actual_usd is not None
            and reservation is not None
            and token_reservation.funding_class == "personal"
        ):
            if reservation.ceiling_usd is not None and actual_usd > reservation.ceiling_usd:
                # The personal ledger records/freeze-signals this bound breach itself.
                logger.warning("personal_llm_settlement_bound_breach dispatch_id=%s", dispatch_id)
        state.latest_event_key = record["event_id"]
        state.status = (
            "not_dispatched"
            if event.cost_certainty == "not_dispatched"
            else "settled" if state.actual_tokens is not None and state.actual_usd is not None else "unresolved"
        )
        state.updated_at = now

    async def reconcile_once(self, *, limit: int = 50) -> int:
        """Inspect a bounded set of expired states; accounting only, never resend HTTP."""
        if type(limit) is not int or not 0 < limit <= MAX_RECONCILE_BATCH:
            raise ValueError("Reconcile batch must be between 1 and 100")
        processed = [0]
        try:
            async with asyncio.timeout(30):
                return await self._reconcile_pass(limit, processed)
        except TimeoutError:
            logger.warning("llm_dispatch_reconcile_timed_out processed=%s", processed[0])
            return processed[0]

    async def _reconcile_pass(self, limit: int, progress: list[int]) -> int:
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session:
            await session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            await session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
            rows = (
                await session.execute(
                    select(LLMDispatchReservationState.dispatch_id, LLMDispatchReceipt.tenant_id)
                    .join(LLMDispatchReceipt, LLMDispatchReceipt.dispatch_id == LLMDispatchReservationState.dispatch_id)
                    .where(
                        LLMDispatchReservationState.status.in_(("reserved", "dispatched", "unresolved")),
                        LLMDispatchReservationState.lease_until <= now,
                    )
                    .order_by(LLMDispatchReservationState.lease_until, LLMDispatchReservationState.dispatch_id)
                    .limit(limit)
                )
            ).all()
        processed = progress[0]
        for dispatch_id, tenant_id in rows:
            completed = False
            try:
                with tenant_scope(tenant_id):
                    async with async_session_maker() as session:
                        async with session.begin():
                            await session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                            await session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                            state = (
                                await session.execute(
                                    select(LLMDispatchReservationState)
                                    .where(LLMDispatchReservationState.dispatch_id == dispatch_id)
                                    .with_for_update(skip_locked=True)
                                )
                            ).scalar_one_or_none()
                            if state is None or state.status in {"settled", "not_dispatched"}:
                                continue
                            evidence = (
                                await session.execute(
                                    select(LLMOutcomeEvent)
                                    .where(
                                        LLMOutcomeEvent.dispatch_id == dispatch_id,
                                        LLMOutcomeEvent.tenant_id == tenant_id,
                                    )
                                    .order_by(LLMOutcomeEvent.observed_at.desc(), LLMOutcomeEvent.event_id.desc())
                                    .limit(1)
                                )
                            ).scalar_one_or_none()
                            token_reservation = (
                                await session.execute(
                                    select(LLMTokenAllowanceReservation).where(
                                        LLMTokenAllowanceReservation.dispatch_id == dispatch_id
                                    )
                                )
                            ).scalar_one()
                            if evidence is None:
                                # Expiry proves only an orphan, never that HTTP was absent.
                                receipt = (
                                    await session.execute(
                                        select(LLMDispatchReceipt).where(
                                            LLMDispatchReceipt.dispatch_id == dispatch_id,
                                            LLMDispatchReceipt.tenant_id == tenant_id,
                                        )
                                    )
                                ).scalar_one()
                                receipt_map = {field: getattr(receipt, field) for field in RECEIPT_FIELDS}
                                event_id = hashlib.sha256(f"{dispatch_id}:orphan-unknown".encode()).hexdigest()[:32]
                                event_record = build_outcome_event(
                                    receipt_map,
                                    event_id=event_id,
                                    observed_at=max(now, receipt.prepared_at),
                                    dispatch_state="possible",
                                    outcome="unknown",
                                    accounting_envelope={},
                                )
                                event_record["tenant_id"] = tenant_id
                                validated = validate_outcome_event(event_record, receipt_map)
                                await session.execute(
                                    insert(LLMOutcomeEvent.__table__)
                                    .values(**validated)
                                    .on_conflict_do_nothing(constraint="uq_llm_outcome_events_identity")
                                )
                                evidence = (
                                    await session.execute(
                                        select(LLMOutcomeEvent).where(
                                            LLMOutcomeEvent.event_id == event_id,
                                            LLMOutcomeEvent.tenant_id == tenant_id,
                                        )
                                    )
                                ).scalar_one()
                            event_key = evidence.event_id
                            settlement_event = (
                                await session.execute(
                                    select(LLMDispatchReservationEvent).where(
                                        LLMDispatchReservationEvent.dispatch_id == dispatch_id,
                                        LLMDispatchReservationEvent.event_key == event_key,
                                    )
                                )
                            ).scalar_one_or_none()
                            await self._apply_event(
                                session,
                                _event_row(evidence),
                                state,
                                token_reservation,
                                (
                                    settlement_event.evidence_fingerprint
                                    if settlement_event is not None and settlement_event.evidence_fingerprint
                                    else hashlib.sha256(event_key.encode()).hexdigest()
                                ),
                                actual_tokens_override=(
                                    settlement_event.actual_tokens
                                    if settlement_event is not None
                                    else (
                                        0
                                        if evidence.dispatch_state == "not_dispatched"
                                        else _tokens(evidence) if evidence.cache_usage is not None else None
                                    )
                                ),
                            )
                            if token_reservation.funding_class == "personal":
                                from app.models.managers.personal_llm_billing_manager import PersonalLLMBillingManager

                                await PersonalLLMBillingManager().observe_event(
                                    tenant_id=tenant_id, event_id=evidence.event_id, session=session
                                )
                            if state.status == "unresolved":
                                state.lease_until = now + RECONCILE_RETRY
                            completed = True
                    if completed:
                        processed += 1
                        progress[0] = processed
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("llm_dispatch_reconcile_failed dispatch_id=%s", dispatch_id)
        return processed
