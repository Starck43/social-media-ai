import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional
from uuid import uuid4

import httpx

from app.core.config import settings
from app.models import LLMModel
from app.services.ai.cache_pricing import cache_price_usd, model_pricing_token_unit, normalize_cache_usage
from app.services.ai.json_schema_builder import validate_with_pydantic

logger = logging.getLogger(__name__)

_last_request_time: dict[str, float] = {}
MINUTE = 60

# AgentMessage.cost / DigestRun.llm_cost are plain USD floats; quantise to 1e-9
# so sub-cent calls from cheap models never round to "free".
_COST_SCALE = Decimal("0.000000001")


def _reported_provider_cost_usd(data: Any) -> Decimal | None:
    from app.services.ai.attempt_journal import provider_cost_evidence

    return provider_cost_evidence(data)[1]


def _payload_has_media(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in value for key in ("image_url", "input_image", "media_url", "media_urls")):
            return True
        return any(_payload_has_media(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_payload_has_media(item) for item in value)
    return False


async def _personal_admission(
    client: Any,
    payload: dict[str, Any],
    *,
    purpose: str,
    stage: str,
    execution: dict[str, Any] | None = None,
    has_media: bool = False,
    uses_tools: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    """Reserve one trusted provider envelope before HTTP when personal billing is active."""
    # Flag off: no reservation, no payer, and no ORM. The check comes first so a
    # deployment that never enabled personal billing is not forced to satisfy the
    # dispatch validator (it reads the model row and the bound catalog owner).
    if not settings.PERSONAL_BILLING_ENABLED:
        return None, None
    from app.services.ai.personal_dispatch import validate_personal_dispatch

    await validate_personal_dispatch(client)
    api_format = getattr(client.provider, "api_format", None)
    adapter = {
        "openai": "openai_chat_completions",
        "anthropic": "anthropic_messages",
    }.get(api_format)
    request_shape = {
        "adapter": adapter,
        "provider_name": client.provider.name,
        "model_id": client.model_name,
        "has_media": has_media or _payload_has_media(payload),
        "uses_tools": uses_tools or bool(payload.get("tools")),
        "requested_output_tokens": payload.get("max_tokens"),
    }
    from app.services.billing.personal_ledger import reserve_personal_dispatch

    admission = dict(
        await reserve_personal_dispatch(
            client.model,
            purpose=purpose,
            stage=stage,
            execution=execution,
            request_shape=request_shape,
        )
    )
    max_output = admission.pop("__personal_max_output_tokens", None)
    if type(max_output) is int:
        from app.services.ai.budget_admission import BudgetAdmissionUnavailable

        if type(payload.get("max_tokens")) is not int or payload["max_tokens"] <= 0:
            raise BudgetAdmissionUnavailable("Request output token limit is invalid")
        payload["max_tokens"] = min(payload["max_tokens"], max_output)
    dispatch_id = admission["dispatch_id"]
    return admission, dispatch_id


async def _verify_personal_dispatch_day(receipt: dict[str, Any] | None) -> None:
    """Refuse a reservation acknowledged on a prior UTC day before HTTP.

    A proven no-dispatch release is recorded before the refusal, so the held
    reservation cannot leak past a UTC rollover.
    """
    if receipt is None:
        return
    from datetime import datetime, timezone

    from app.models.managers.personal_billing_manager import PersonalBillingUnavailable

    if receipt["utc_day"] != datetime.now(timezone.utc).date():
        try:
            await _observe_personal_failure(receipt, not_dispatched=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Failed to record not_dispatched release for prior-day reservation", exc_info=True)
        raise PersonalBillingUnavailable("UTC billing day changed after reservation; retry under the new day")


async def _observe_personal_outcome(
    receipt: dict[str, Any] | None,
    *,
    dispatch_state: str,
    outcome: str,
    accounting_envelope: dict[str, Any] | None = None,
) -> None:
    """Persist and settle one immutable event; unknown outcomes retain liability."""
    if receipt is None:
        return
    from app.models import LLMOutcomeEvent
    from app.services.ai.outcome_events import build_outcome_event
    from app.services.billing.personal_ledger import observe_personal_outcome

    try:
        event = build_outcome_event(
            receipt,
            event_id=uuid4().hex,
            observed_at=datetime.now(timezone.utc),
            dispatch_state=dispatch_state,
            outcome=outcome,
            accounting_envelope=accounting_envelope,
        )
        created = await LLMOutcomeEvent.objects.record_event(event)
        if created:
            await observe_personal_outcome(tenant_id=receipt["tenant_id"], event_id=event["event_id"])
    except asyncio.CancelledError:
        # Cancellation cannot prove non-dispatch. Best-effort durable unknown
        # observation in a shielded task; the reservation remains held either way.
        try:
            await asyncio.shield(asyncio.create_task(_observe_personal_failure(receipt)))
        except BaseException:
            pass
        raise
    except Exception as exc:
        from app.models.managers.personal_billing_manager import PersonalBillingUnavailable

        raise PersonalBillingUnavailable("Outcome accounting failed; the reservation remains held") from exc


async def _observe_personal_failure(receipt: dict[str, Any] | None, *, not_dispatched: bool = False) -> None:
    await _observe_personal_outcome(
        receipt,
        dispatch_state="not_dispatched" if not_dispatched else "possible",
        outcome="not_dispatched" if not_dispatched else "unknown",
    )


async def _observe_cancelled_personal(receipt: dict[str, Any] | None) -> None:
    """Persist an ambiguous cancellation without releasing its reservation."""
    if receipt is None:
        return
    task = asyncio.create_task(_observe_personal_failure(receipt))
    try:
        await asyncio.shield(task)
    except BaseException:
        # Preserve the original cancellation; the reservation remains durable.
        pass


async def _record_llm_usage(model: LLMModel, success: bool, usage: dict | None = None) -> None:
    """Update model usage/health counters (last_used_at, last_success_at, last_error_at, use_count, fail_count).

    One UPDATE per model attempt — the per-client chat() call is the single
    recording point; chat_with_fallback must not record on top of it.
    Naive UTC matches the plain DateTime columns; NULL counters read as 0.
    """
    try:
        now = datetime.utcnow()
        updates = {"last_used_at": now, "use_count": (model.use_count or 0) + 1}
        if success:
            updates["last_success_at"] = now
        else:
            updates["last_error_at"] = now
            updates["fail_count"] = (model.fail_count or 0) + 1
        await LLMModel.objects.update_by_id(model.id, **updates)
        # Update local object for potential re-use in same request
        for k, v in updates.items():
            setattr(model, k, v)
    except Exception:
        # Never break the main flow for metrics
        logger.debug("Failed to record LLM usage for model %s", model.id, exc_info=True)


def price_usage_usd(model: LLMModel, prompt_tokens: int, completion_tokens: int) -> float:
    """Price token usage using the model's explicit token pricing unit.

    Same formula as AIAnalyzer._price_usage but returns plain USD (that one
    returns USD cents for the ai_analytics column). Decimal throughout — tariffs
    have 4+ decimals and cheap models bill well under a cent per call; unknown
    tariffs contribute 0 (never hardcode rates).
    """
    unit = model_pricing_token_unit(model)
    usd = Decimal(prompt_tokens or 0) / unit * Decimal(str(getattr(model, "input_cost_per_1k", 0) or 0)) + (
        Decimal(completion_tokens or 0) / unit * Decimal(str(getattr(model, "output_cost_per_1k", 0) or 0))
    )
    if usd <= 0:
        return 0.0
    return float(usd.quantize(_COST_SCALE, rounding=ROUND_HALF_UP))


def _cap_text(m: LLMModel) -> bool:
    """Text-capable fleet rows, including legacy image-only text support."""
    return bool({"text", "image"}.intersection(m.capabilities))


def _eligible_text_model(m: LLMModel, allowed_types: Optional[set[str]]) -> bool:
    """One automatic routing gate for capabilities, owner, tier and provider.

    None is unfiltered; an empty allowed set permits nothing. A model with
    extra billed capabilities never bypasses the tier through its default or
    preferred flag. The explicit AGENT_MODEL lookup passes this same ownership
    and activity gate and only bypasses the tier.
    """
    from app.core.personal_catalog_context import current_catalog_owner_id
    from app.services.ai.personal_catalog import eligible_chat_model
    return eligible_chat_model(m, user_id=current_catalog_owner_id(), allowed_types=allowed_types)



def default_model_sort_key(m: LLMModel) -> tuple[bool, int]:
    """Order key for default-model resolution.

    Shared by every place that picks "the" model (digest, agent fallback
    chain, settings dropdown): the model flagged `is_default` first, then the
    lowest id. Only models carry the default flag — a provider-level flag
    used to outrank it, which silently overrode the operator's own default
    model and forked the fleet when two providers were flagged. A missing
    attribute is treated as non-default so the picker never crashes.
    """
    return (not bool(getattr(m, "is_default", False)), int(getattr(m, "id", 0) or 0))


async def _allowed_model_types() -> Optional[set[str]]:
    """Model types the ambient workspace's tier may use, or None when unfiltered.

    `starter` buys text, so an image model in the catalog must not be chosen
    for it — the tier is a promise about what is billed, and routing a `starter`
    workspace to a vision model breaks it. Returns None (allow everything) when
    no workspace is in scope, so operator-level calls and seeding still work.
    """
    from app.core.tenant_context import current_tenant_id
    from app.models.managers.tenant_manager import tenants

    tenant_id = current_tenant_id()
    if tenant_id is None:
        return None
    tenant = await tenants.get(id=tenant_id)
    if tenant is None:
        return None
    allowed = tenant.plan_limits().get("model_types")
    return None if allowed is None else set(allowed)


# ──────────────────────────────────────────────────────────────
# Base client
# ──────────────────────────────────────────────────────────────


class LLMClient(ABC):
    def __init__(self, model: LLMModel):
        from app.core.personal_catalog_context import assert_catalog_model
        assert_catalog_model(model)
        self.provider = model.provider
        self.model = model
        self.api_key = model.provider.get_api_key()
        self.base_url = model.provider.base_url.rstrip("/")
        self.model_name = model.model_id
        self.max_tokens = model.max_tokens
        self.default_temperature = model.default_temperature
        self.timeout = float(getattr(settings, "LLM_DEFAULT_TIMEOUT", 60.0))

    @classmethod
    def create(cls, model: LLMModel) -> "LLMClient":
        return LLMClientFactory.create(model)

    def _usage_block(
        self, prompt_tokens: int, completion_tokens: int, *, reported_cost_usd: Decimal | None = None
    ) -> dict[str, Any]:
        """Normalized usage: token counts + priced USD cost.

        `cost` is what the daily cap and DigestRun.llm_cost account from; keep
        the key present (0.0 when tariffs are unknown) so callers never branch.
        """
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": (
                float(reported_cost_usd.quantize(_COST_SCALE, rounding=ROUND_HALF_UP))
                if reported_cost_usd is not None
                else price_usage_usd(self.model, prompt_tokens, completion_tokens)
            ),
        }

    def _usage_block_for_response(self, prompt_tokens: int, completion_tokens: int, data: Any) -> dict[str, Any]:
        """Prefer provider USD totals, then a cache-aware tariff projection."""
        from app.services.ai.attempt_journal import provider_cost_evidence

        report_claimed, reported = provider_cost_evidence(data)
        if report_claimed:
            # A claimed but unusable provider total (non-USD, malformed, out of
            # range) is unresolved spend. Publishing 0.0 here let a paid call
            # count as free everywhere the legacy `cost` key is read.
            amount = (
                float(reported.quantize(_COST_SCALE, rounding=ROUND_HALF_UP))
                if reported is not None
                else None
            )
            return {
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
                "cost": amount,
                "cost_source": "provider" if reported is not None else "provider_unresolved",
                "cost_is_estimate": False,
            }
        try:
            result = self._usage_block(prompt_tokens, completion_tokens)
        except (ArithmeticError, TypeError, ValueError):
            result = {
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
                "cost": None,
            }
            result.update(cost_source="undefined", cost_is_estimate=True)
            return result
        if reported is not None:
            result["cost"] = float(reported.quantize(_COST_SCALE, rounding=ROUND_HALF_UP))
            result.update(cost_source="provider", cost_is_estimate=False)
            return result
        cache_usage = self._accounting_cache_usage(data)
        try:
            cache_input = cache_price_usd(
                cache_usage,
                {
                    "input_cost_per_1k": getattr(self.model, "input_cost_per_1k", None),
                    "cache_read_cost_per_1m": getattr(self.model, "cache_read_cost_per_1m", None),
                    "cache_write_5m_cost_per_1m": getattr(self.model, "cache_write_5m_cost_per_1m", None),
                    "cache_write_1h_cost_per_1m": getattr(self.model, "cache_write_1h_cost_per_1m", None),
                },
                prompt_tokens=prompt_tokens,
                pricing_unit=model_pricing_token_unit(self.model),
            )
        except (ArithmeticError, TypeError, ValueError):
            result.update(cost_source="undefined", cost_is_estimate=True)
            return result
        if cache_input is None:
            result.update(cost_source="flat_tariff_estimate", cost_is_estimate=True)
            return result
        try:
            output_rate = Decimal(str(getattr(self.model, "output_cost_per_1k", None)))
            unit = model_pricing_token_unit(self.model)
            amount = cache_input + Decimal(completion_tokens or 0) * output_rate / unit
            result["cost"] = float(amount.quantize(_COST_SCALE, rounding=ROUND_HALF_UP))
            result.update(cost_source="cache_tariff", cost_is_estimate=True)
        except (ArithmeticError, TypeError, ValueError):
            result.update(cost_source="undefined", cost_is_estimate=True)
        return result

    def _analysis_request_meta(self, prompt: str) -> dict[str, Any]:
        """Allocate a fresh local identity once, before an analyze HTTP attempt.

        This metadata is not a provider payload or an idempotency header.
        Returning it on success and failure lets persistence retry the same
        envelope without conflating another invocation with identical usage.
        """
        return {
            "model": self.model_name,
            "llm_model_id": getattr(self.model, "id", None),
            "prompt": prompt,
            "provider": self.provider.name.lower(),
            "dispatch_id": uuid4().hex,
        }

    def _accounting_usage(
        self, data: Any, prompt_field: str = "prompt_tokens", completion_field: str = "completion_tokens"
    ) -> dict[str, Optional[int]]:
        """Owned token evidence, independent of the legacy numeric projection.

        Missing, malformed or out-of-storage-range counts are unknown, not zero.
        Provider totals or response content cannot supply missing token fields.
        """
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}

        def count(field: str) -> Optional[int]:
            value = usage.get(field)
            return value if type(value) is int and 0 <= value <= 2147483647 else None

        return {"prompt_tokens": count(prompt_field), "completion_tokens": count(completion_field)}

    def _accounting_cost(self, data: Any) -> dict[str, Any]:
        """Owned reported USD evidence; None never means a free provider call."""
        from app.services.ai.attempt_journal import provider_cost_evidence

        claimed, reported = provider_cost_evidence(data)
        return {
            "accounting_cost_usd": str(reported) if reported is not None else None,
            "accounting_cost_reported": claimed,
        }

    def _accounting_cache_usage(self, data: Any) -> dict[str, Any]:
        """Provider-owned cache counters in a bounded client-normalized shape."""
        return normalize_cache_usage(data, getattr(self.provider, "api_format", None))

    def _analyze_usage_projection(
        self, prompt: Optional[int], completion: Optional[int], data: Any = None
    ) -> dict[str, Any]:
        """Legacy numeric adapter failures cannot discard a paid API envelope."""
        try:
            return self._usage_block_for_response(prompt, completion, data) if data is not None else self._usage_block(
                prompt, completion
            )
        except (ArithmeticError, TypeError, ValueError):
            prompt, completion = prompt or 0, completion or 0
            if data is not None:
                reported = _reported_provider_cost_usd(data)
                return {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                    "cost": float(reported.quantize(_COST_SCALE, rounding=ROUND_HALF_UP))
                    if reported is not None
                    else 0.0,
                    "cost_source": "provider" if reported is not None else "flat_tariff_estimate",
                    "cost_is_estimate": reported is None,
                }
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "cost": 0.0,
            }

    @abstractmethod
    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]: ...

    async def _rate_limit(self):
        key = f"{self.provider.owner_user_id}:{self.provider.id}"
        now = asyncio.get_event_loop().time()
        delay = float(getattr(settings, "LLM_REQUEST_DELAY", 2000)) / 1000.0
        if key in _last_request_time:
            elapsed = now - _last_request_time[key]
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
        _last_request_time[key] = asyncio.get_event_loop().time()


