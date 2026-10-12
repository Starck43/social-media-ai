"""Durable per-attempt accounting, separate from mutable analytics projections.

PRD-03 requires every actual provider attempt to be recorded with its own
identity, usage, tariff snapshot, cost certainty and outcome. The daily
`ai_analytics` row, `jobs.llm_cost`, `digest_runs.llm_cost` and the model
counters are mutable projections: a partial run whose daily row is skipped, a
relevance-filtered day or a reaped queue row must not take the evidence of the
paid attempt with it.

The module is dependency-free at import time (stdlib only): the builder and
pricing are pure functions, so they can be tested without database or
provider infrastructure. Persistence uses the project's existing
manager/session lifecycle and the caller runs inside the ambient tenant scope.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

#: Money is exact USD at 1e-9 precision, like the other cost surfaces.
COST_SCALE = Decimal("0.000000001")

PURPOSE_ANALYSIS = "analysis"

# Cost certainty vocabulary. "Missing usage or a configured zero tariff is not
# proof of free service": unknown usage stays unknown even with a zero tariff.
CERTAINTY_PRICED = "priced"
CERTAINTY_FREE = "free"
CERTAINTY_UNKNOWN_TARIFF = "unknown_tariff"
CERTAINTY_UNKNOWN_USAGE = "unknown_usage"
CERTAINTY_NOT_DISPATCHED = "not_dispatched"

# Outcome vocabulary: a paid attempt is evidence regardless of its outcome.
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED_OUTPUT = "failed_output"
OUTCOME_FAILED_TRANSPORT = "failed_transport"
OUTCOME_UNKNOWN = "unknown"

STAGE_UNIFIED_SUMMARY = "unified_summary"


def _tokens(value: Any) -> Optional[int]:
    """A non-negative exact int, or None — booleans and floats are not usage."""
    if type(value) is not int or value < 0:
        return None
    return value


def _usage_tokens(envelope: Any) -> tuple[Optional[int], Optional[int]]:
    """Priced client usage block first, raw provider usage as fallback.

    The client returns the priced block as a sibling of `request`/`response`;
    transport failures return no usage at all, which stays unknown usage.
    """
    if not isinstance(envelope, dict):
        return None, None
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        response = envelope.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        usage = usage if isinstance(usage, dict) else None
    if usage is None:
        return None, None
    return _tokens(usage.get("prompt_tokens")), _tokens(usage.get("completion_tokens"))


def _request_field(envelope: Any, field: str) -> Optional[str]:
    if not isinstance(envelope, dict):
        return None
    request = envelope.get("request")
    value = request.get(field) if isinstance(request, dict) else None
    return value if type(value) is str and value else None


def _response_error(envelope: Any) -> bool:
    if not isinstance(envelope, dict):
        return False
    response = envelope.get("response")
    return isinstance(response, dict) and bool(response.get("error"))


def _execution_id(task_payload: Any, field: str) -> Optional[int]:
    if not isinstance(task_payload, dict):
        return None
    value = task_payload.get(field)
    return value if type(value) is int else None


def attempt_key(
    *,
    tenant_id: Optional[int],
    purpose: str,
    stage: str,
    source_id: Optional[int],
    analysis_date: Optional[date],
    job_id: Optional[int],
    agent_task_id: Optional[int],
    model_id: Optional[str],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
) -> str:
    """Content-derived identity: repeat writes of the same attempt are no-ops.

    The key deliberately excludes any dispatch timestamp, so a retried
    persistence of one execution records once; a later execution with
    different usage, model or scope is a distinct attempt. Per-dispatch
    identity (run nonce) arrives with the client-level capture slice.
    """
    identity = json.dumps(
        [
            tenant_id,
            purpose,
            stage,
            source_id,
            analysis_date.isoformat() if isinstance(analysis_date, date) else None,
            job_id,
            agent_task_id,
            model_id or "",
            prompt_tokens,
            completion_tokens,
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def build_attempt_records(
    analysis_results: Any,
    unified_summary: Any,
    *,
    tenant_id: Optional[int],
    source_id: Optional[int],
    analysis_date: Optional[date] = None,
    task_payload: Any = None,
    purpose: str = PURPOSE_ANALYSIS,
) -> list[dict[str, Any]]:
    """Pure attempt records (without pricing) for one analyzer execution.

    Every stage envelope of the run becomes one record, including failed
    envelopes: a provider that answered with an error still spent the request.
    """
    envelopes: list[tuple[str, Any]] = []
    if isinstance(analysis_results, dict):
        envelopes.extend(
            (stage, envelope) for stage, envelope in analysis_results.items() if isinstance(envelope, dict)
        )
    if isinstance(unified_summary, dict):
        envelopes.append((STAGE_UNIFIED_SUMMARY, unified_summary))

    job_id = _execution_id(task_payload, "job_id")
    agent_task_id = _execution_id(task_payload, "agent_task_id")

    records: list[dict[str, Any]] = []
    for stage, envelope in envelopes:
        prompt, completion = _usage_tokens(envelope)
        response_error = _response_error(envelope)
        if response_error and prompt is None and completion is None:
            # No usage block at all: the call never reached a billable response.
            outcome = OUTCOME_FAILED_TRANSPORT
        elif response_error:
            outcome = OUTCOME_FAILED_OUTPUT
        elif prompt is None and completion is None:
            outcome = OUTCOME_UNKNOWN
        else:
            outcome = OUTCOME_SUCCEEDED
        model_id = _request_field(envelope, "model")
        records.append(
            {
                "attempt_key": attempt_key(
                    tenant_id=tenant_id,
                    purpose=purpose,
                    stage=stage,
                    source_id=source_id,
                    analysis_date=analysis_date,
                    job_id=job_id,
                    agent_task_id=agent_task_id,
                    model_id=model_id,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                ),
                "purpose": purpose,
                "stage": stage,
                "source_id": source_id,
                "analysis_date": analysis_date if isinstance(analysis_date, date) else None,
                "job_id": job_id,
                "agent_task_id": agent_task_id,
                "model_id": model_id,
                "provider_name": _request_field(envelope, "provider"),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "outcome": outcome,
            }
        )
    return records


def price_attempt(record: dict[str, Any], tariff: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Attach the tariff snapshot, exact cost and cost certainty to a record.

    `tariff` is the snapshot of the `llm_models` row at record time, or None
    when no such row exists. Cost is never hardcoded and never inferred: an
    unresolvable tariff or missing usage makes the cost unknown (NULL), which
    is different from a known zero.
    """
    priced = dict(record)
    if tariff is None:
        priced.update(
            {
                "llm_model_row_id": None,
                "input_cost_per_1k": None,
                "output_cost_per_1k": None,
                "cost_usd": None,
                "cost_certainty": CERTAINTY_UNKNOWN_TARIFF,
            }
        )
        return priced

    input_rate = Decimal(str(tariff.get("input_cost_per_1k") or 0))
    output_rate = Decimal(str(tariff.get("output_cost_per_1k") or 0))
    priced.update(
        {
            "llm_model_row_id": tariff.get("id"),
            "input_cost_per_1k": input_rate,
            "output_cost_per_1k": output_rate,
        }
    )

    prompt, completion = priced.get("prompt_tokens"), priced.get("completion_tokens")
    if prompt is None or completion is None:
        priced.update({"cost_usd": None, "cost_certainty": CERTAINTY_UNKNOWN_USAGE})
        return priced

    cost = Decimal(prompt) / 1000 * input_rate + Decimal(completion) / 1000 * output_rate
    cost = cost.quantize(COST_SCALE, rounding=ROUND_HALF_UP) if cost > 0 else Decimal(0)
    priced["cost_usd"] = cost
    if cost == 0 and input_rate == 0 and output_rate == 0:
        priced["cost_certainty"] = CERTAINTY_FREE
    else:
        priced["cost_certainty"] = CERTAINTY_PRICED
    return priced


