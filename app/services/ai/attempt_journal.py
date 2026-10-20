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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

#: Money is exact USD at 1e-9 precision, like the other cost surfaces.
COST_SCALE = Decimal("0.000000001")

PURPOSE_ANALYSIS = "analysis"

# New monetary outcomes follow the recorded three-state pricing contract.
# Missing token counts remain independent evidence, including for explicit free.
CERTAINTY_PRICED = "priced"
CERTAINTY_FREE = "free"
CERTAINTY_UNDEFINED = "undefined"
# Import compatibility only; historical rows are not rewritten.
CERTAINTY_UNKNOWN_TARIFF = CERTAINTY_UNDEFINED
CERTAINTY_UNKNOWN_USAGE = CERTAINTY_UNDEFINED
CERTAINTY_NOT_DISPATCHED = "not_dispatched"

# Outcome vocabulary: a paid attempt is evidence regardless of its outcome.
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_FAILED_OUTPUT = "failed_output"
OUTCOME_FAILED_TRANSPORT = "failed_transport"
OUTCOME_UNKNOWN = "unknown"

STAGE_UNIFIED_SUMMARY = "unified_summary"
CACHE_RATE_FIELDS = (
    "cache_read_cost_per_1m",
    "cache_write_5m_cost_per_1m",
    "cache_write_1h_cost_per_1m",
)
PRICING_POLICY_V1 = "provider-price-v1"
PRICING_POLICY_V2 = "cache-tariff-v2"


def _tokens(value: Any) -> Optional[int]:
    """A non-negative exact int, or None — booleans and floats are not usage."""
    if type(value) is not int or not 0 <= value <= 2147483647:
        return None
    return value


def _validate_cache_usage(value: Any) -> dict[str, Any] | None:
    from app.services.ai.cache_pricing import validate_cache_usage

    return validate_cache_usage(value)


def _usage_tokens(envelope: Any) -> tuple[Optional[int], Optional[int]]:
    """Owned accounting evidence first; legacy priced/raw usage as fallback.

    Presence of accounting_usage is authoritative even if malformed: never
    fall back to numeric projection zeros or provider-supplied replacements.
    Transport failures return no usage at all, which stays unknown usage.
    """
    if not isinstance(envelope, dict):
        return None, None
    if "accounting_usage" in envelope:
        usage = envelope["accounting_usage"]
        if not isinstance(usage, dict):
            return None, None
    else:
        usage = envelope.get("usage")
    if "accounting_usage" not in envelope and not isinstance(usage, dict):
        response = envelope.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        usage = usage if isinstance(usage, dict) else None
    if usage is None:
        return None, None
    return _tokens(usage.get("prompt_tokens")), _tokens(usage.get("completion_tokens"))


def _usd_amount(value: Any) -> Optional[Decimal]:
    """A structured non-negative USD amount, never generated text or a boolean."""
    if type(value) not in (int, float, str, Decimal):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _reported_usd(value: Any) -> Optional[Decimal]:
    amount = _usd_amount(value)
    if amount is None or amount > Decimal("999999999.999999999"):
        return None
    # Extreme exponents stay unresolved, never underflow through a component sum.
    if amount != 0 and amount.adjusted() < -1000:
        return None
    return amount


def provider_cost_evidence(response: Any) -> tuple[bool, Optional[Decimal]]:
    """Recognized API cost metadata, with explicit zeros and exact Decimal math.

    Known API extensions are USD unless their currency says otherwise. A
    non-USD report stays unresolved, never converted or guessed. Totals outrank component sums;
    neither parsed model content nor legacy client usage projections are prices.
    """
    if not isinstance(response, dict):
        return False, None
    usage = response.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    currency = response.get("currency", "USD")

    def usd(mapping: dict[str, Any], fallback: Any) -> bool:
        value = mapping.get("currency", fallback)
        return type(value) is str and value.strip().upper() == "USD"

    for mapping, allowed in ((usage, usd(usage, currency)), (response, usd(response, "USD"))):
        for field in ("total_cost", "cost"):
            amount = _usd_amount(mapping.get(field))
            if amount is not None:
                return True, _reported_usd(amount) if allowed else None

    def component(fields: tuple[str, str]) -> Optional[Decimal]:
        for field in fields:
            amount = _usd_amount(usage.get(field))
            if amount is not None:
                return amount
        return None

    prompt = component(("prompt_cost", "input_cost"))
    completion = component(("completion_cost", "output_cost"))
    if prompt is None or completion is None:
        return prompt is not None or completion is not None, None
    if not usd(usage, currency):
        return True, None
    if _reported_usd(prompt) is None or _reported_usd(completion) is None:
        return True, None
    return True, _reported_usd(prompt + completion)


