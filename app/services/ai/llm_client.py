import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, Any, Coroutine

import httpx

from app.core.config import settings
from app.models import LLMModel

logger = logging.getLogger(__name__)

# Global rate limiter to avoid 429 errors
# Track last request time per provider
_last_request_time: dict[str, float] = {}


class LLMClient(ABC):
	"""
	Abstract base class for LLM clients.
	
	Provides a unified interface for different LLM providers (DeepSeek, OpenAI, etc.)
	Each implementation handles provider-specific API calls and response formatting.
	"""

	@classmethod
	def create(cls, model: LLMModel) -> Coroutine[Any, Any, 'LLMClient']:
		"""Factory method — delegates to LLMClientFactory."""
		return LLMClientFactory.create(model)

	def __init__(self, model: LLMModel):
		"""
		Initialize LLM client with provider configuration.
		
		Args:
			model: Model instance
		"""
		self.provider = model.provider
		self.api_key = model.provider.get_api_key()
		self.api_url = model.provider.api_url
		self.config = self._merge_configs(model.provider.config, model.config if model else {})
		self.model_name = model.name

	@staticmethod
	def _merge_configs(provider_config: dict, model_config: dict) -> dict:
		"""
		Merge configurations with proper priority handling.

		Priority order (highest to lowest):
		1. Model-specific config (highest priority)
		2. Provider config
		3. Default settings from app.core.config (lowest priority)

		Args:
			provider_config: Configuration from LLMProvider
			model_config: Configuration from LLMModel

		Returns:
			Merged configuration dictionary
		"""
		# Start with default settings from app.core.config
		merged = {
			"temperature": getattr(settings, 'LLM_DEFAULT_TEMPERATURE', 0.3),
			"max_tokens": getattr(settings, 'LLM_DEFAULT_MAX_TOKENS', 400),
			"stream": getattr(settings, 'LLM_DEFAULT_STREAM', False),
			"timeout": getattr(settings, 'LLM_DEFAULT_TIMEOUT', 90.0),
			"rate_limit_delay": getattr(settings, 'LLM_REQUEST_DELAY', 2000),  # Safe fallback to 2000ms
		}

		# Apply provider configuration (medium priority)
		if provider_config:
			if isinstance(provider_config, str):
				try:
					provider_config = json.loads(provider_config)
				except json.JSONDecodeError:
					logger.warning(f"Failed to parse provider config as JSON: {provider_config}")
					provider_config = {}
			merged.update(provider_config)

		# Apply model configuration (highest priority)
		if model_config:
			if isinstance(model_config, str):
				try:
					model_config = json.loads(model_config)
				except json.JSONDecodeError:
					logger.warning(f"Failed to parse model config as JSON: {model_config}")
					model_config = {}
			merged.update(model_config)

		# Clean string values and remove None values
		cleaned_config = {}
		for k, v in merged.items():
			if v is None:
				continue
			if isinstance(v, str):
				cleaned_config[k.strip()] = v.strip()
			else:
				cleaned_config[k] = v

		return cleaned_config

	@staticmethod
	def _get_system_prompt(media_urls: Optional[list[str]] = None) -> str:
		"""Return system prompt based on content type."""

		if media_urls:
			return (
				"Ты - аналитик социальных сетей. Анализируй изображения и текст. "
				"Давай краткую аннотацию: что изображено, основная тема, эмоциональная окраска. "
				"Будь точным и объективным."
			)
		else:
			return (
				"Ты - аналитик социальных сетей. Анализируй посты и давай краткую аннотацию: "
				"основная тема, эмоциональная окраска, ключевые моменты. "
				"Будь точным и объективным. Отвечай в формате JSON."
			)

	async def _apply_rate_limit(self):
		"""
		Apply rate limiting to avoid API throttling and 429 errors.

		Uses rate_limit_delay from merged config with safe fallback to 2000ms.
		Tracks request timing per provider to maintain proper intervals.
		"""
		global _last_request_time

		provider_key = self.provider.name.lower()
		current_time = asyncio.get_event_loop().time()

		# Get rate limit delay from merged config with safe fallback
		rate_limit_delay = float(self.config.get("rate_limit_delay", 2000)) / 1000.0

		# Apply rate limiting if this provider has made recent requests
		if provider_key in _last_request_time:
			elapsed = current_time - _last_request_time[provider_key]
			if elapsed < rate_limit_delay:
				delay = rate_limit_delay - elapsed
				logger.debug(
					f"Rate limiting {provider_key}: waiting {delay:.2f}s (configured: {rate_limit_delay}s)"
				)
				await asyncio.sleep(delay)

		# Update last request time for this provider
		_last_request_time[provider_key] = asyncio.get_event_loop().time()

	@abstractmethod
	async def analyze(
			self,
			prompt: str,
			media_urls: Optional[list[str]] = None,
			**kwargs
	) -> dict[str, Any]:
		"""
		Analyze content using the LLM.
		
		Args:
			prompt: Text prompt for analysis
			media_urls: Optional list of media URLs (images, videos) to analyze
			**kwargs: Additional provider-specific parameters
			
		Returns:
			Dictionary with analysis results
		"""
		pass

	@abstractmethod
	def _prepare_request(
			self,
			prompt: str,
			media_urls: Optional[list[str]] = None,
			**kwargs
	) -> dict[str, Any]:
		"""
		Prepare API request payload for the specific provider.
		
		Args:
			prompt: Text prompt
			media_urls: Optional media URLs
			**kwargs: Additional parameters
			
		Returns:
			Request payload dictionary
		"""
		pass

	@abstractmethod
	def _parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
		"""
		Parse provider-specific response into a unified format.
		
		Args:
			response: Raw API response
			
		Returns:
			Parsed analysis results
		"""
		pass


