import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import json
import asyncio

import httpx

from app.core import config
from app.models import LLMProvider

logger = logging.getLogger(__name__)

# Global rate limiter to avoid 429 errors
# Track last request time per provider
_last_request_time: Dict[str, float] = {}
_rate_limit_delay = config.LLM_REQUEST_DELAY / 1000  # seconds between requests


class LLMClient(ABC):
	"""
	Abstract base class for LLM clients.
	
	Provides a unified interface for different LLM providers (DeepSeek, OpenAI, etc.)
	Each implementation handles provider-specific API calls and response formatting.
	"""
	
	def __init__(self, provider: LLMProvider):
		"""
		Initialize LLM client with provider configuration.
		
		Args:
			provider: LLMProvider instance with API configuration
		"""
		self.provider = provider
		self.api_key = provider.get_api_key()
		self.api_url = provider.api_url
		self.model_name = provider.model_name
		self.config = provider.config or {}
	
	def _get_provider_name(self) -> str:
		"""Get provider name for analytics tracking."""
		from app.utils.enum_helpers import get_enum_value
		provider_type = get_enum_value(self.provider.provider_type)
		# For custom providers, try to detect from API URL or name
		if provider_type == 'custom':
			if 'sambanova' in self.provider.api_url.lower():
				return 'sambanova'
			if 'openrouter' in self.provider.api_url.lower():
				return 'openrouter'
			# Fallback to provider name
			return self.provider.name.lower().replace(' ', '_')
		return provider_type
	
	async def _apply_rate_limit(self):
		"""Apply rate limiting to avoid 429 errors."""
		global _last_request_time
		
		provider_key = self._get_provider_name()
		current_time = asyncio.get_event_loop().time()
		
		if provider_key in _last_request_time:
			elapsed = current_time - _last_request_time[provider_key]
			if elapsed < _rate_limit_delay:
				delay = _rate_limit_delay - elapsed
				logger.debug(f"Rate limiting: waiting {delay:.2f}s for {provider_key}")
				await asyncio.sleep(delay)
		
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


class DeepSeekClient(LLMClient):
	"""LLM client for DeepSeek API."""
	
	async def analyze(
		self,
		prompt: str,
		media_urls: Optional[list[str]] = None,
		**kwargs
	) -> dict[str, Any]:
		"""
		Analyze content using DeepSeek API.
		
		Args:
			prompt: Text prompt for analysis
			media_urls: Not supported by DeepSeek (text-only)
			**kwargs: Additional parameters
			
		Returns:
			Dictionary with 'request' and 'response' keys
		"""
		if media_urls:
			logger.warning("DeepSeek does not support media URLs, ignoring them")
		
		if not self.api_key:
			raise ValueError(f"API key not configured for {self.provider.name}")
		
		# Apply rate limiting
		await self._apply_rate_limit()
		
		payload = self._prepare_request(prompt, **kwargs)
		
		async with httpx.AsyncClient() as client:
			response = await client.post(
				self.api_url,
				headers={
					"Authorization": f"Bearer {self.api_key}",
					"Content-Type": "application/json"
				},
				json=payload,
				timeout=60.0,
			)
			response.raise_for_status()
			
			result = self._parse_response(response.json())
			
			return {
				"request": {
					"model": self.model_name, 
					"prompt": prompt,
					"provider": self._get_provider_name()
				},
				"response": response.json(),
				"parsed": result
			}
	
	def _prepare_request(
		self,
		prompt: str,
		media_urls: Optional[list[str]] = None,
		**kwargs
	) -> dict[str, Any]:
		"""Prepare DeepSeek API request."""
		return {
			"model": self.model_name,
			"messages": [{"role": "user", "content": prompt}],
			"temperature": self.config.get("temperature", 0.2),
			"max_tokens": self.config.get("max_tokens", 2000),
			"response_format": {"type": "json_object"},
		}
	
	def _parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
		"""Parse DeepSeek response."""
		try:
			content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
			return json.loads(content)
		except (json.JSONDecodeError, KeyError, IndexError) as e:
			logger.warning(f"Failed to parse DeepSeek response: {e}")
			return {"raw_response": str(response), "parse_error": str(e)}