# ──────────────────────────────────────────────────────────────
# OpenAI-compatible client
# ──────────────────────────────────────────────────────────────


class OpenAICompatibleClient(LLMClient):
    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        execution = kwargs.pop("personal_billing_execution", None)
        stage = kwargs.pop("personal_billing_stage", "text_analysis")
        payload = self._prepare_analyze(prompt, media_urls, **kwargs)
        timeout = min(self.timeout, 60.0)
        request_meta = self._analysis_request_meta(prompt)
        receipt, reserved_dispatch_id = await _personal_admission(
            self, payload, purpose="analysis", stage=stage, execution=execution, has_media=bool(media_urls)
        )
        await _verify_personal_dispatch_day(receipt)
        if receipt is not None:
            request_meta["dispatch_id"] = reserved_dispatch_id

        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
        except asyncio.CancelledError:
            await _observe_cancelled_personal(receipt)
            raise
        except httpx.TimeoutException:
            await _observe_personal_failure(receipt)
            logger.error(f"Timeout for {self.provider.name} after {timeout}s")
            await _record_llm_usage(self.model, success=False)
            return {
                "request": {**payload, **request_meta},
                "response": {"error": "timeout"},
                "parsed": {"analysis": "Timeout"},
            }
        except Exception as e:
            await _observe_personal_failure(receipt)
            logger.error(f"Unexpected error for {self.provider.name}: {e}")
            await _record_llm_usage(self.model, success=False)
            return {
                "request": {**payload, **request_meta},
                "response": {"error": str(e)},
                "parsed": {"analysis": f"Error: {e}"},
            }

        accounting_usage = self._accounting_usage(data)
        accounting_cost = self._accounting_cost(data)
        parse_error = False
        try:
            parsed = self._parse_response(data)
        except Exception:
            parsed, parse_error = {}, True
        if pydantic_model is not None:
            try:
                parsed = validate_with_pydantic(parsed, pydantic_model, strict=True)
            except Exception as exc:
                logger.warning("Pydantic validation failed for %s: %s", self.provider.name, exc)
                data["error"] = "invalid_structured_output"
                parsed = {}

        response = {
            "request": request_meta,
            "response": data,
            "parsed": parsed,
            "accounting_usage": accounting_usage,
            "accounting_cache_usage": self._accounting_cache_usage(data),
            **accounting_cost,
            "usage": self._analyze_usage_projection(
                accounting_usage["prompt_tokens"], accounting_usage["completion_tokens"], data
            ),
        }
        await _observe_personal_outcome(
            receipt,
            dispatch_state="response_received",
            outcome="failed_output" if data.get("error") or parse_error else "succeeded",
            accounting_envelope=response,
        )
        await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
        return response

    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]:
        # Only the newly-created admin probe client opts into this in-memory
        # observation. Never add diagnostics to the provider payload or logs.
        diagnostic = getattr(self, "_probe_diagnostics", None)
        if isinstance(diagnostic, dict):
            diagnostic.update(stage="admission", dispatch_state="not_dispatched")
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.pop("tool_choice", "auto")
        execution = kwargs.pop("personal_billing_execution", None)
        personal_purpose = kwargs.pop("personal_billing_purpose", "agent_chat")
        payload.update(kwargs)

        timeout = min(float(payload.get("timeout", self.timeout)), 60.0)
        receipt, dispatch_id = await _personal_admission(
            self,
            payload,
            purpose=personal_purpose,
            stage="chat",
            execution=execution,
            uses_tools=bool(tools),
        )
        if isinstance(diagnostic, dict):
            diagnostic["effective_max_tokens"] = payload["max_tokens"]
        await _verify_personal_dispatch_day(receipt)

        try:
            if isinstance(diagnostic, dict):
                diagnostic.update(stage="transport", dispatch_state="possible")
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=payload, timeout=timeout
                )
                if isinstance(diagnostic, dict):
                    diagnostic.update(stage="response", dispatch_state="response_received", http_status=r.status_code)
                    try:
                        diagnostic["raw_response"] = r.json()
                    except ValueError:
                        diagnostic["response_body"] = r.text
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
                if isinstance(diagnostic, dict) and not isinstance(data, dict):
                    raise ValueError("Provider response must be a JSON object")

            if isinstance(diagnostic, dict):
                diagnostic["stage"] = "accounting"
            accounting = {
                "accounting_usage": self._accounting_usage(data),
                "accounting_cache_usage": self._accounting_cache_usage(data),
                **self._accounting_cost(data),
            }
            parse_error = False
            try:
                response = self._parse_chat(data)
            except Exception:
                response, parse_error = {
                    "content": "", "tool_calls": [], "usage": self._usage_block_for_response(0, 0, data)
                }, True
            response["_personal_dispatch_id"] = dispatch_id
            await _observe_personal_outcome(
                receipt,
                dispatch_state="response_received",
                outcome="failed_output" if parse_error else "succeeded",
                accounting_envelope=accounting,
            )
            await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
            if isinstance(diagnostic, dict):
                diagnostic.update(stage="complete", accounting_status="confirmed" if receipt is not None else "standard")
            return response
        except asyncio.CancelledError:
            await _observe_cancelled_personal(receipt)
            raise
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as error:
            await _observe_personal_failure(receipt)
            await _record_llm_usage(self.model, success=False)
            setattr(error, "personal_dispatch_id", dispatch_id)
            raise
        except Exception as error:
            from app.services.billing.errors import is_personal_billing_block

            if is_personal_billing_block(error):
                setattr(error, "personal_dispatch_id", dispatch_id)
                raise
            await _observe_personal_failure(receipt)
            setattr(error, "personal_dispatch_id", dispatch_id)
            raise

    # ── internal helpers ─────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        header_name = self.provider.auth_header or "Authorization: Bearer {key}"
        if "{key}" in header_name:
            h["Authorization"] = f"Bearer {self.api_key}"
        else:
            k, _, v = header_name.partition(": ")
            h[k] = v.format(key=self.api_key) if "{key}" in v else v
        return h

    def _prepare_analyze(self, prompt: str, media_urls: Optional[list[str]], **kwargs) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(bool(media_urls))},
        ]
        if media_urls:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url", "image_url": {"url": u}} for u in media_urls]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "stream": False,
        }
        if not media_urls:
            payload["response_format"] = {"type": "json_object"}
        payload.update(kwargs)
        return payload

    def _parse_response(self, response: dict) -> dict[str, Any]:
        choices = response.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content")
        return _try_json(content)

    def _parse_chat(self, data: dict) -> dict[str, Any]:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls: list[dict[str, Any]] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(args) if isinstance(args, str) else dict(args)
            except json.JSONDecodeError:
                parsed = {"_raw": args}
            tool_calls.append(
                {"id": tc.get("id") or fn.get("name", ""), "name": fn.get("name", ""), "arguments": parsed}
            )
        usage = data.get("usage") or {}
        block = self._usage_block_for_response(
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), data
        )
        block["total_tokens"] = usage.get("total_tokens", 0) or block["total_tokens"]
        return {
            "content": msg.get("content"),
            "tool_calls": tool_calls,
            "usage": block,
            "finish_reason": choice.get("finish_reason"),
            "raw": data,
        }