class OpenAIClient(LLMClient):
	"""LLM client for OpenAI API (GPT-4 Vision, etc.)."""

	async def analyze(
			self,
			prompt: str,
			media_urls: Optional[list[str]] = None,
			**kwargs
	) -> dict[str, Any]:
		"""Analyze content with enhanced error handling and logging."""

		if not self.api_key:
			raise ValueError(f"API key not configured for {self.provider.name}")

		# Apply rate limiting
		await self._apply_rate_limit()

		payload = self._prepare_request(prompt, media_urls, **kwargs)

		# Уменьшаем таймаут для избежания долгих ожиданий
		timeout = min(self.config.get("timeout", 90.0), 60.0)  # Максимум 60 секунд

		safe_payload = {
			'model': payload.get('model'),
			'message_count': len(payload.get('messages', [])),
			'media_count': len(media_urls) if media_urls else 0,
			'temperature': payload.get('temperature'),
			'max_tokens': payload.get('max_tokens'),
			'timeout': timeout
		}

		logger.info(f"Making request to {self.provider.name} with timeout {timeout}s: {safe_payload}")

		try:
			async with httpx.AsyncClient() as client:
				response = await client.post(
					self.api_url,
					headers={
						"Authorization": f"Bearer {self.api_key}",
						"Content-Type": "application/json"
					},
					json=payload,
					timeout=timeout,
				)

				if response.status_code != 200:
					error_detail = f"API returned status {response.status_code}: {response.text}"
					logger.error(f"API Error for {self.provider.name}: {error_detail}")
					raise httpx.HTTPStatusError(error_detail, request=response.request, response=response)

				response_data = response.json()
				result = self._parse_response(response_data)

				usage = response_data.get('usage', {})
				logger.info(
					f"✅ Request successful - Tokens: {usage.get('total_tokens', 'N/A')}"
				)

				return {
					"request": {
						"model": self.model_name,
						"prompt": prompt,
						"media_count": len(media_urls) if media_urls else 0,
						"provider": self.provider.name.lower(),
						"config": safe_payload
					},
					"response": response_data,
					"parsed": result
				}

		except httpx.TimeoutException:
			logger.error(f"Timeout for {self.provider.name} after {timeout}s")
			return {
				"request": safe_payload,
				"response": {"error": "timeout"},
				"parsed": {"analysis": "Timeout - analysis skipped"}
			}
		except Exception as e:
			logger.error(f"Unexpected error for {self.provider.name}: {str(e)}")
			return {
				"request": safe_payload,
				"response": {"error": str(e)},
				"parsed": {"analysis": f"Error: {str(e)}"}
			}

	def _prepare_request(
			self,
			prompt: str,
			media_urls: Optional[list[str]] = None,
			**kwargs
	) -> dict[str, Any]:
		"""
		Prepare API request payload with optimized defaults for content analysis.

		Uses merged configuration from defaults → provider → model with proper type handling.

		Args:
			prompt: Text prompt for analysis
			media_urls: Optional media URLs for multimodal analysis
			**kwargs: Additional parameters that override all other configs

		Returns:
			Request payload dictionary ready for API call
		"""
		messages = []
		content = prompt

		# Add system prompt for consistent behavior
		system_prompt = self._get_system_prompt(media_urls)
		messages.append({"role": "system", "content": system_prompt})

		# Build messages array based on content type
		if media_urls:
			# Multimodal request with images — construct complex content array
			content = [{"type": "text", "text": prompt}]
			for media in media_urls:
				if media.startswith('data:image/'):
					# Handle base64-encoded image data
					content.append({
						"type": "image_url",
						"image_url": {"url": media}
					})
				else:
					# Handle regular image URLs
					content.append({
						"type": "image_url",
						"image_url": {"url": media}
					})

		messages.append({"role": "user", "content": content})

		# Build final request data with proper type conversion
		request_data = {
			"model": self.model_name,
			"messages": messages,
			"temperature": float(self.config.get("temperature", 0.3)),
			"max_tokens": int(self.config.get("max_tokens", 400)),
			"stream": bool(self.config.get("stream", False)),
			"timeout": float(self.config.get("timeout", 60.0)),
		}

		# Add optional parameters if they exist in config
		optional_params = ["top_p", "frequency_penalty", "presence_penalty", "stop", "seed"]
		for param in optional_params:
			if param in self.config:
				request_data[param] = self.config[param]

		# Apply any kwargs (highest priority overrides)
		request_data.update(kwargs)

		# Set response format to JSON for text-only non-streaming requests
		if not media_urls and not request_data.get("stream"):
			request_data["response_format"] = {"type": "json_object"}

		return request_data

	def _parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
		"""Common response parsing for OpenAI-compatible providers."""

		try:
			if "choices" in response:
				content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
			elif "completion" in response:
				content = response.get("completion", "{}")  # if some providers use different field names
			else:
				content = "{}"

			# Try to parse as JSON, fall back to raw text
			try:
				return json.loads(content)
			except json.JSONDecodeError:
				return {"analysis": content}

		except (KeyError, IndexError) as e:
			logger.warning(f"Failed to parse {self.provider.name} response: {e}")
			return {"raw_response": str(response), "parse_error": str(e)}


