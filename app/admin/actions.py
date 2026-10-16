"""Operator LLM testing: GET renders only; explicit CSRF-protected POST executes."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

from app.models import LLMModel
from app.services.ai.model_probe import PROBE_PROMPT, probe_mock, probe_real_capped, probe_short, probe_status


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
        if test_type == "short":
            result = probe_short(model)
        elif test_type == "mock":
            if not prompt.strip():
                return await cls._render(
                    admin_view,
                    request,
                    model,
                    prompt=prompt,
                    result={"ok": False, "tier": "mock", "text": "Введите вопрос для mock-теста."},
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
            verdict = probe_status(model)
            if not verdict["ok"]:
                result = {"ok": False, "tier": "real", "text": verdict["hint"]}
            elif model.provider.api_format != "openai":
                # The existing capped helper is OpenAI-shaped, not a format-agnostic chat client.
                result = {
                    "ok": False,
                    "tier": "real",
                    "text": "Реальная проба доступна только для OpenAI-совместимого API.",
                }
            else:
                probe = await probe_real_capped(model)
                result = {
                    "ok": bool(probe.get("ok")),
                    "tier": "real",
                    "text": (
                        "Провайдер ответил на фиксированную пробу."
                        if probe.get("ok")
                        else "Реальный ответ не подтверждён. Проверьте подключение, ключ и лимиты."
                    ),
                    "content": probe.get("content", "") if probe.get("ok") else "",
                    "usage": probe.get("usage", {}),
                    "tokens_spent": probe.get("tokens_spent"),
                }
                if probe.get("save_warning"):
                    result["save_warning"] = probe["save_warning"]
                estimate = _probe_estimate(model, result["usage"]) if result["ok"] else None
                result["estimated_cost"] = estimate
                if estimate is not None:
                    try:
                        await LLMModel.objects.update_by_id(
                            model.id, last_request_cost=float(estimate), last_request_cost_at=datetime.now()
                        )
                    except Exception:
                        result["save_warning"] = (
                            "Ответ получен, но оценка стоимости не сохранена. Не повторяйте вызов автоматически."
                        )
        result["mode"] = test_type
        return await cls._render(admin_view, request, model, prompt=prompt, result=result)

    @staticmethod
    async def _render(admin_view, request, model, *, prompt="", result=None):
        from app.core.config import settings
        from app.models import Tenant

        actor = getattr(getattr(request, "state", None), "admin_user", None)
        billing_ready = bool(settings.PERSONAL_BILLING_ENABLED and actor and actor.is_platform_admin)
        billing_tenants = (
            list(await Tenant.objects.filter(is_active=True).order_by(Tenant.name)) if billing_ready else []
        )
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
                "key_configured": bool(verdict["checks"][0][1]),
                "billing_ready": billing_ready,
                "billing_tenants": billing_tenants,
                "billing_tenant_id": getattr(getattr(request, "state", None), "probe_tenant_id", None),
            },
        )


def _probe_estimate(model, usage: dict[str, Any]) -> Decimal | None:
    """Display estimate only; unknown usage/rates are not zero or settled spend."""
    incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing)):
        return None
    try:
        rates = [Decimal(str(model.input_cost_per_1k)), Decimal(str(model.output_cost_per_1k))]
        if any(not value.is_finite() or value < 0 for value in rates):
            return None
        return (incoming * rates[0] + outgoing * rates[1]) / 1000
    except (InvalidOperation, TypeError, ValueError):
        return None