# ──────────────────────────────────────────────────────────────
# Anthropic client (real implementation)
# ──────────────────────────────────────────────────────────────


class AnthropicClient(LLMClient):
    ANTHROPIC_VERSION = "2023-06-01"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()
        system = _system_prompt(bool(media_urls))
        content = prompt
        if media_urls:
            blocks: list[dict] = [{"type": "text", "text": prompt}]
            blocks += [{"type": "image", "source": {"type": "url", "url": u}} for u in media_urls]
            content = blocks

        payload = {
            "model": self.model_name,
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        timeout = min(self.timeout, 60.0)
        request_meta = self._analysis_request_meta(prompt)
        execution = kwargs.pop("personal_billing_execution", None)
        stage = kwargs.pop("personal_billing_stage", "text_analysis")
        receipt, reserved_dispatch_id = await _personal_admission(
            self, payload, purpose="analysis", stage=stage, execution=execution, has_media=bool(media_urls)
        )
        await _verify_personal_dispatch_day(receipt)
        if receipt is not None:
            request_meta["dispatch_id"] = reserved_dispatch_id
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}/messages", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
        except asyncio.CancelledError:
            await _observe_cancelled_personal(receipt)
            raise
        except httpx.TimeoutException:
            await _observe_personal_failure(receipt)
            logger.error(f"Anthropic timeout after {timeout}s")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": "timeout"}, "parsed": {"analysis": "Timeout"}}
        except Exception as e:
            await _observe_personal_failure(receipt)
            logger.error(f"Anthropic error: {e}")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": str(e)}, "parsed": {"analysis": f"Error: {e}"}}

        accounting_usage = self._accounting_usage(data, "input_tokens", "output_tokens")
        accounting_cost = self._accounting_cost(data)
        parse_error = False
        try:
            text = "\n".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
            parsed = _try_json(text)
        except Exception:
            parsed, parse_error = {}, True
        if pydantic_model is not None:
            try:
                parsed = validate_with_pydantic(parsed, pydantic_model, strict=True)
            except Exception as exc:
                logger.warning("Pydantic validation failed for Anthropic %s: %s", self.provider.name, exc)
                data["error"] = "invalid_structured_output"
                parsed = {}

        response = {
            "request": request_meta,
            "response": data,
            "parsed": parsed,
            "accounting_usage": accounting_usage,
            "accounting_cache_usage": self._accounting_cache_usage(data),
            **accounting_cost,
            "usage": self._analyze_usage_projection(
                accounting_usage["prompt_tokens"], accounting_usage["completion_tokens"], data
            ),
        }
        await _observe_personal_outcome(
            receipt,
            dispatch_state="response_received",
            outcome="failed_output" if data.get("error") or parse_error else "succeeded",
            accounting_envelope=response,
        )
        await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
        return response

    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        chat_msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": chat_msgs,
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "stream": False,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]

        timeout = min(self.timeout, 60.0)
        execution = kwargs.pop("personal_billing_execution", None)
        personal_purpose = kwargs.pop("personal_billing_purpose", "agent_chat")
        receipt, dispatch_id = await _personal_admission(
            self,
            payload,
            purpose=personal_purpose,
            stage="chat",
            execution=execution,
            uses_tools=bool(tools),
        )
        await _verify_personal_dispatch_day(receipt)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}/messages", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()

            accounting = {
                "accounting_usage": self._accounting_usage(data, "input_tokens", "output_tokens"),
                "accounting_cache_usage": self._accounting_cache_usage(data),
                **self._accounting_cost(data),
            }
            parse_error = False
            try:
                response = self._parse_chat(data)
            except Exception:
                response, parse_error = {
                    "content": "", "tool_calls": [], "usage": self._usage_block_for_response(0, 0, data)
                }, True
            response["_personal_dispatch_id"] = dispatch_id
            await _observe_personal_outcome(
                receipt,
                dispatch_state="response_received",
                outcome="failed_output" if parse_error else "succeeded",
                accounting_envelope=accounting,
            )
            await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
            return response
        except asyncio.CancelledError:
            await _observe_cancelled_personal(receipt)
            raise
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as error:
            await _observe_personal_failure(receipt)
            await _record_llm_usage(self.model, success=False)
            setattr(error, "personal_dispatch_id", dispatch_id)
            raise
        except Exception as error:
            from app.services.billing.errors import is_personal_billing_block

            if is_personal_billing_block(error):
                setattr(error, "personal_dispatch_id", dispatch_id)
                raise
            await _observe_personal_failure(receipt)
            setattr(error, "personal_dispatch_id", dispatch_id)
            raise

    def _parse_chat(self, data: dict) -> dict[str, Any]:
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                content_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append({"id": block["id"], "name": block["name"], "arguments": block.get("input", {})})

        usage = data.get("usage") or {}
        return {
            "content": "\n".join(content_parts) or None,
            "tool_calls": tool_calls,
            "usage": self._usage_block_for_response(
                usage.get("input_tokens", 0), usage.get("output_tokens", 0), data
            ),
            "finish_reason": data.get("stop_reason"),
            "raw": data,
        }


