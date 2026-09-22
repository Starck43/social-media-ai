import json
import logging
from datetime import datetime

from fastapi import status
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.models import LLMModel
from app.services.ai.llm_client import LLMClientFactory

logger = logging.getLogger(__name__)


class LLMModelActions:
	"""Encapsulates all action logic for LLM Model admin."""

	@classmethod
	async def test_model(cls, admin_view, request: Request, pks: str, admin_identity: str | None):
		if not pks:
			return RedirectResponse(
				url=request.url_for("admin:list", identity=admin_identity),
				status_code=status.HTTP_303_SEE_OTHER
			)

		try:
			# ВСЕ параметры только из query string для GET запросов
			prompt = request.query_params.get("prompt", "")
			test_type = request.query_params.get("test_type", "mock")

			# Get the first model ID from the list
			model_id = pks.split(",")[0]

			# Get the model
			model = await LLMModel.objects.select_related("provider").get(id=int(model_id))

			# Если нет промпта - показываем форму для ввода
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
								"api_url": "Not configured yet",
								"api_key_configured": False,
								"config": {}
							},
							"response_received": {
								"status": "ready_for_testing",
								"content": "Введите запрос и нажмите кнопку для тестирования"
							}
						},
						"full_response_json": "{}",
						"request_time": datetime.now(),
						"error": None,
						"is_real_response": False
					}
				)

			if test_type == "real":
				return await cls.test_model_real(admin_view, request, pks, prompt, None)
			else:
				return await cls.test_model_mock(admin_view, request, pks, prompt, None)

		except Exception as e:
			logger.error(f"Error in test_model: {str(e)}", exc_info=True)
			request.session["admin_message"] = {
				"type": "error",
				"message": f"Error testing model: {str(e)}"
			}
			return RedirectResponse(
				url=request.url_for("admin:list", identity=admin_identity),
				status_code=status.HTTP_303_SEE_OTHER
			)

	@classmethod
	async def test_model_mock(
			cls,
			admin_view,
			request: Request,
			pks: str,
			prompt: str,
			image_file: str = ""
	):
		"""Perform mock test with realistic simulated response."""
		model_id = pks.split(",")[0] if pks else None
		model = await LLMModel.objects.select_related("provider").get(id=int(model_id))

		try:
			# Create client for configuration
			client = await LLMClientFactory.create(model)

			# Prepare request payload
			request_payload = client._prepare_request(prompt)
			request_payload_json = json.dumps(request_payload, indent=2, ensure_ascii=False)

			# Client info
			client_info = {
				"provider": client.provider.name,
				"api_url": client.api_url,
				"api_key_configured": bool(client.api_key),
				"config": client.config,
				"model_name": model.name,
			}

			# СОЗДАЕМ РЕАЛИСТИЧНЫЙ MOCK ОТВЕТ
			has_image = bool(image_file and image_file.startswith('data:image/'))

			# Адаптируем ответ в зависимости от промпта
			if "json" in prompt.lower() or "формат" in prompt.lower():
				mock_content = json.dumps({
					"answer": f"Этот ответ был сгенерирован для тестирования конфигурации.",
					"status": "success",
					"timestamp": datetime.now().isoformat()
				}, ensure_ascii=False, indent=2)
			else:
				mock_content = f"""Этот ответ был сгенерирован для тестирования конфигурации."""

			simulated_api_response = {
				"model": model.name,
				"choices": [
					{
						"index": 0,
						"message": {
							"role": "assistant",
							"content": mock_content
						},
						"finish_reason": "stop"
					}
				],
				"usage": {
					"prompt_tokens": max(10, len(prompt) // 4),
					"completion_tokens": len(mock_content) // 4,
					"total_tokens": max(10, len(prompt) // 4) + (len(mock_content) // 4)
				}
			}

			try:
				parsed_response = client._parse_response(simulated_api_response)
			except:
				parsed_response = {"mock_response": "parsed_successfully"}

			# Unified response structure
			raw_response = {
				"client_info": client_info,
				"request_payload": request_payload,
				"response_received": {
					"status": "mock_response",
					"content": mock_content,
					"timestamp": datetime.now().isoformat(),
					"full_response": simulated_api_response
				}
			}

			full_response_json = json.dumps({
				"request": {
					"provider": client.provider.name.lower(),
					"model": client.model_name,
					"media_count": 1 if has_image else 0,
					"prompt": prompt,
				},
				"response": parsed_response,
			}, indent=2, ensure_ascii=False)

			template_name = "llm_model/test_results.html"
			return await admin_view.templates.TemplateResponse(
				request,
				template_name,
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
					"is_real_response": False
				}
			)

		except Exception as e:
			logger.error(f"Error in mock test: {e}")

			error_template = "llm_model/test_error.html"
			return await admin_view.templates.TemplateResponse(
				request,
				error_template,
				{
					"request": request,
					"model": model,
					"provider": model.provider,
					"error_message": str(e),
					"prompt": prompt,
					"error_time": datetime.now()
				}
			)

	@classmethod
	async def test_model_real(
			cls,
			admin_view,
			request: Request,
			pks: str,
			prompt: str,
			image_file: str = None
	):
		"""Perform real API test."""
		model_id = pks.split(",")[0] if pks else None
		model = await LLMModel.objects.select_related("provider").get(id=int(model_id))

		try:
			# Create client and make real API call
			client = await LLMClientFactory.create(model)

			# Prepare media URLs if image data is provided
			media_urls = None
			if image_file and image_file.startswith('data:image/'):
				media_urls = [image_file]

			# Make real API call
			start_time = datetime.now()
			response_data = await client.analyze(prompt, media_urls=media_urls)
			end_time = datetime.now()
			request_duration = (end_time - start_time).total_seconds()

			# Prepare request payload for display
			request_payload = client._prepare_request(prompt, media_urls=media_urls)
			request_payload_json = json.dumps(request_payload, indent=2, ensure_ascii=False)

			# Client info
			client_info = {
				"provider": client.provider.name,
				"api_url": client.api_url,
				"api_key_configured": bool(client.api_key),
				"config": client.config,
				"model_name": model.name,
			}

			# ИСПРАВЛЕННО: Правильно извлекаем контент ответа
			response_content = ""
			full_api_response = {}

			# Пытаемся извлечь контент разными способами в зависимости от структуры ответа
			if isinstance(response_data, dict):
				if "parsed" in response_data and response_data["parsed"]:
					response_content = json.dumps(response_data["parsed"], indent=2, ensure_ascii=False)
				elif "response" in response_data:
					# Стандартная структура ответа от API
					api_response = response_data["response"]
					full_api_response = api_response

					# Извлекаем контент из choices[0].message.content
					if "choices" in api_response and len(api_response["choices"]) > 0:
						message = api_response["choices"][0].get("message", {})
						response_content = message.get("content", "No content in message")
					else:
						response_content = "No choices in response"
				else:
					# Если другая структура, показываем весь ответ
					response_content = json.dumps(response_data, indent=2, ensure_ascii=False)
			else:
				response_content = str(response_data)

			# Создаем ПРАВИЛЬНУЮ структуру для реального ответа
			raw_response = {
				"client_info": client_info,
				"request_payload": request_payload,
				"response_received": {
					"status": "success",
					"content": response_content,
					"timestamp": end_time.isoformat(),
					"duration_seconds": request_duration,
					"full_api_response": full_api_response  # Добавляем полный ответ API
				}
			}

			full_response_json = json.dumps(raw_response, indent=2, ensure_ascii=False)

			template_name = "llm_model/test_results.html"
			return await admin_view.templates.TemplateResponse(
				request,
				template_name,
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
					"is_real_response": True
				}
			)

		except Exception as e:
			logger.error(f"Error in real test: {str(e)}", exc_info=True)

			# Получаем полную информацию об ошибке
			error_details = str(e)

			# Если это HTTP ошибка, получаем больше деталей
			if hasattr(e, 'response'):
				try:
					error_response = e.response.json()
					error_details = f"HTTP {e.response.status_code}: {error_response}"
				except:
					error_details = f"HTTP {e.response.status_code}: {e.response.text}"

			error_template = "llm_model/test_error.html"
			return await admin_view.templates.TemplateResponse(
				request,
				error_template,
				{
					"request": request,
					"model": model,
					"provider": model.provider,
					"error_message": error_details,
					"prompt": prompt,
					"error_time": datetime.now()
				}
			)
