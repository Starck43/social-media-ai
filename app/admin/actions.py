import json
import logging
from datetime import datetime
from typing import Any

import httpx
from fastapi import status
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.models import LLMModel
from app.services.ai.llm_client import LLMClientFactory, extract_cost_info

logger = logging.getLogger(__name__)


class LLMModelActions:
    """Encapsulates all action logic for LLM Model admin."""

    @classmethod
    async def test_model(cls, admin_view, request: Request, pks: str, admin_identity: str | None):
        if not pks:
            return RedirectResponse(
                url=request.url_for("admin:list", identity=admin_identity), status_code=status.HTTP_303_SEE_OTHER
            )
        try:
            from app.services.ai.model_probe import probe_status

            prompt = request.query_params.get("prompt", "")
            test_type = request.query_params.get("test_type", "mock")
            model_id = pks.split(",")[0]
            model = await LLMModel.objects.select_related("provider").get(id=int(model_id))
            verdict = probe_status(model)

            if not prompt:
                template_name = "llm_model/test_results.html"
                return await admin_view.templates.TemplateResponse(
                    request,
                    template_name,
                    {
                        "request": request,
                        "model": model,
                        "provider": model.provider,
                        "prompt": "Привет! Расскажи о себе и своих возможностях.",
                        "request_payload": "",
                        "raw_response": {
                            "client_info": {
                                "provider": model.provider.name,
                                "model_name": model.name,
                                "model_id": model.model_id,
                                "base_url": model.provider.base_url,
                                "api_key_configured": bool(model.provider.get_api_key()),
                                "api_format": model.provider.api_format,
                            },
                            "response_received": {
                                "status": "ready_for_testing",
                                "content": "Введите запрос и нажмите кнопку для тестирования",
                            },
                        },
                        "full_response_json": "{}",
                        "request_time": datetime.now(),
                        "error": None,
                        "is_real_response": False,
                        "cost_info": {},
                        "price_updated": False,
                        "status_checks": verdict["checks"],
                        "status_hint": verdict["hint"],
                    },
                )

            if test_type == "real":
                return await cls.test_model_real(admin_view, request, pks, prompt, None)
            else:
                return await cls.test_model_mock(admin_view, request, pks, prompt, None)
        except Exception as e:
            logger.error(f"Error in test_model: {e}", exc_info=True)
            request.session["admin_message"] = {"type": "error", "message": f"Error testing model: {e}"}
            return RedirectResponse(
                url=request.url_for("admin:list", identity=admin_identity), status_code=status.HTTP_303_SEE_OTHER
            )

    @classmethod
    async def test_model_mock(cls, admin_view, request: Request, pks: str, prompt: str, image_file: str = ""):
        model_id = pks.split(",")[0] if pks else None
        model = await LLMModel.objects.select_related("provider").get(id=int(model_id))
        try:
            client = LLMClientFactory.create(model)
            media_urls = [image_file] if image_file and image_file.startswith("data:image/") else None
            request_payload, messages = cls._build_chat_payload(client, prompt, media_urls)
            request_payload_json = json.dumps(request_payload, indent=2, ensure_ascii=False)

            client_info = {
                "provider": client.provider.name,
                "base_url": client.base_url,
                "api_key_configured": bool(client.api_key),
                "api_format": client.provider.api_format,
                "model_name": model.name,
                "model_id": model.model_id,
                "max_tokens": model.max_tokens,
                "default_temperature": model.default_temperature,
            }

            has_image = bool(media_urls)
            if "json" in prompt.lower() or "формат" in prompt.lower():
                mock_content = json.dumps(
                    {"answer": "Этот ответ сгенерирован для тестирования.", "status": "success"},
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                mock_content = "Этот ответ сгенерирован для тестирования."

            simulated_api_response = {
                "model": model.model_id,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": mock_content}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": max(10, len(prompt) // 4),
                    "completion_tokens": len(mock_content) // 4,
                    "total_tokens": max(10, len(prompt) // 4) + (len(mock_content) // 4),
                },
            }

            raw_response = {
                "client_info": client_info,
                "request_payload": request_payload,
                "response_received": {
                    "status": "mock_response",
                    "content": mock_content,
                    "timestamp": datetime.now().isoformat(),
                    "full_response": simulated_api_response,
                },
            }
            full_response_json = json.dumps(
                {
                    "request": {
                        "provider": client.provider.name.lower(),
                        "model": client.model_name,
                        "media_count": 1 if has_image else 0,
                        "prompt": prompt,
                    },
                    "response": simulated_api_response,
                },
                indent=2,
                ensure_ascii=False,
            )

            return await admin_view.templates.TemplateResponse(
                request,
                "llm_model/test_results.html",
                {
                    "request": request,
                    "model": model,
                    "provider": model.provider,
                    "prompt": prompt,
                    "request_payload": request_payload_json,
                    "raw_response": raw_response,
                    "full_response_json": full_response_json,
                    "request_time": datetime.now(),
                    "error": None,
                    "is_real_response": False,
                    "cost_info": {},
                    "price_updated": False,
                },
            )
        except Exception as e:
            logger.error(f"Error in mock test: {e}")
            return await admin_view.templates.TemplateResponse(
                request,
                "llm_model/test_error.html",
                {
                    "request": request,
                    "model": model,
                    "provider": model.provider,
                    "error_message": str(e),
                    "prompt": prompt,
                    "error_time": datetime.now(),
                },
            )

    @classmethod
    async def test_model_real(cls, admin_view, request: Request, pks: str, prompt: str, image_file: str = None):
        """Real test — one minimal capped probe, not a full chat answer.

        Previously the user's full prompt went through ``client.chat`` with the
        model's full ``max_tokens``; now the reachability probe uses a fixed
        one-word prompt (``max_tokens=8``) so a check costs a fraction of a
        cent. The free-form prompt box stays for the mock path.
        """
        from app.services.ai.model_probe import PROBE_PROMPT, probe_real_capped, probe_status

        model_id = pks.split(",")[0] if pks else None
        model = await LLMModel.objects.select_related("provider").get(id=int(model_id))
        verdict = probe_status(model)
        if not verdict["ok"]:
            return await admin_view.templates.TemplateResponse(
                request,
                "llm_model/test_error.html",
                {
                    "request": request,
                    "model": model,
                    "provider": model.provider,
                    "error_message": verdict["hint"],
                    "prompt": prompt,
                    "error_time": datetime.now(),
                },
            )
        try:
            start_time = datetime.now()
            probe = await probe_real_capped(model, PROBE_PROMPT)
            end_time = datetime.now()
            if not probe.get("ok"):
                return await admin_view.templates.TemplateResponse(
                    request,
                    "llm_model/test_error.html",
                    {
                        "request": request,
                        "model": model,
                        "provider": model.provider,
                        "error_message": probe.get("error", "Проба не удалась"),
                        "prompt": prompt,
                        "error_time": end_time,
                    },
                )
            duration = (end_time - start_time).total_seconds()
            usage = probe.get("usage", {})
            tokens = {
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            }
            estimated = (
                tokens["input"] / 1000 * model.input_cost_per_1k
                + tokens["output"] / 1000 * model.output_cost_per_1k
            )
            cost_info = {"tokens": tokens, "estimated_cost": round(estimated, 8), "has_cost_report": False}
            if tokens["total"] > 0:
                await LLMModel.objects.update_by_id(
                    model.id,
                    last_request_cost=round(estimated, 8),
                    last_request_cost_at=end_time,
                )
                model.last_request_cost = round(estimated, 8)
                model.last_request_cost_at = end_time
            raw_response = {
                "client_info": {
                    "provider": model.provider.name,
                    "model_name": model.name,
                    "model_id": model.model_id,
                    "probe": f"Фикс-проба «{PROBE_PROMPT}» (max_tokens=8)",
                },
                "response_received": {
                    "status": "success",
                    "content": probe.get("content", ""),
                    "timestamp": end_time.isoformat(),
                    "duration_seconds": duration,
                },
            }
            full_response_json = json.dumps(raw_response, indent=2, ensure_ascii=False)

            return await admin_view.templates.TemplateResponse(
                request,
                "llm_model/test_results.html",
                {
                    "request": request,
                    "model": model,
                    "provider": model.provider,
                    "prompt": prompt,
                    "request_payload": json.dumps(
                        {"probe": PROBE_PROMPT, "max_tokens": 8}, indent=2, ensure_ascii=False
                    ),
                    "raw_response": raw_response,
                    "full_response_json": full_response_json,
                    "request_time": end_time,
                    "error": None,
                    "is_real_response": True,
                    "cost_info": cost_info,
                    "price_updated": False,
                    "status_checks": verdict["checks"],
                    "status_hint": verdict["hint"],
                },
            )
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as e:
            logger.error(f"LLM request error for model {model.name} ({model.model_id}): {e}", exc_info=True)
            error_details = _build_error_details(e, model)
            return await admin_view.templates.TemplateResponse(
                request,
                "llm_model/test_error.html",
                {
                    "request": request,
                    "model": model,
                    "provider": model.provider,
                    "error_details": error_details,
                    "prompt": prompt,
                    "error_time": datetime.now(),
                },
            )
        except Exception as e:
            logger.error(f"Unexpected error testing model {model.name} ({model.model_id}): {e}", exc_info=True)
            error_details = {
                "kind": "unexpected_error",
                "message": f"Внутренняя ошибка: {str(e)}",
                "retry_after": "",
                "status_code": None,
                "recommendations": [
                    "Проверьте настройки провайдера и попробуйте снова",
                    "Обратитесь к администратору при повторении ошибки",
                ],
            }
            return await admin_view.templates.TemplateResponse(
                request,
                "llm_model/test_error.html",
                {
                    "request": request,
                    "model": model,
                    "provider": model.provider,
                    "error_details": error_details,
                    "prompt": prompt,
                    "error_time": datetime.now(),
                },
            )

    @staticmethod
    def _build_chat_payload(client, prompt: str, media_urls: list[str] | None) -> tuple[dict, list[dict]]:
        """Build the unified chat request: user prompt as-is (no forced system/Analysis prompt).

        Matches how services/agent call the model — content is decided by the prompt itself.
        """
        if media_urls:
            content: list[dict] = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url", "image_url": {"url": u}} for u in media_urls]
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": prompt}]
        payload = {
            "model": client.model_name,
            "messages": messages,
            "max_tokens": client.max_tokens,
            "temperature": client.default_temperature,
            "stream": False,
        }
        return payload, messages


def _build_error_details(exc: Exception, model: LLMModel) -> dict[str, Any]:
    """Build structured error details for the test_error template."""
    status_code: int | None = None
    message = str(exc)
    code = ""
    retry_after = ""
    raw_body: str | None = None
    provider_host = model.provider.base_url.replace("https://", "").replace("http://", "").split("/")[0]

    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        status_code = exc.response.status_code
        raw_body = exc.response.text
        try:
            err_json = exc.response.json()
            if isinstance(err_json, dict):
                code = err_json.get("error", {}).get("code") or err_json.get("code") or err_json.get("error") or ""
                if isinstance(code, dict):
                    code = code.get("message") or code.get("type") or str(code)
                message = err_json.get("error", {}).get("message") or err_json.get("message") or message
        except Exception:
            pass
        retry_after = exc.response.headers.get("Retry-After", "")
        if not message or message == str(exc):
            body_preview = raw_body[:500] if raw_body else "No response body"
            message = f"HTTP {status_code}: {body_preview}"

    elif isinstance(exc, httpx.TimeoutException):
        message = f"Превышено время ожидания ответа от {model.provider.name} ({model.provider.base_url})"

    elif isinstance(exc, httpx.TransportError):
        message = f"Ошибка подключения к {model.provider.name} ({model.provider.base_url}): {exc}"

    recommendations = _error_recommendations(
        kind=type(exc).__name__.lower(),
        code=code.lower() if isinstance(code, str) else "",
        message=message.lower(),
        status_code=status_code or 0,
    )

    return {
        "kind": type(exc).__name__,
        "message": message,
        "code": code,
        "status_code": status_code,
        "retry_after": retry_after,
        "recommendations": recommendations,
        "raw_body": raw_body,
        "provider_host": provider_host,
    }


def _error_recommendations(kind: str, code: str, message: str, status_code: int) -> list[str]:
    """Generate human-readable recommendations based on LLM API error."""
    recs = []

    # Rate limiting
    if kind in ("rate_limit_error", "rate_limit") or code in (
        "model_concurrency",
        "rate_limit_exceeded",
        "quota_exceeded",
    ):
        recs.append("Лимит запросов исчерпан. Подождите и повторите попытку.")
        if status_code == 429:
            recs.append("Или увеличьте лимит параллельных запросов в настройках провайдера.")

    # Authentication
    elif kind == "authentication_error" or code in ("invalid_api_key", "authentication_failed", "unauthorized"):
        recs.append("Проверьте API ключ провайдера в настройках модели.")
        recs.append("Убедитесь, что ключ активен и не истёк.")

    # Model not found / invalid model
    elif kind in ("model_not_found", "invalid_request_error") or code in (
        "model_not_found",
        "invalid_model",
        "model_not_allowed",
    ):
        recs.append(f"Модель '{code}' не найдена или недоступна у провайдера.")
        recs.append("Проверьте название model_id в настройках модели.")
        recs.append("Возможно модель депрекейднута — обратитесь к документации провайдера.")

    # Upstream rejected (provider-side error)
    elif code == "upstream_rejected" or "upstream" in message.lower():
        recs.append("Провайдер отклонил запрос. Возможные причины:")
        recs.append("• Недостаточно средств на аккаунте провайдера")
        recs.append("• Модель временно недоступна")
        recs.append("• Превышен лимит параллельных запросов")
        recs.append("• Модель не поддерживает указанный max_tokens")

    # Insufficient funds
    elif "insufficient" in message.lower() or "balance" in message.lower():
        recs.append("Недостаточно средств на аккаунте провайдера.")
        recs.append("Пополните баланс для продолжения тестирования.")

    # Timeout
    elif "timeout" in kind.lower() or "timeout" in message.lower():
        recs.append("Превышено время ожидания ответа от провайдера.")
        recs.append("Проверьте стабильность网络连接 и попробуйте снова.")

    # Generic errors
    else:
        recs.append(f"Ошибка: {kind}")
        if status_code == 400:
            recs.append("Проверьте корректность параметров запроса (model_id, max_tokens, temperature).")
        elif status_code == 401:
            recs.append("Ошибка авторизации — проверьте API ключ.")
        elif status_code == 403:
            recs.append("Доступ запрещён — проверьте права провайдера.")
        elif status_code == 429:
            recs.append("Слишком много запросов — подождите и повторите.")
        elif status_code >= 500:
            recs.append("Ошибка на стороне провайдера — попробуйте позже.")

    return recs


def _try_parse_json(text: str) -> dict | None:
    """Parse a JSON dict from model text; None if not JSON."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        # Try to find first {...} block in the text (models sometimes wrap JSON in prose)
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                return None
        return None
