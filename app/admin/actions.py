import json
import logging
from datetime import datetime

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
            prompt = request.query_params.get("prompt", "")
            test_type = request.query_params.get("test_type", "mock")
            model_id = pks.split(",")[0]
            model = await LLMModel.objects.select_related("provider").get(id=int(model_id))

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

            start_time = datetime.now()
            # Единый путь к модели: промпт пользователя передаётся as-is (как в агенте/сервисах).
            # Если промпт просит JSON — модель вернёт JSON; если свободный диалог — обычный текст.
            response_data = await client.chat(messages)
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            full_api_response = response_data.get("raw") or {}
            raw_content = response_data.get("content") or ""
            parsed_fields = _try_parse_json(raw_content)

            response_content = raw_content or json.dumps(full_api_response, indent=2, ensure_ascii=False)

            cost_info = extract_cost_info(full_api_response)

            price_updated = False
            in_price = cost_info.get("price_per_1k", {}).get("input")
            out_price = cost_info.get("price_per_1k", {}).get("output")

            if in_price is not None and out_price is not None:
                await LLMModel.objects.update_by_id(
                    model.id,
                    input_cost_per_1k=in_price,
                    output_cost_per_1k=out_price,
                )
                model.input_cost_per_1k = in_price
                model.output_cost_per_1k = out_price
                price_updated = True
                logger.info(f"Model {model.name} prices updated from response: in=${in_price}, out=${out_price}")

            tokens = cost_info.get("tokens", {})
            if not cost_info.get("has_cost_report"):
                estimated = (
                    tokens.get("input", 0) / 1000 * model.input_cost_per_1k
                    + tokens.get("output", 0) / 1000 * model.output_cost_per_1k
                )
                cost_info["estimated_cost"] = round(estimated, 8)
                if tokens.get("total", 0) > 0:
                    await LLMModel.objects.update_by_id(
                        model.id,
                        last_request_cost=round(estimated, 8),
                        last_request_cost_at=end_time,
                    )
                    model.last_request_cost = round(estimated, 8)
                    model.last_request_cost_at = end_time
                    logger.info(f"Model {model.name} last_request_cost saved: ${estimated:.6f}")
            else:
                cost_info["estimated_cost"] = None

            raw_response = {
                "client_info": client_info,
                "request_payload": request_payload,
                "response_received": {
                    "status": "success",
                    "content": response_content,
                    "raw_content": raw_content,
                    "parsed_fields": parsed_fields,
                    "timestamp": end_time.isoformat(),
                    "duration_seconds": duration,
                    "full_api_response": full_api_response,
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
                    "request_payload": request_payload_json,
                    "raw_response": raw_response,
                    "full_response_json": full_response_json,
                    "request_time": end_time,
                    "error": None,
                    "is_real_response": True,
                    "cost_info": cost_info,
                    "price_updated": price_updated,
                },
            )
        except Exception as e:
            logger.error(f"Error in real test: {e}", exc_info=True)
            error_details = {"kind": "unknown", "message": str(e)}
            if hasattr(e, "response") and e.response is not None:
                try:
                    err_data = e.response.json()
                    err_obj = err_data.get("error", {}) if isinstance(err_data, dict) else {}
                    error_details = {
                        "kind": err_obj.get("type", "api_error"),
                        "code": err_obj.get("code", ""),
                        "message": err_obj.get("message", e.response.text or str(e)),
                        "retry_after": e.response.headers.get("retry-after", ""),
                        "status_code": e.response.status_code,
                    }
                except Exception:
                    error_details = {
                        "kind": "http_error",
                        "message": f"HTTP {getattr(e.response, 'status_code', '?')}: {getattr(e.response, 'text', str(e))}",
                        "status_code": getattr(e.response, "status_code", None),
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