def provider_reported_cost_usd(response: Any) -> Optional[Decimal]:
    """Representable report amount; missing/unrepresentable never means free."""
    return provider_cost_evidence(response)[1]


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
    dispatch_id: Optional[str] = None,
) -> str:
    """Dispatch identity when present; content identity for legacy envelopes.

    Fresh analyze clients allocate the UUID before HTTP dispatch. For those
    envelopes, usage, tariff and projection/execution fields do not alter the
    identity of a persistence retry. Missing IDs retain the legacy key exactly;
    legacy-only calls with identical content can still collapse.
    """
    if dispatch_id is not None:
        if (
            type(dispatch_id) is not str
            or len(dispatch_id) != 32
            or any(char not in "0123456789abcdef" for char in dispatch_id)
        ):
            raise ValueError("Invalid analysis dispatch identity")
        identity = json.dumps(
            ["analysis_dispatch_v1", tenant_id, purpose, stage, dispatch_id],
            separators=(",", ":"),
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

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
        response = envelope.get("response")
        invalid_output = isinstance(response, dict) and response.get("error") == "invalid_structured_output"
        if invalid_output:
            outcome = OUTCOME_FAILED_OUTPUT
        elif response_error and prompt is None and completion is None:
            # A transport error without usage does not prove the call was free.
            outcome = OUTCOME_FAILED_TRANSPORT
        elif response_error:
            outcome = OUTCOME_FAILED_OUTPUT
        elif prompt is None and completion is None:
            outcome = OUTCOME_UNKNOWN
        else:
            outcome = OUTCOME_SUCCEEDED
        model_id = _request_field(envelope, "model")
        request = envelope.get("request")
        dispatch_id = request.get("dispatch_id") if isinstance(request, dict) else None
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
                    dispatch_id=dispatch_id,
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
                "cache_usage": (
                    _validate_cache_usage(envelope.get("accounting_cache_usage"))
                    if "accounting_cache_usage" in envelope
                    else None
                ),
                "pricing_policy_version": (
                    PRICING_POLICY_V2 if "accounting_cache_usage" in envelope else PRICING_POLICY_V1
                ),
                "pricing_token_unit": None,
            }
        )
        if "accounting_cost_usd" in envelope:
            reported = _reported_usd(envelope["accounting_cost_usd"])
            unresolved = envelope.get("accounting_cost_reported") is True and reported is None
        else:
            claimed, reported = provider_cost_evidence(envelope.get("response"))
            unresolved = claimed and reported is None
        if reported is not None:
            # Transient input; price_attempt removes it before manager insertion.
            records[-1]["_reported_cost_usd"] = reported
        if unresolved:
            records[-1]["_unresolved_cost_report"] = True
    return records


def _tariff_rate(value: Any) -> Optional[Decimal]:
    """Finite non-negative USD rate representable by the journal tariff column."""
    if type(value) not in (int, float, str, Decimal):
        return None
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not rate.is_finite() or not 0 <= rate <= Decimal("9999999999.99999999"):
        return None
    # Preserve the exact rate; do not silently round an unrepresentable snapshot.
    try:
        if rate != rate.quantize(Decimal("0.00000001")):
            return None
    except InvalidOperation:
        return None
    return rate