class OpenAIClient(LLMClient):
	"""LLM client for OpenAI API (GPT-4 Vision, etc.)."""
	
	async def analyze(
		self,
		prompt: str,
		media_urls: Optional[list[str]] = None,
		**kwargs
	) -> dict[str, Any]:
		"""
		Analyze content using OpenAI API.
		
		Supports both text and vision models (images).
		
		Args:
			prompt: Text prompt for analysis
			media_urls: Optional image URLs for vision analysis
			**kwargs: Additional parameters
			
		Returns:
			Dictionary with analysis results
		"""
		if not self.api_key:
			raise ValueError(f"API key not configured for {self.provider.name}")
		
		# Apply rate limiting
		await self._apply_rate_limit()
		
		payload = self._prepare_request(prompt, media_urls, **kwargs)
		
		async with httpx.AsyncClient() as client:
			response = await client.post(
				self.api_url,
				headers={
					"Authorization": f"Bearer {self.api_key}",
					"Content-Type": "application/json"
				},
				json=payload,
				timeout=90.0,
			)
			response.raise_for_status()
			
			result = self._parse_response(response.json())
			
			return {
				"request": {
					"model": self.model_name, 
					"prompt": prompt, 
					"media_count": len(media_urls) if media_urls else 0,
					"provider": self._get_provider_name()
				},
				"response": response.json(),
				"parsed": result
			}
	
	def _prepare_request(
		self,
		prompt: str,
		media_urls: Optional[list[str]] = None,
		**kwargs
	) -> dict[str, Any]:
		"""Prepare OpenAI API request with optional vision support."""
		messages = []
		
		if media_urls:
			# Vision request with images
			content = [{"type": "text", "text": prompt}]
			for url in media_urls:
				content.append({
					"type": "image_url",
					"image_url": {"url": url}
				})
			messages.append({"role": "user", "content": content})
		else:
			# Text-only request
			messages.append({"role": "user", "content": prompt})
		
		return {
			"model": self.model_name,
			"messages": messages,
			"temperature": self.config.get("temperature", 0.2),
			"max_tokens": self.config.get("max_tokens", 2000),
			"response_format": {"type": "json_object"} if not media_urls else None,
		}
	
	def _parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
		"""Parse OpenAI response."""
		try:
			content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
			# Try to parse as JSON, fall back to raw text
			try:
				return json.loads(content)
			except json.JSONDecodeError:
				return {"analysis": content}
		except (KeyError, IndexError) as e:
			logger.warning(f"Failed to parse OpenAI response: {e}")
			return {"raw_response": str(response), "parse_error": str(e)}


class LLMClientFactory:
	"""Factory for creating appropriate LLM clients based on provider type."""
	
	_client_map = {
		"deepseek": DeepSeekClient,
		"openai": OpenAIClient,
		"custom": DeepSeekClient,  # Custom providers use OpenAI-compatible API
		# Add more providers here as needed
		# "anthropic": AnthropicClient,
		# "google": GoogleClient,
	}
	
	@classmethod
	def create(cls, provider: LLMProvider) -> LLMClient:
		"""
		Create an LLM client for the given provider.
		
		Args:
			provider: LLMProvider instance
			
		Returns:
			Appropriate LLMClient implementation
			
		Raises:
			ValueError: If provider type is not supported
		"""
		from app.utils.enum_helpers import get_enum_value
		provider_type = get_enum_value(provider.provider_type)
		
		client_class = cls._client_map.get(provider_type.lower())
		
		if not client_class:
			logger.warning(f"Unknown provider type: {provider_type}, using DeepSeekClient as fallback")
			client_class = DeepSeekClient
		
		logger.info(f"Creating {client_class.__name__} for provider: {provider.name}")
		return client_class(provider)
	
	@classmethod
	def register_client(cls, provider_type: str, client_class: type):
		"""
		Register a custom LLM client for a provider type.
		
		Args:
			provider_type: Provider type string
			client_class: LLMClient subclass
		"""
		cls._client_map[provider_type.lower()] = client_class
		logger.info(f"Registered custom client {client_class.__name__} for {provider_type}")