# ──────────────────────────────────────────────────────────────
# Custom client (generic, per-model endpoint override)
# ──────────────────────────────────────────────────────────────


class CustomClient(LLMClient):
    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        endpoint = self.model.custom_endpoint_path
        if not endpoint:
            raise ValueError(f"Model {self.model.name} has api_format=custom but no custom_endpoint_path set")
        payload: dict[str, Any] = {"model": self.model_name, "prompt": prompt}
        if media_urls:
            payload["media_urls"] = media_urls
        execution = kwargs.pop("personal_billing_execution", None)
        stage = kwargs.pop("personal_billing_stage", "text_analysis")
        payload.update(kwargs)

        timeout = min(self.timeout, 60.0)
        request_meta = {**self._analysis_request_meta(prompt), "endpoint": endpoint}
        receipt, reserved_dispatch_id = await _personal_admission(
            self, payload, purpose="analysis", stage=stage, execution=execution, has_media=bool(media_urls)
        )
        await _verify_personal_dispatch_day(receipt)
        if receipt is not None:
            request_meta["dispatch_id"] = reserved_dispatch_id

        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}{endpoint}", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
        except asyncio.CancelledError:
            await _observe_cancelled_personal(receipt)
            raise
        except httpx.TimeoutException:
            await _observe_personal_failure(receipt)
            logger.error(f"Custom endpoint timeout for {self.provider.name} after {timeout}s")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": "timeout"}, "parsed": {"analysis": "Timeout"}}
        except Exception as e:
            await _observe_personal_failure(receipt)
            logger.error(f"Custom endpoint error for {self.provider.name}: {e}")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": str(e)}, "parsed": {"analysis": f"Error: {e}"}}

        accounting_usage = self._accounting_usage(data)
        accounting_cost = self._accounting_cost(data)
        parse_error = False
        try:
            content = _extract_custom_content(data)
            parsed = _try_json(content) if isinstance(content, str) else content
        except Exception:
            parsed, parse_error = {}, True
        if pydantic_model is not None:
            try:
                parsed = validate_with_pydantic(parsed, pydantic_model, strict=True)
            except Exception as exc:
                logger.warning("Pydantic validation failed for custom %s: %s", self.provider.name, exc)
                data["error"] = "invalid_structured_output"
                parsed = {}

        response = {
            "request": request_meta,
            "response": data,
            "parsed": parsed,
            "accounting_usage": accounting_usage,
            "accounting_cache_usage": self._accounting_cache_usage(data),
            **accounting_cost,
            "usage": self._analyze_usage_projection(0, 0, data),
        }
        await _observe_personal_outcome(
            receipt,
            dispatch_state="response_received",
            outcome="failed_output" if data.get("error") or parse_error else "succeeded",
            accounting_envelope=response,
        )
        await _record_llm_usage(self.model, success=not bool(data.get("error")))
        return response

    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        endpoint = self.model.custom_endpoint_path
        if not endpoint:
            raise ValueError(f"Model {self.model.name} has api_format=custom but no custom_endpoint_path set")
        payload: dict[str, Any] = {"model": self.model_name, "messages": messages}
        if tools:
            payload["tools"] = tools
        execution = kwargs.pop("personal_billing_execution", None)
        personal_purpose = kwargs.pop("personal_billing_purpose", "agent_chat")
        payload.update(kwargs)

        timeout = min(self.timeout, 60.0)
        request_meta = {
            "model": self.model_name,
            "llm_model_id": getattr(self.model, "id", None),
            "provider": self.provider.name.lower(),
            "endpoint": endpoint,
        }
        receipt, dispatch_id = await _personal_admission(
            self,
            payload,
            purpose=personal_purpose,
            stage="chat",
            execution=execution,
            uses_tools=bool(tools),
        )
        await _verify_personal_dispatch_day(receipt)

        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}{endpoint}", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()

            accounting = {
                "accounting_usage": self._accounting_usage(data),
                "accounting_cache_usage": self._accounting_cache_usage(data),
                **self._accounting_cost(data),
            }
            parse_error = False
            try:
                content = _extract_custom_content(data)
                response = {
                    "content": content,
                    "tool_calls": [],
                    "usage": self._usage_block_for_response(0, 0, data),
                    "finish_reason": None,
                    "raw": data,
                }
            except Exception:
                response, parse_error = {
                    "content": "", "tool_calls": [], "usage": self._usage_block_for_response(0, 0, data)
                }, True
            response["_personal_dispatch_id"] = dispatch_id
            await _observe_personal_outcome(
                receipt,
                dispatch_state="response_received",
                outcome="failed_output" if data.get("error") or parse_error else "succeeded",
                accounting_envelope=accounting,
            )
            await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
            return response
        except asyncio.CancelledError:
            await _observe_cancelled_personal(receipt)
            raise
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as error:
            await _observe_personal_failure(receipt)
            await _record_llm_usage(self.model, success=False)
            setattr(error, "personal_dispatch_id", dispatch_id)
            raise
        except Exception as error:
            from app.services.billing.errors import is_personal_billing_block

            if is_personal_billing_block(error):
                setattr(error, "personal_dispatch_id", dispatch_id)
                raise
            await _observe_personal_failure(receipt)
            setattr(error, "personal_dispatch_id", dispatch_id)
            raise

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        header_name = self.provider.auth_header or "Authorization: Bearer {key}"
        if "{key}" in header_name:
            h["Authorization"] = f"Bearer {self.api_key}"
        else:
            k, _, v = header_name.partition(": ")
            h[k] = v.format(key=self.api_key) if "{key}" in v else v
        return h