def price_attempt(record: dict[str, Any], tariff: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Attach the tariff snapshot, exact cost and cost certainty to a record.

    A valid provider report arbitrates actual cost, including an explicit zero.
    Otherwise explicit zero table rates are free, known rates plus known usage
    are priced, and uncomputable actual cost stays NULL/undefined. Tariff fields
    remain independent snapshot evidence, not a replacement for reported cost.
    """
    from app.services.ai.cache_pricing import cache_price_usd, pricing_token_unit, validate_cache_usage

    priced = dict(record)
    reported = _reported_usd(priced.pop("_reported_cost_usd", None))
    unresolved_report = priced.pop("_unresolved_cost_report", False)
    tariff_missing = not isinstance(tariff, dict)
    tariff = tariff if isinstance(tariff, dict) else {}
    input_rate = _tariff_rate(tariff.get("input_cost_per_1k"))
    output_rate = _tariff_rate(tariff.get("output_cost_per_1k"))
    policy = priced.get("pricing_policy_version") or PRICING_POLICY_V1
    if policy not in {PRICING_POLICY_V1, PRICING_POLICY_V2}:
        raise ValueError("Unsupported attempt pricing policy")
    cache_rates = (
        {field: _tariff_rate(tariff.get(field)) for field in CACHE_RATE_FIELDS}
        if policy == PRICING_POLICY_V2
        else {field: None for field in CACHE_RATE_FIELDS}
    )
    if policy == PRICING_POLICY_V1:
        priced["cache_usage"] = None
        unit = None
    else:
        if tariff_missing:
            unit = None
        elif "pricing_token_unit" not in tariff:
            unit = pricing_token_unit()
        elif tariff["pricing_token_unit"] is None:
            if any(rate is not None for rate in (input_rate, output_rate, *cache_rates.values())):
                raise ValueError("A v2 tariff snapshot with configured rates requires an explicit unit")
            unit = None
        else:
            unit = pricing_token_unit(tariff["pricing_token_unit"])
    priced.update(
        {
            "llm_model_row_id": tariff.get("id"),
            "input_cost_per_1k": input_rate,
            "output_cost_per_1k": output_rate,
            "pricing_token_unit": unit,
            **cache_rates,
        }
    )

    prompt, completion = _tokens(priced.get("prompt_tokens")), _tokens(priced.get("completion_tokens"))
    priced.update({"prompt_tokens": prompt, "completion_tokens": completion})
    if unresolved_report:
        priced.update({"cost_usd": None, "cost_certainty": CERTAINTY_UNDEFINED})
        return priced
    if reported is not None:
        priced.update(
            {
                "cost_usd": reported.quantize(COST_SCALE, rounding=ROUND_HALF_UP),
                "cost_certainty": CERTAINTY_FREE if reported == 0 else CERTAINTY_PRICED,
            }
        )
        return priced

    if policy == PRICING_POLICY_V2:
        all_rates = (input_rate, output_rate, *cache_rates.values())
        if all(rate == 0 for rate in all_rates):
            priced.update(cost_usd=Decimal(0), cost_certainty=CERTAINTY_FREE)
            return priced
        if input_rate is None or output_rate is None or (completion is None and output_rate != 0):
            priced.update(cost_usd=None, cost_certainty=CERTAINTY_UNDEFINED)
            return priced
        cache_input = cache_price_usd(
            priced.get("cache_usage"),
            {"input_cost_per_1k": input_rate, **cache_rates},
            prompt_tokens=prompt,
            pricing_unit=unit,
        )
        if cache_input is None:
            priced.update(cost_usd=None, cost_certainty=CERTAINTY_UNDEFINED)
            return priced
        with localcontext() as context:
            context.prec = 64
            cost = cache_input + Decimal(completion or 0) / unit * output_rate
            if cost > Decimal("999999999.999999999"):
                priced.update(cost_usd=None, cost_certainty=CERTAINTY_UNDEFINED)
                return priced
            rounded_cost = cost.quantize(COST_SCALE, rounding=ROUND_HALF_UP) if cost > 0 else Decimal(0)
        if rounded_cost != cost:
            priced.update(cost_usd=None, cost_certainty=CERTAINTY_UNDEFINED)
            return priced
        priced.update(cost_usd=rounded_cost, cost_certainty=CERTAINTY_PRICED)
        return priced

    if input_rate == 0 and output_rate == 0:
        priced.update({"cost_usd": Decimal(0), "cost_certainty": CERTAINTY_FREE})
        return priced
    if input_rate is None or output_rate is None:
        priced.update({"cost_usd": None, "cost_certainty": CERTAINTY_UNDEFINED})
        return priced
    if (prompt is None and input_rate != 0) or (completion is None and output_rate != 0):
        priced.update({"cost_usd": None, "cost_certainty": CERTAINTY_UNDEFINED})
        return priced

    cost = Decimal(prompt or 0) / 1000 * input_rate + Decimal(completion or 0) / 1000 * output_rate
    if cost > Decimal("999999999.999999999"):
        priced.update({"cost_usd": None, "cost_certainty": CERTAINTY_UNDEFINED})
        return priced
    cost = cost.quantize(COST_SCALE, rounding=ROUND_HALF_UP) if cost > 0 else Decimal(0)
    priced["cost_usd"] = cost
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
                **{field: getattr(model, field, None) for field in CACHE_RATE_FIELDS},
                "pricing_token_unit": getattr(model, "pricing_token_unit", None),
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

    if journal is None:
        from app.models.managers.llm_attempt_manager import LLMAttemptManager

        journal = LLMAttemptManager()
    created = 0
    for record in records:
        if await journal.record_attempt(price_attempt(record, tariffs.get(record["model_id"]))):
            created += 1
    return created


# Non-analyzer purposes. Each is a separate execution kind of the same journal;
# the column is free text, so new purposes need no schema change.
PURPOSE_CHAT = "chat"
PURPOSE_AGENT_CHAT = "agent_chat"
PURPOSE_LEARN = "learn"
PURPOSE_REFLECT = "reflect"
PURPOSE_PROMPT_SUGGEST = "prompt_suggest"
PURPOSE_MODEL_TEST = "model_test"
PURPOSE_DIGEST = "digest"

STAGE_CHAT = "chat"
STAGE_DIGEST_SUMMARY = "digest_summary"

#: Error marker of a chat try that raised after a possible dispatch.
CHAT_CALL_FAILED = "chat_call_failed"


class MissingAttemptPayer(ValueError):
    """No trusted tenant scope: evidence is never charged to a bootstrap workspace."""


def chat_attempt_envelope(
    *,
    model_id: Optional[str],
    provider_name: Optional[str],
    dispatch_id: str,
    raw: Any = None,
    failed: bool = False,
    usage_fields: tuple[str, str] = ("prompt_tokens", "completion_tokens"),
    api_format: str | None = None,
) -> dict[str, Any]:
    """Analyzer-shaped envelope for one chat HTTP attempt.

    Token evidence comes only from the provider's raw usage fields, never from
    the client's numeric projection, which defaults missing counts to zero.
    A failed try keeps no usage, so it stays a failed transport with undefined
    cost rather than a free call.
    """
    request = {"model": model_id, "provider": provider_name, "dispatch_id": dispatch_id}
    from app.services.ai.cache_pricing import normalize_cache_usage

    if failed:
        return {
            "request": request,
            "response": {"error": CHAT_CALL_FAILED},
            "accounting_cache_usage": normalize_cache_usage(None, api_format),
        }
    raw = raw if isinstance(raw, dict) else {}
    usage = raw.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    claimed, reported = provider_cost_evidence(raw)
    return {
        "request": request,
        "response": raw,
        "accounting_usage": {
            "prompt_tokens": _tokens(usage.get(usage_fields[0])),
            "completion_tokens": _tokens(usage.get(usage_fields[1])),
        },
        "accounting_cost_usd": str(reported) if reported is not None else None,
        "accounting_cost_reported": claimed,
        "accounting_cache_usage": normalize_cache_usage(raw, api_format),
    }


async def record_scoped_attempts(
    envelopes: Any,
    *,
    purpose: str,
    tenant_id: Optional[int],
    task_payload: Any = None,
    journal: Any = None,
    tariff_resolver: Optional[Callable[[set[str]], Coroutine[Any, Any, dict[str, Optional[dict[str, Any]]]]]] = None,
) -> int:
    """Record non-analyzer attempts (stage -> envelope) for an explicit payer.

    The payer is passed on every row, so a superuser/bypass context cannot
    silently move the evidence to the bootstrap workspace. Missing payer is
    refused. Returns the number of newly recorded attempts.
    """
    if type(tenant_id) is not int:
        raise MissingAttemptPayer("attempt_journal_payer_missing")
    records = build_attempt_records(
        envelopes,
        None,
        tenant_id=tenant_id,
        source_id=None,
        analysis_date=None,
        task_payload=task_payload,
        purpose=purpose,
    )
    if not records:
        return 0

    resolver = tariff_resolver or resolve_tariffs
    tariffs = await resolver({record["model_id"] for record in records if record["model_id"]})

    if journal is None:
        from app.models.managers.llm_attempt_manager import LLMAttemptManager

        journal = LLMAttemptManager()
    created = 0
    for record in records:
        priced = price_attempt(record, tariffs.get(record["model_id"]))
        priced["tenant_id"] = tenant_id
        if await journal.record_attempt(priced):
            created += 1
    return created


async def journal_scoped_attempts(envelopes: Any, *, purpose: str, task_payload: Any = None) -> int:
    """Best-effort journaling in the ambient tenant scope; never breaks the caller.

    Like the analyzer's bounded warning, a failure here is not durable
    accounting and is not retried; the log carries no prompt, response or
    payer values.
    """
    try:
        from app.core.tenant_context import current_tenant_id

        return await record_scoped_attempts(
            envelopes, purpose=purpose, tenant_id=current_tenant_id(), task_payload=task_payload
        )
    except MissingAttemptPayer:
        logger.warning("attempt_journal_payer_missing purpose=%s", purpose)
    except Exception:
        logger.warning("attempt_journal_write_failed purpose=%s", purpose)
    return 0