class AnthropicClient(OpenAIClient):
	pass


class GoogleAIClient(OpenAIClient):
	pass


class MistralClient:
	pass


class GroqClient:
	pass


class LLMClientFactory:
	"""Factory for creating appropriate LLM clients based on provider type."""

	# OpenAI-compatible providers mapping
	_openai_compatible_map = {
		"openai": OpenAIClient,
		"deepseek": OpenAIClient,
		"anthropic": AnthropicClient,
		"google": GoogleAIClient,
		"mistral": MistralClient,
		"groq": GroqClient,
		"together": OpenAIClient,
		"cohere": OpenAIClient,
		"sambanova": OpenAIClient,
	}

	# Unique providers with custom implementations
	_unique_providers_map = {
		# "claude": ClaudeNativeClient,  # If using native API
		# "bedrock": BedrockClient,      # AWS Bedrock
		# "vertexai": VertexAIClient,    # Google Vertex AI
	}

	@classmethod
	async def create(cls, model: LLMModel = None) -> 'LLMClient':
		"""
		Factory method to create appropriate client for provider.
		Detects client type by API URL.
		
		Args:
			model: LLMModel instance (required)
			
		Returns:
			Specific LLMClient implementation
		"""
		provider = model.provider
		provider_key = provider.name.lower()

		# Check unique providers first
		if provider_key in cls._unique_providers_map:
			client_class = cls._unique_providers_map[provider_key]

		# Check OpenAI-compatible providers
		elif provider_key in cls._openai_compatible_map:
			client_class = cls._openai_compatible_map[provider_key]
		else:
			# Fallback to base OpenAI client for unknown providers
			logger.warning(f"No specific client for {provider_key}, using OpenAIClient as fallback")
			client_class = OpenAIClient

		logger.info(
			f"Creating {client_class.__name__} for {provider.name}, model: {model.name if model else 'default'}"
		)
		return client_class(model)