def _extract_custom_content(data: dict) -> Any:
    for key in ("answers", "content", "text", "result", "output", "response"):
        val = data.get(key)
        if val is not None:
            return val
    if isinstance(data, dict) and len(data) == 1:
        return next(iter(data.values()))
    return data


# ──────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────


class LLMClientFactory:
    _clients: dict[str, type[LLMClient]] = {
        "openai": OpenAICompatibleClient,
        "anthropic": AnthropicClient,
        "custom": CustomClient,
    }

    @classmethod
    def create(cls, model: LLMModel) -> LLMClient:
        if model is None:
            raise ValueError("LLMModel is required")
        fmt = model.provider.api_format.lower()
        client_cls = cls._clients.get(fmt)
        if client_cls is None:
            logger.warning(f"Unknown api_format '{fmt}' for provider '{model.provider.name}', using OpenAI-compatible")
            client_cls = OpenAICompatibleClient
        return client_cls(model)


# ──────────────────────────────────────────────────────────────
# Fallback chain
# ──────────────────────────────────────────────────────────────


async def _llm_models(query) -> list[LLMModel]:
    """Load models with their provider attached.

    `prefetch_related` is not optional: the factory and the fallback ordering
    both read `model.provider` after the session is gone.
    """
    return list(await query.prefetch_related("provider"))


