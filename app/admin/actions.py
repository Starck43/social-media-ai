"""Operator LLM testing: GET renders only; explicit CSRF-protected POST executes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

from app.models import LLMModel
from app.services.ai.model_probe import (
    PROBE_PROMPT,
    REQUEST_MAX_TOKENS,
    probe_connectivity,
    probe_mock,
    probe_real_capped,
    probe_status,
    request_parameters,
)


class LLMModelActions:
    """Keep template state consistent across initial, short, mock and real results."""

    @staticmethod
    async def _model(pk: str):
        try:
            model_id = int(pk)
        except (ValueError, TypeError):
            raise HTTPException(422, "Select exactly one model") from None
        # LLMModel.id is PostgreSQL INTEGER; reject overflow before binding SQL.
        if not 1 <= model_id <= 2_147_483_647:
            raise HTTPException(422, "Select exactly one model")
        model = await LLMModel.objects.select_related("provider").get(id=model_id)
        if model is None or model.provider is None:
            raise HTTPException(404, "Model or provider not found")
        return model

    @classmethod
    async def test_model(cls, admin_view, request: Request, pks: str, admin_identity: str | None):
        """A bookmarked/legacy action URL must never send a paid request."""
        model = await cls._model(pks)
        return await cls._render(admin_view, request, model)

    @classmethod
    async def submit_test(cls, admin_view, request: Request, model_id: str, test_type: str, prompt: str):
        """Caller must enforce fresh platform CONFIGURE and CSRF before this method."""
        if test_type not in {"short", "mock", "real"}:
            raise HTTPException(422, "Unknown test mode")
        if len(prompt) > 8000:
            raise HTTPException(422, "Prompt exceeds 8000 characters")
        model = await cls._model(model_id)
        started = monotonic()
        if test_type == "short":
            result = await probe_connectivity(model)
        elif test_type == "mock":
            if not prompt.strip():
                return await cls._render(
                    admin_view,
                    request,
                    model,
                    prompt=prompt,
                    result={
                        "ok": False,
                        "tier": "mock",
                        "mode": "mock",
                        "tokens_spent": 0,
                        "elapsed_ms": 0,
                        "text": "Введите вопрос для mock-теста.",
                    },
                )
            try:
                mock = probe_mock(model, prompt)
                result = {
                    "ok": True,
                    "tier": "mock",
                    "text": "Mock: локальный ответ, без вызова провайдера.",
                    "content": mock["content"],
                    "usage": mock.get("usage", {}),
                    "tokens_spent": 0,
                }
            except Exception:
                result = {
                    "ok": False,
                    "tier": "mock",
                    "text": "Не удалось собрать локальный mock. Проверьте настройки.",
                }
        else:
            question, limit = request_parameters(model, prompt)
            probe = await probe_real_capped(model, prompt=prompt)
            result = {
                **probe,
                "ok": bool(probe.get("ok")),
                "tier": "real",
                "text": (
                    "Получен ответ на ваш запрос."
                    if probe.get("ok")
                    else probe.get("error", "Реальный ответ не подтверждён. Автоматического повтора нет.")
                ),
                "request_prompt": question,
                "max_tokens": probe.get("effective_max_tokens") or limit,
            }
            estimate = _probe_estimate(model, result.get("usage", {}), result.get("response_json"))
            result["estimated_cost"] = estimate
            if estimate is not None:
                try:
                    await LLMModel.objects.update_by_id(
                        model.id, last_request_cost=float(estimate), last_request_cost_at=datetime.now()
                    )
                except Exception:
                    result["save_warning"] = (
                        result.get("save_warning", "")
                        + " Ответ получен, но оценка стоимости не сохранена. Не повторяйте вызов автоматически."
                    ).strip()
        result["elapsed_ms"] = round((monotonic() - started) * 1000)
        result["mode"] = test_type
        if test_type == "mock":
            result["request_prompt"] = prompt
        return await cls._render(admin_view, request, model, prompt=prompt, result=result)

    @classmethod
    async def reject_test(cls, admin_view, request, model_id, prompt, *, code, text, status_code):
        """Render an actionable preflight refusal without provider dispatch."""
        model = await cls._model(model_id)
        return await cls._render(
            admin_view,
            request,
            model,
            prompt=prompt[:8000],
            status_code=status_code,
            result={
                "ok": False,
                "tier": "real",
                "text": text,
                "error_code": code,
                "stage": "preflight",
                "dispatch_state": "not_dispatched",
                "accounting_status": "not_started",
                "tokens_spent": 0,
            },
        )

    @staticmethod
    async def _render(admin_view, request, model, *, prompt="", result=None, status_code=200):
        from app.core.config import settings
        from app.models import Tenant

        actor = getattr(getattr(request, "state", None), "admin_user", None)
        billing_ready = bool(settings.PERSONAL_BILLING_ENABLED and actor and actor.is_platform_admin)
        billing_tenants = (
            list(await Tenant.objects.filter(is_active=True).order_by(Tenant.name)) if billing_ready else []
        )
        if not actor or not actor.is_platform_admin:
            billing_block_reason = "Запуск заблокирован: нужен активный администратор платформы."
        elif billing_ready and not billing_tenants:
            billing_block_reason = "Запуск заблокирован: нет активного рабочего пространства для учёта запроса."
        else:
            billing_block_reason = ""
        verdict = probe_status(model)
        return await admin_view.templates.TemplateResponse(
            request,
            "llm_model/test_results.html",
            {
                "model": model,
                "provider": model.provider,
                "prompt": prompt,
                "result": result,
                "status_checks": verdict["checks"],
                "status_hint": verdict["hint"],
                "probe_prompt": PROBE_PROMPT,
                "request_max_tokens": REQUEST_MAX_TOKENS,
                "key_configured": bool(verdict["checks"][0][1]),
                "billing_ready": billing_ready,
                "billing_block_reason": billing_block_reason,
                "billing_tenants": billing_tenants,
                "billing_tenant_id": getattr(getattr(request, "state", None), "probe_tenant_id", None),
            },
            **({"status_code": status_code} if status_code != 200 else {}),
        )


def _probe_estimate(model, usage: dict[str, Any], response: Any = None) -> Decimal | None:
    """Display estimate only; unknown usage/rates are not zero or settled spend."""
    incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing)):
        return None
    try:
        from app.services.ai.attempt_journal import provider_cost_evidence
        from app.services.ai.cache_pricing import cache_price_usd, model_pricing_token_unit, normalize_cache_usage

        claimed, reported = provider_cost_evidence(response)
        if claimed:
            return reported
        rates = [Decimal(str(model.input_cost_per_1k)), Decimal(str(model.output_cost_per_1k))]
        if any(not value.is_finite() or value < 0 for value in rates):
            return None
        unit = model_pricing_token_unit(model)
        if isinstance(response, dict) and isinstance(response.get("usage"), dict):
            cache = normalize_cache_usage(response, getattr(model.provider, "api_format", None))
            cache_cost = cache_price_usd(
                cache,
                {
                    "input_cost_per_1k": rates[0],
                    "cache_read_cost_per_1m": getattr(model, "cache_read_cost_per_1m", None),
                    "cache_write_5m_cost_per_1m": getattr(model, "cache_write_5m_cost_per_1m", None),
                    "cache_write_1h_cost_per_1m": getattr(model, "cache_write_1h_cost_per_1m", None),
                },
                prompt_tokens=incoming,
                pricing_unit=unit,
            )
            if cache_cost is None:
                return None
            return cache_cost + Decimal(outgoing) * rates[1] / unit
        return (incoming * rates[0] + outgoing * rates[1]) / unit
    except (InvalidOperation, TypeError, ValueError):
        return None
