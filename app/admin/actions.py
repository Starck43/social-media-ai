import logging

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.models import LLMProvider
from app.services.ai.llm_metadata import LLMMetadataHelper

logger = logging.getLogger(__name__)


class LLMProviderActions:
	"""Encapsulates all action logic for LLM Provider admin."""
	
	@staticmethod
	async def test_connection(request: Request, pks: str, admin_identity: str) -> RedirectResponse:
		"""
		Test API connection for selected providers.
		
		Args:
			request: Starlette request
			pks: Comma-separated provider IDs
			admin_identity: Admin view identity for redirect
			
		Returns:
			Redirect response
		"""
		try:
			pk_list = [pk for pk in pks.split(",") if pk]
			if not pk_list:
				raise HTTPException(status_code=400, detail="No providers selected")
			
			results = []
			for pk in pk_list:
				provider = await LLMProvider.objects.get(id=int(pk))
				api_key = provider.get_api_key()
				
				if not api_key:
					results.append(f"❌ {provider.name}: API key not found in environment ({provider.api_key_env})")
				else:
					# TODO: Implement actual API test
					results.append(f"✅ {provider.name}: API key configured")
			
			# Store message in session for display
			request.session["admin_message"] = {
				"type": "success",
				"message": "Test results:\n" + "\n".join(results)
			}
			
		except Exception as e:
			logger.error(f"Test connection error: {e}")
			request.session["admin_message"] = {
				"type": "error",
				"message": f"Error: {str(e)}"
			}
		
		return RedirectResponse(
			url=request.url_for("admin:list", identity=admin_identity),
			status_code=303
		)
	
	@staticmethod
	async def toggle_active(request: Request, pks: str, admin_identity: str) -> RedirectResponse:
		"""
		Toggle active status for selected providers.
		
		Args:
			request: Starlette request
			pks: Comma-separated provider IDs
			admin_identity: Admin view identity
			
		Returns:
			Redirect response
		"""
		try:
			pk_list = [pk for pk in pks.split(",") if pk]
			if not pk_list:
				raise HTTPException(status_code=400, detail="No providers selected")
			
			count = 0
			for pk in pk_list:
				provider = await LLMProvider.objects.get(id=int(pk))
				new_status = not provider.is_active
				await LLMProvider.objects.update_by_id(int(pk), is_active=new_status)
				count += 1
				logger.info(f"Provider {pk} ({provider.name}) status changed to {new_status}")
			
			request.session["admin_message"] = {
				"type": "success",
				"message": f"Статус изменён для {count} провайдеров"
			}
			
		except Exception as e:
			logger.error(f"Toggle active error: {e}")
			request.session["admin_message"] = {
				"type": "error",
				"message": f"Error: {str(e)}"
			}
		
		return RedirectResponse(
			url=request.url_for("admin:list", identity=admin_identity),
			status_code=303
		)
	
	@staticmethod
	async def quick_create_provider(
		request: Request,
		provider_type: str,
		model_id: str,
		admin_identity: str
	) -> RedirectResponse:
		"""
		Quick create a provider from template.
		
		Args:
			request: Starlette request
			provider_type: Provider type (deepseek, openai, etc.)
			model_id: Model identifier
			admin_identity: Admin view identity.
			
		Returns:
			Redirect response
		"""
		try:
			config = LLMMetadataHelper.get_provider_config(provider_type)
			model_info = LLMMetadataHelper.get_model_info(provider_type, model_id)
			
			if not config or not model_info:
				raise ValueError(f"Invalid provider/model: {provider_type}/{model_id}")
			
			# Check if already exists
			existing = await LLMProvider.objects.filter(
				provider_type=provider_type,
				model_name=model_id
			)
			
			if existing:
				request.session["admin_message"] = {
					"type": "warning",
					"message": f"Провайдер {model_info.name} уже существует"
				}
				return RedirectResponse(
					url=request.url_for("admin:list", identity=admin_identity),
					status_code=303
				)
			
			# Create new provider
			provider = await LLMProvider.objects.create(
				name=f"{config['display_name']} {model_info.name}",
				description=model_info.description,
				provider_type=provider_type,
				api_url=config['api_url'],
				api_key_env=config['api_key_env'],
				model_name=model_id,
				capabilities=list(model_info.capabilities),
				config={
					"temperature": 0.2,
					"max_tokens": model_info.max_tokens,
				},
				is_active=False  # User needs to add API key first
			)
			
			request.session["admin_message"] = {
				"type": "success",
				"message": f"✅ Создан провайдер: {provider.name}. Добавьте API ключ в .env и активируйте."
			}
			
			# Redirect to edit page
			return RedirectResponse(
				url=request.url_for("admin:edit", identity=admin_identity, pk=provider.id),
				status_code=303
			)
			
		except Exception as e:
			logger.error(f"Quick create error: {e}")
			request.session["admin_message"] = {
				"type": "error",
				"message": f"Ошибка создания: {str(e)}"
			}
			return RedirectResponse(
				url=request.url_for("admin:list", identity=admin_identity),
				status_code=303
			)