async def journal_chat_attempt(
    client: Any, purpose: str, response: Optional[dict[str, Any]], dispatch_id: str | None = None
) -> None:
    """Journal one chat HTTP try in the ambient tenant scope; never breaks the call.

    Every provider try is its own attempt with a fresh local identity,
    including failed tries of the fallback chain. Returned chat values and the
    message/digest/job cost projections are unchanged. Pre-dispatch refusals
    (no API key, custom model without endpoint) are not paid attempts.
    """
    if not getattr(client, "api_key", None):
        return
    if isinstance(client, CustomClient) and not getattr(client.model, "custom_endpoint_path", None):
        return
    try:
        from app.services.ai.attempt_journal import STAGE_CHAT, chat_attempt_envelope, journal_scoped_attempts

        fields = ("input_tokens", "output_tokens") if isinstance(client, AnthropicClient) else None
        envelope = chat_attempt_envelope(
            model_id=client.model_name,
            provider_name=client.provider.name.lower(),
            dispatch_id=dispatch_id or uuid4().hex,
            raw=response.get("raw") if isinstance(response, dict) else None,
            failed=response is None,
            **({"usage_fields": fields} if fields else {}),
            api_format=getattr(client.provider, "api_format", None),
            model_row_id=getattr(client.model, "id", None),
        )
        await journal_scoped_attempts({STAGE_CHAT: envelope}, purpose=purpose)
    except Exception:
        logger.warning("attempt_journal_write_failed purpose=%s", purpose)