async def resolve_tariffs(model_ids: set[str]) -> dict[str, Optional[dict[str, Any]]]:
    """Snapshot `llm_models` tariffs once per unique model id (None when unknown)."""
    from app.models import LLMModel

    tariffs: dict[str, Optional[dict[str, Any]]] = {}
    for model_id in sorted(model_ids):
        model = None
        try:
            model = await LLMModel.objects.filter(model_id=model_id, is_active=True).first()
        except Exception:
            # Pricing must not break accounting: without a row the attempt is
            # recorded with an unknown tariff, not dropped.
            model = None
        tariffs[model_id] = (
            {
                "id": model.id,
                "input_cost_per_1k": model.input_cost_per_1k,
                "output_cost_per_1k": model.output_cost_per_1k,
            }
            if model is not None
            else None
        )
    return tariffs


async def record_analysis_attempts(
    analysis_results: Any,
    unified_summary: Any,
    *,
    source: Any,
    analysis_date: Optional[date] = None,
    task_payload: Any = None,
    journal: Any = None,
    tariff_resolver: Optional[Callable[[set[str]], Coroutine[Any, Any, dict[str, Optional[dict[str, Any]]]]]] = None,
) -> int:
    """Record one journal row per provider attempt of an analyzer execution.

    Returns the number of newly recorded attempts (0 when the ledger already
    holds them, or there was nothing to record). Injection points exist for
    tests; production uses the project manager and tariff lookup.
    """
    from app.models.managers.llm_attempt_manager import LLMAttemptManager

    records = build_attempt_records(
        analysis_results,
        unified_summary,
        tenant_id=getattr(source, "tenant_id", None),
        source_id=getattr(source, "id", None),
        analysis_date=analysis_date,
        task_payload=task_payload,
    )
    if not records:
        return 0

    resolver = tariff_resolver or resolve_tariffs
    tariffs = await resolver({record["model_id"] for record in records if record["model_id"]})

    journal = journal or LLMAttemptManager()
    created = 0
    for record in records:
        if await journal.record_attempt(price_attempt(record, tariffs.get(record["model_id"]))):
            created += 1
    return created
