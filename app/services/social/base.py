import logging
from abc import ABC, abstractmethod
import httpx

from app.models import Source
from app.types.models import SourceType

logger = logging.getLogger(__name__)


class BaseClient(ABC):
	"""Базовый класс для всех клиентов социальных платформ"""

	def __init__(self, platform):
		self.platform = platform

	async def collect_data(self, source: Source, content_type: str = "posts") -> list[dict]:
		"""
		Асинхронный метод сбора данных - ТОЧКА ВХОДА
		
		Args:
			source: Источник данных
			content_type: Тип контента (по умолчанию: "posts")
			
		Returns:
			list[dict]: Список нормализованных данных
		"""
		try:
			# 1. Получаем метод API для типа источника
			method = self._get_api_method(source.source_type, content_type)

			# 2. Формируем параметры
			params = self._build_params(source, method)

			# 3. Выполняем асинхронный запрос
			res = await self._make_request(method, params)

			# 4. Нормализуем данные
			normalized_data = self._normalize_response(res, source.source_type)
			return normalized_data or []

		except Exception as e:
			self._handle_error(source, e)
			return []

	async def _make_request(self, method: str, params: dict) -> dict:
		"""
		Асинхронная общая логика HTTP запроса с использованием httpx

		Args:
			method: API endpoint method
			params: Query parameters for the request

		Returns:
			dict: Parsed JSON response

		Raises:
			HTTPError: If the request fails
		"""
		api_base_url = self.platform.params.get('api_base_url')
		async with httpx.AsyncClient() as client:
			response = await client.get(
				f"{api_base_url}/{method}",
				params=params,
				timeout=30.0
			)
			response.raise_for_status()
			return response.json()

	def _handle_error(self, source: Source, error: Exception):
		"""Общая обработка ошибок"""
		logger.error(f"Ошибка сбора данных для {source.name}: {error}")

	# ⚡️ АБСТРАКТНЫЕ МЕТОДЫ - должны быть реализованы в дочерних классах
	@abstractmethod
	def _get_api_method(self, source_type: SourceType, content_type: str) -> str:
		"""Возвращает метод API для типа источника и контента"""
		pass

	@abstractmethod
	def _build_params(self, source: Source, method: str) -> dict:
		"""Формирует параметры для API запроса"""
		pass

	@abstractmethod
	def _normalize_response(self, raw_data: dict, source_type: SourceType) -> list[dict]:
		"""Приводит данные платформы к единому формату"""
		pass