async def chat_with_fallback(
    messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None,
    preferred_model: Optional[LLMModel] = None, *, budget: Optional[float] = None,
    failures: Optional[list[dict[str, Any]]] = None, **kwargs
) -> Optional[dict[str, Any]]:
    """One chat turn across the eligible fleet: the preferred model first, then the rest.

    Ordering follows the product rule: the workspace's ★ default (or the model
    it names for this surface) starts, then **every** other eligible active
    model — most proven first (`use_count - fail_count`, then most recently
    used), never "cheapest first", which is what sent the chat into free rows
    whose own counters already read 100% failures.

    A try is a failure when the transport fails, when the provider answers with
    a retryable status, **or** when it answers 200 with nothing usable (no text,
    no tool call — e.g. a truncated `max_tokens` envelope). Every switch is
    appended to `failures` as `{model, provider, reason}` for the caller's
    hint; the raw provider body never is.

    `budget` is the USD this workspace may still spend today; once it is gone
    the walk stops instead of switching the owner to a paid provider. The
    caller gets None, which means "try again tomorrow", not "the model broke".
    """
    # Accounting label only, never sent to the provider.
    accounting_purpose = kwargs.pop("accounting_purpose", "chat")
    kwargs.setdefault("personal_billing_purpose", accounting_purpose)
    models = await _llm_models(LLMModel.objects.filter(is_active=True).order_by(LLMModel.id))

    allowed_types = await _allowed_model_types()
    # The same eligibility gate as automatic resolution: capability lists,
    # the full tier subset and an active provider all precede preference.
    text_models = [m for m in models if _eligible_text_model(m, allowed_types)]
    if not text_models and allowed_types is not None:
        logger.warning("Workspace tier allows only %s models; none is active", sorted(allowed_types))
    text_models.sort(key=lambda m: (
        m.id != getattr(preferred_model, "id", None),
        -_model_proven_score(m),
        -_model_last_seen(m),
        default_model_sort_key(m),
    ))

    last_err: Optional[Exception] = None
    blocked_paid = False
    for model in text_models:
        # A paid try must fit in what is left; free rows never touch the cap.
        price = _model_price_floor(model)
        if budget is not None and price > 0 and budget <= price:
            blocked_paid = True
            if failures is not None:
                failures.append({"model": model.model_id, "provider": model.provider.name, "reason": "budget"})
            continue
        try:
            client = LLMClientFactory.create(model)
            # Usage is recorded inside client.chat (single recording point).
            # Each try, failed or not, is journaled as its own attempt.
            try:
                response = await client.chat(messages, tools=tools, **kwargs)
            except Exception as error:
                from app.services.billing.errors import raise_personal_billing_block

                raise_personal_billing_block(error)
                # Personal reservation attempts already own a durable dispatch identity.
                await journal_chat_attempt(
                    client,
                    accounting_purpose,
                    None,
                    dispatch_id=getattr(error, "personal_dispatch_id", None),
                )
                raise
            if not _chat_response_usable(response):
                reason = _empty_reply_reason(response)
                logger.warning("%s/%s returned an empty reply (%s), trying next", model.provider.name, model.model_id, reason)
                _record_failure(failures, model, reason, limit=getattr(model, "max_tokens", None))
                last_err = RuntimeError(f"empty_reply:{reason}")
                continue
            dispatch_id = response.pop("_personal_dispatch_id", None)
            await journal_chat_attempt(client, accounting_purpose, response, dispatch_id=dispatch_id)
            if failures is not None:
                response["_model_failures"] = failures
            return response
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 429:
                last_err = e
                _record_failure(failures, model, "rate_limited")
                logger.warning(f"{model.provider.name}/{model.model_id} rate-limited (429), trying next")
            elif code < 500:
                # 4xx errors: if tools were provided and error suggests tools/function
                # calling is not supported, try next model instead of raising immediately.
                if tools and _is_tools_not_supported_error(e):
                    last_err = e
                    _record_failure(failures, model, "tools_unsupported")
                    logger.warning(
                        f"{model.provider.name}/{model.model_id} appears not to support tools ({code}), trying next"
                    )
                else:
                    raise
            else:
                last_err = e
                _record_failure(failures, model, "provider_error")
                logger.warning(f"{model.provider.name}/{model.model_id} failed ({code}), trying next")
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = e
            _record_failure(failures, model, "timeout")
            logger.warning(f"{model.provider.name}/{model.model_id} timeout/transport, trying next")
    if blocked_paid and budget is not None:
        # Nothing free answered and the cap forbids the paid rows: this is the
        # budget, not a broken fleet. None tells the caller to stop the turn.
        return None
    raise last_err or RuntimeError("No LLM models available")


def _model_price_floor(model: LLMModel) -> float:
    """Return a comparable USD/1M tariff signal, including configured cache rates."""
    from app.services.ai.cache_pricing import canonical_decimal, model_pricing_token_unit, CACHE_RATE_FIELDS

    unit = model_pricing_token_unit(model)
    ordinary = [
        canonical_decimal(getattr(model, field, None))
        for field in ("input_cost_per_1k", "output_cost_per_1k")
    ]
    prices = [float(rate * Decimal(1_000_000) / unit) for rate in ordinary if rate is not None]
    prices.extend(
        float(rate)
        for field in CACHE_RATE_FIELDS
        if (rate := canonical_decimal(getattr(model, field, None))) is not None
    )
    return max(prices) if prices else 0.0


def _model_proven_score(model: LLMModel) -> int:
    """`use_count - fail_count`: how often this model actually answered.

    A row that only knows failures (free tiers that are always rate-limited)
    sorts behind one that has served turns, whatever either costs.
    """
    return int(getattr(model, "use_count", 0) or 0) - int(getattr(model, "fail_count", 0) or 0)


def _model_last_seen(model: LLMModel) -> float:
    """Most recent success (else last use) as a POSIX timestamp; 0 when unknown."""
    seen = getattr(model, "last_success_at", None) or getattr(model, "last_used_at", None)
    try:
        return float(seen.timestamp()) if seen is not None else 0.0
    except (AttributeError, TypeError, ValueError):
        return 0.0


#: Reasons a chat try counts as failed, as short human-readable labels.
FAILURE_LABELS = {
    "rate_limited": "лимит запросов",
    "provider_error": "ошибка провайдера",
    "timeout": "таймаут",
    "tools_unsupported": "не поддерживает инструменты",
    "empty_reply": "пустой ответ",
    "truncated": "ответ обрезан",
    "budget": "не оплачено — дневной лимит",
}


def _record_failure(
    failures: Optional[list[dict[str, Any]]], model: LLMModel, reason: str, *, limit: Optional[int] = None
) -> None:
    if failures is None:
        return
    entry: dict[str, Any] = {
        "model": model.model_id,
        "provider": getattr(getattr(model, "provider", None), "name", None),
        "reason": reason,
    }
    if limit is not None:
        # The row's own output ceiling, so the caller can name it instead of
        # guessing why a free model answered with nothing.
        entry["max_tokens"] = int(limit)
    failures.append(entry)


def _empty_reply_reason(response: dict[str, Any]) -> str:
    """`truncated` when the provider stopped on the output budget, else `empty_reply`."""
    if (response or {}).get("finish_reason") in ("length", "max_tokens"):
        return "truncated"
    return "empty_reply"


def _chat_response_usable(response: dict[str, Any]) -> bool:
    """A chat turn without text and without a tool call carries no work.

    Such a 200 is a silent failure: the loop would answer "Готово." and the
    user would see an empty turn. It counts as a failed try, so the fleet moves
    to the next model.
    """
    if not isinstance(response, dict):
        return False
    if response.get("tool_calls"):
        return True
    return bool(str(response.get("content") or "").strip())


def _is_tools_not_supported_error(e: httpx.HTTPStatusError) -> bool:
    """Check if a 4xx error indicates that tools/function calling is not supported."""
    try:
        error_data = e.response.json()
        error_msg = str(error_data).lower()
    except Exception:
        error_msg = e.response.text.lower()

    # Keywords that suggest tools/function calling is not supported
    unsupported_keywords = [
        "tool",
        "function",
        "unsupported",
        "not supported",
        "not implemented",
        "invalid_request_error",
        "upstream",
        "rejected",
        "capability",
    ]
    return any(kw in error_msg for kw in unsupported_keywords)


async def resolve_model() -> Optional[LLMModel]:
    if settings.AGENT_MODEL:
        m = await _llm_models(LLMModel.objects.filter(name=settings.AGENT_MODEL))
        m = m[0] if m else None
        # The env name overrides the automatic choice, never ownership: it must
        # still be a text-capable row in the acting catalog on an active
        # provider. A foreign or unknown name falls back inside that catalog
        # instead of reaching another user's key.
        if m is not None and _eligible_text_model(m, None):
            return m
        logger.warning(
            f"AGENT_MODEL '{settings.AGENT_MODEL}' is not available in the acting personal catalog, falling back"
        )

    models = await _llm_models(LLMModel.objects.filter(is_active=True).order_by(LLMModel.id))
    # An eligible explicit AGENT_MODEL is honoured even on a tier that does not
    # include its type: it is an operator's deliberate override of the runtime,
    # and a blank env var must not turn into "no model at all". The automatic
    # choice below is filtered like every other routing decision.
    allowed_types = await _allowed_model_types()
    models.sort(key=default_model_sort_key)
    for m in models:
        if _eligible_text_model(m, allowed_types):
            return m
    if allowed_types is None:
        logger.error("No active text LLM model available")
    else:
        logger.error("No active LLM model of type %s available for this workspace's tier", sorted(allowed_types))
    return None


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _system_prompt(has_media: bool) -> str:
    if has_media:
        return "Ты - аналитик социальных сетей. Анализируй изображения и текст. Дай краткую аннотацию."
    return "Ты - аналитик социальных сетей. Анализируй посты. Отвечай в формате JSON."


def _try_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"analysis": text}
    return parsed if isinstance(parsed, dict) else {"analysis": text}


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace("USD", "").strip().lower()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def extract_cost_info(raw_response: Any) -> dict[str, Any]:
    """Extract token usage and reported dollar costs from a raw LLM API response.

    Understands OpenAI/Anthropic usage plus common provider cost extensions
    (OpenRouter `cost`, LiteLLM proxy `prompt_cost`/`completion_cost`/`total_cost`).

    Returns:
        tokens:      {"input": int, "output": int, "total": int}
        cost:        {"input": float|None, "output": float|None, "total": float|None}
        price_per_1k: {"input": float|None, "output": float|None}  (derived from cost/tokens)
        has_cost_report: bool
    """
    empty = {
        "tokens": {"input": 0, "output": 0, "total": 0},
        "cost": {"input": None, "output": None, "total": None},
        "price_per_1k": {"input": None, "output": None},
        "has_cost_report": False,
    }
    if not isinstance(raw_response, dict):
        return empty

    usage = raw_response.get("usage")
    if not isinstance(usage, dict) or not usage:
        # Some providers report cost at the top level of the response
        if _num(raw_response.get("total_cost")) is not None or _num(raw_response.get("cost")) is not None:
            return {
                "tokens": {"input": 0, "output": 0, "total": 0},
                "cost": {
                    "input": None,
                    "output": None,
                    "total": _num(raw_response.get("total_cost") or raw_response.get("cost")),
                },
                "price_per_1k": {"input": None, "output": None},
                "has_cost_report": True,
            }
        return empty

    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

    input_cost = _num(usage.get("prompt_cost") or usage.get("input_cost"))
    output_cost = _num(usage.get("completion_cost") or usage.get("output_cost"))
    total_cost = _num(usage.get("total_cost") or usage.get("cost"))
    if total_cost is None:
        total_cost = _num(raw_response.get("total_cost") or raw_response.get("cost"))

    has_report = any(v is not None for v in (input_cost, output_cost, total_cost))

    def per_1k(cost: Optional[float], tokens: int) -> Optional[float]:
        if cost is None or tokens <= 0:
            return None
        return round(cost / tokens * 1000, 10)

    return {
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
        "cost": {"input": input_cost, "output": output_cost, "total": total_cost},
        "price_per_1k": {
            "input": per_1k(input_cost, input_tokens),
            "output": per_1k(output_cost, output_tokens),
        },
        "has_cost_report": has_report,
    }
