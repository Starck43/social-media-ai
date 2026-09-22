import json
import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqladmin import action
from sqladmin.fields import SelectField
from sqlalchemy import Select
from starlette.requests import Request
from starlette.responses import RedirectResponse
from wtforms.fields.choices import SelectMultipleField

from app.admin.actions import LLMModelActions
from app.models import (
	User,
	Role,
	Permission,
	Notification,
	Platform,
	Source,
	SourceUserRelationship,
	BotScenario,
	AIAnalytics,
	LLMProvider,
	LLMModel,
)
from app.models.managers.base_manager import prefetch
from app.types import (
	SourceType, ContentType, AnalysisType, LLMStrategyType, BotActionType, BotTriggerType, NotificationType
)
from app.types.enums.llm_types import MediaType
from .base import BaseAdmin
from .widgets import EuropeanDateField

logger = logging.getLogger(__name__)


class UserAdmin(BaseAdmin, model=User):
	name = "Пользователь"
	name_plural = "Пользователи"
	icon = "fa fa-user"

	column_list = ["id", "username", "email", "is_active", "role", "updated_at"]
	column_labels = dict({
		"id": "ID",
		"username": "Имя пользователя",
		"email": "Email",
		"role": "Роль",
		"hashed_password": "Пароль",
		"is_superuser": "Администратор",
	}, **BaseAdmin.column_labels)
	column_searchable_list = ["username", "email"]
	column_sortable_list = ["is_active", "username"]
	column_default_sort = [("updated_at", True)]
	column_details_exclude_list = [
		"social_accounts",
		"notifications",
		"role_id",
	]
	form_create_rules = ["username", "email", "hashed_password", "role", "is_active"]
	form_edit_rules = ["username", "email", "role", "is_active"]

	form_widget_args = {
		"hashed_password": {"type": "password"}
	}

	def on_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
		"""Handle datetime conversion for form display."""
		# Convert datetime objects to strings for date fields in forms
		if not is_created and model:  # Only for editing existing models
			if hasattr(model, 'date_from') and model.date_from:
				data['date_from'] = model.date_from.strftime('%Y-%m-%d')
			if hasattr(model, 'date_to') and model.date_to:
				data['date_to'] = model.date_to.strftime('%Y-%m-%d')

		super().on_model_change(data, model, is_created, request)

	async def insert_model(self, request, data: dict) -> Any:
		"""Ensure a password is set on user creation."""
		if "hashed_password" not in data or not data["hashed_password"]:
			raise HTTPException(status_code=400, detail="Требуется заполнить поле 'Пароль'")
		return await super().insert_model(request, data)

	@action(
		name="change_password",
		label="Изменить пароль",
		add_in_list=True,
		add_in_detail=True,
	)
	async def change_password_action(self, request: Request):
		"""Redirect to change password page."""
		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		user_id = pks.split(",")[0]
		return RedirectResponse(
			url=f"/admin/user/change-password/{user_id}",
			status_code=303,
		)


class RoleAdmin(BaseAdmin, model=Role):
	name = "Роль"
	name_plural = "Роли"
	icon = "fa fa-shield"
	column_list = ["id", "name", "description"]
	column_searchable_list = ["name"]
	column_labels = dict({
		"id": "ID",
		"name": "Название",
		"codename": "Кодовое наименование",
		"description": "Описание",
		"users": "Пользователи",
		"permissions": "Разрешения",
	}, **BaseAdmin.column_labels)


class PermissionAdmin(BaseAdmin, model=Permission):
	name = "Разрешение"
	name_plural = "Разрешения"
	icon = "fa fa-key"
	column_list = ["id", "codename", "name", "description"]
	column_searchable_list = ["codename", "name"]
	column_sortable_list = ["codename"]
	column_labels = dict({
		"id": "ID",
		"model_type": "Приложение.Таблица",
		"model_type_id": "ID типа модели",
		"action_type": "Вид разрешения",
		"codename": "Кодовое имя",
		"name": "Название",
		"description": "Описание",
		"roles": "Роли",
	}, **BaseAdmin.column_labels)
	column_details_exclude_list = ["model_type", "model_type_id"]

	form_excluded_columns = ["model_type_id", "codename"] + BaseAdmin.form_excluded_columns


class PlatformAdmin(BaseAdmin, model=Platform):
	name = "Платформа"
	name_plural = "Платформы"
	icon = "fa fa-globe"
	column_list = ["id", "name", "platform_type", "is_active"]
	column_searchable_list = ["name"]
	column_sortable_list = ["name", "is_active", "last_sync"]
	column_labels = {
		"id": "ID",
		"name": "Название",
		"platform_type": "Тип платформы",
		"is_active": "Активна",
		"base_url": "УРЛ платформы",
		"params": "Настройки API запросов",
		"rate_limit_remaining": "Остаток лимитов",
		"rate_limit_reset_at": "Сброс лимитов",
		"sources": "Источники",
	}

	form_excluded_columns = ["sources"] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		"rate_limit_remaining": {
			"readonly": True,
		},
		"rate_limit_reset_at": {
			"readonly": True,
		},
	}

	@action(
		name="sync_platform",
		label="Синхронизировать",
		add_in_list=True,
		add_in_detail=True,
	)
	async def sync_platform_action(self, request: Request):
		"""Trigger platform synchronization."""
		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		# TODO: Implement platform synchronization logic
		return RedirectResponse(
			url=request.url_for("admin:list", identity=self.identity),
			status_code=303,
		)


class SourceAdmin(BaseAdmin, model=Source):
	name = "Источник"
	name_plural = "Источники"
	icon = "fa fa-rss"
	column_list = [
		"id",
		"platform",
		"name",
		"source_type",
		"external_id",
		"bot_scenario",
		"is_active",
		"last_checked",
		"date_from",
		"date_to",
	]
	column_searchable_list = ["name", "external_id"]
	column_sortable_list = ["name", "is_active", "last_checked"]
	column_labels = dict(
		{
			"id": "ID",
			"platform": "Платформа",
			"name": "Название",
			"platform_id": "ID платформы",
			"source_type": "Тип источника",
			"external_id": "Внешний ID источника",
			"params": "Параметры",
			"bot_scenario": "Сценарий бота",
			"last_checked": "Последняя проверка",
			"date_from": "Дата начала сбора",
			"date_to": "Дата окончания сбора",
			"analytics": "Аналитика",
			"monitored_users": "Отслеживаемые пользователи",
			"tracked_in_sources": "Отслеживается в источниках",
		},
		**BaseAdmin.column_labels,
	)
	column_details_exclude_list = ["platform_id", "bot_scenario_id"]

	form_columns = [
		"platform",
		"name",
		"source_type",
		"external_id",
		"bot_scenario",
		"params",
		"is_active",
		"monitored_users",
		"date_from",
		"date_to",
	]
	form_widget_args = {
		"last_checked": {
			"readonly": True,
		},
		"date_from": {"placeholder": "ДД-ММ-ГГГГ"},
		"date_to": {"placeholder": "ДД-ММ-ГГГГ"},
	}
	form_overrides = {
		"date_from": EuropeanDateField,
		"date_to": EuropeanDateField,
	}
	form_args = {
		"date_from": {
			"label": "Дата начала сбора",
			"description": "Дата начала мониторинга источника",
		},
		"date_to": {
			"label": "Дата окончания сбора",
			"description": "Дата окончания мониторинга источника. Оставьте пустым для бессрочного мониторинга",
		},
	}
	column_formatters = {
		"last_checked": lambda m, a: m.last_checked.strftime("%d.%m.%Y %H:%M") if m.last_checked else "",
		"date_from": lambda m, a: m.date_from.strftime("%d.%m.%Y") if m.date_from else "—",
		"date_to": lambda m, a: m.date_to.strftime("%d.%m.%Y") if m.date_to else "—",
	}

	# Use custom templates for create/edit/details to inject per-view JS
	create_template = "sqladmin/source_create.html"
	edit_template = "sqladmin/source_edit.html"
	details_template = "sqladmin/source_details.html"

	def list_query(self, request: Request) -> Select:
		return Source.objects.prefetch_related(
			"monitored_users",
			"tracked_in_sources",
		).to_select()

	def details_query(self, request: Request) -> Select:
		pk = int(request.path_params["pk"])

		monitored_prefetch = prefetch("monitored_users", queryset=Source.objects.exclude(id=pk))
		# monitored_prefetch = prefetch("monitored_users", filters={"id__ne": pk})

		return (
			Source.objects.prefetch_related(
				monitored_prefetch,
				"tracked_in_sources",
				"analytics",
				"platform",
			)
			.filter(id=pk)
			.to_select()
		)

	async def scaffold_form(self, rules=None):
		"""
		Configure create/edit form.

		For monitored_users field:
		— Show only USER sources
		— Filter by current source platform (when editing)
		— Always exclude a current source from the list
		"""
		form_class = await super().scaffold_form(rules)

		if hasattr(form_class, "monitored_users"):
			current_platform_id = None
			current_source_id = None

			try:
				if hasattr(self, "request") and self.request:
					current_source_id = self.request.path_params.get("pk")

				if not current_source_id and hasattr(self, "model_id"):
					current_source_id = self.model_id

				if current_source_id:
					current_source = await Source.objects.get(id=int(current_source_id))
					if current_source:
						current_platform_id = current_source.platform_id
			except (ValueError, KeyError, AttributeError, TypeError):
				pass

			qs = Source.objects.filter(source_type=SourceType.USER.name)

			if current_platform_id:
				qs = qs.filter(platform_id=current_platform_id)

			if current_source_id:
				qs = qs.exclude(id=int(current_source_id))

			sources = await qs.order_by(Source.name, Source.id)

			form_class.monitored_users.kwargs.update(
				{
					"data": [(str(source.id), source) for source in sources],
					"get_label": lambda obj: obj.name or obj.external_id or f"Источник #{obj.id}",
				}
			)

		return form_class

	@action(name="check_source", label="Проверить сейчас", add_in_list=True, add_in_detail=True)
	async def check_source_action(self, request: Request):
		"""Collect content and display in a template."""
		from app.services.social.factory import get_social_client
		from starlette.templating import Jinja2Templates
		from pathlib import Path
		import sqladmin

		# Include both app templates and sqladmin templates
		sqladmin_path = Path(sqladmin.__file__).parent
		template_dirs = [
			str(Path(__file__).parent.parent / "templates"),
			str(sqladmin_path / "templates")
		]
		templates = Jinja2Templates(directory=template_dirs)

		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		source_id = int(pks.split(",")[0])
		source = await Source.objects.select_related("platform").get(id=source_id)

		if not source:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		# Get pagination params
		page = int(request.query_params.get("page", 1))
		per_page = int(request.query_params.get("per_page", 20))
		offset = (page - 1) * per_page

		# Collect content in real-time
		try:
			client = get_social_client(source.platform)

			# Temporarily modify source params for pagination
			original_params = source.params.copy() if source.params else {}
			if not source.params:
				source.params = {}
			if 'collection' not in source.params:
				source.params['collection'] = {}

			source.params['collection']['offset'] = offset
			source.params['collection']['count'] = per_page

			content = await client.collect_data(
				source=source,
				content_type="posts"
			)

			# Restore original params
			source.params = original_params

			# Calculate pagination
			total_count = len(content) if len(content) < per_page else (page * per_page + 1)
			total_pages = (total_count + per_page - 1) // per_page if content else 1
			has_next = len(content) == per_page
			has_prev = page > 1

			return templates.TemplateResponse(
				"sqladmin/source_check_results_standalone.html",
				{
					"request": request,
					"source": source,
					"content": content,
					"total_count": total_count,
					"checked_at": datetime.now(),
					"stats": {
						"total_likes": sum(item.get("likes", 0) for item in content),
						"total_comments": sum(item.get("comments", 0) for item in content),
						"total_views": sum(item.get("views", 0) for item in content),
					},
					"pagination": {
						"page": page,
						"per_page": per_page,
						"total_pages": total_pages,
						"has_next": has_next,
						"has_prev": has_prev,
						"offset": offset
					}
				}
			)
		except Exception as e:
			logger.error(f"Error checking source {source_id}: {e}")

			# Check for VK privacy error
			error_msg = str(e)
			error_type = "generic"
			error_details = None

			if "Access denied" in error_msg or "error_code: 15" in error_msg:
				error_type = "access_denied"
				error_details = {
					"title": "Стена закрыта настройками приватности",
					"message": "Пользователь ограничил доступ к своим записям в настройках VK.",
					"instructions": [
						"Попросите пользователя открыть стену в настройках:",
						"1. Перейти на vk.com/settings?act=privacy",
						"2. Раздел 'Кто видит мои записи на стене?'",
						"3. Выбрать 'Все пользователи' или 'Друзья и друзья друзей'"
					],
					"alternative": "Или используйте другой источник с открытой стеной"
				}

			return templates.TemplateResponse(
				"sqladmin/source_check_results_standalone.html",
				{
					"request": request,
					"source": source,
					"error": error_msg,
					"error_type": error_type,
					"error_details": error_details,
					"checked_at": datetime.now(),
				}
			)


class SourceUserRelationshipAdmin(BaseAdmin, model=SourceUserRelationship):
	name = "Отслеживание пользователя"
	name_plural = "Отслеживание пользователей"
	icon = "fa fa-user-plus"

	column_list = ["source_info", "user_info", "source.is_active", "source.updated_at"]

	column_labels = dict(
		{
			"source_info": "Источник",
			"user_info": "Пользователь",
			"source.platform_url": "Источник отслеживания",
			"user": "Пользователь",
			"source.is_active": "Активен",
			"source.updated_at": "Дата обновления"
		},
		**BaseAdmin.column_labels,
	)

	column_details_list = ["source.platform_url", "user", "source.is_active", "source.updated_at"]

	column_formatters = {
		"source_info": lambda m, a: (
			f"{m.source.name}"
			if getattr(m, "source", None) and getattr(m.source, "platform", None)
			else f"Источник #{m.source_id}"
		),
		"user_info": lambda m, a: (
			f"{m.user.name} • {m.user.platform_url}"
			if getattr(m, "user", None) and getattr(m.user, "platform", None)
			else f"Пользователь #{m.user_id}"
		),
		"source.updated_at": lambda m, a: (
			m.source.updated_at.strftime("%d.%m.%Y") if getattr(getattr(m, "source", None), "updated_at", None) else "—"
		),
	}

	def list_query(self, request: Request) -> Select:
		# Load all FK relationships including nested ones to avoid DetachedInstanceError
		# Use '__' syntax for nested relationships (Django-style)
		return SourceUserRelationship.objects.select_related("source__platform", "user__platform").to_select()

	def details_query(self, request: Request) -> Select:
		return self.list_query(request)

	async def scaffold_form(self, rules=None):
		form_class = await super().scaffold_form(rules)

		if hasattr(form_class, "source_id"):
			sources = await Source.objects.exclude(source_type=SourceType.USER.name).order_by(Source.name, Source.id)

			form_class.source_id.kwargs.update(
				{
					"data": [(str(s.id), s) for s in sources],
					"get_label": lambda obj: (
						f"{obj.name} • {obj.platform.name}" if obj.name else f"{obj.external_id} • {obj.platform.name}"
					),
				}
			)

		# Users for "user_id": только USER источники
		if hasattr(form_class, "user_id"):
			users = await Source.objects.filter(source_type=SourceType.USER.name).order_by(Source.name, Source.id)

			form_class.user_id.kwargs.update(
				{
					"data": [(str(u.id), u) for u in users],
					"get_label": lambda obj: (
						f"{obj.name} • {obj.platform.name}" if obj.name else f"{obj.external_id} • {obj.platform.name}"
					),
				}
			)

		return form_class


class BotScenarioAdmin(BaseAdmin, model=BotScenario):
	name = "Сценарий бота"
	name_plural = "Сценарии ботов"
	icon = "fa fa-robot"
	column_list = ["id", "name", "description", "is_active", "collection_interval_hours"]
	column_searchable_list = ["name", "description"]
	column_sortable_list = ["name", "is_active", "collection_interval_hours"]
	column_labels = dict({
		"id": "ID",
		"name": "Название",
		"description": "Описание сценария",

		# Media prompts
		"text_prompt": "Промпт для текста",
		"image_prompt": "Промпт для изображений",
		"video_prompt": "Промпт для видео",
		"audio_prompt": "Промпт для аудио",
		"unified_summary_prompt": "Промпт для общего резюме",

		"trigger_type": "Тип триггера",
		"trigger_config": "Настройки триггера",
		"action_type": "Действие после анализа",
		"analysis_types": "Типы анализа",
		"content_types": "Типы контента",
		"scope": "Дополнительные параметры",
		"collection_interval_hours": "Интервал сбора (часы)",
		"sources": "Источники",
		"text_llm_model": "Модель для текста",
		"image_llm_model": "Модель для изображений",
		"video_llm_model": "Модель для видео",
		"text_llm_provider_id": "ID модели для текста",
		"image_llm_provider_id": "ID модели для изображений",
		"video_llm_provider_id": "ID модели для видео",
		"llm_strategy": "Стратегия выбора модели"
	}, **BaseAdmin.column_labels)

	form_excluded_columns = BaseAdmin.form_excluded_columns + [
		"sources",
		"llm_mapping",
		# Exclude these fields — we handle them manually in custom template
		"content_types",
		"analysis_types",
		"scope",
		"trigger_type",
		"action_type",
		"trigger_config",
	]
	form_overrides = {
		'llm_strategy': SelectField,
		**BaseAdmin.form_overrides
	}
	form_args = {
		'llm_strategy': {
			'choices': LLMStrategyType.choices(),
			'coerce': str
		},
		'text_prompt': {
			'description': (
				'Кастомный промпт для анализа текста. Оставьте пустым для дефолтного. '
				'Переменные: {text}, {platform}, {source_type}, {total_posts}, {avg_reactions}, {avg_comments}'
			)
		},
		'image_prompt': {
			'description': (
				'Кастомный промпт для анализа изображений. Оставьте пустым для дефолтного. '
				'Переменные: {count}, {platform}'
			)
		},
		'video_prompt': {
			'description': (
				'Кастомный промпт для анализа видео. Оставьте пустым для дефолтного. '
				'Переменные: {count}, {platform}'
			)
		},
		'audio_prompt': {
			'description': (
				'Кастомный промпт для анализа аудио. Оставьте пустым для дефолтного. '
				'Переменные: {count}, {platform}'
			)
		},
		'unified_summary_prompt': {
			'description': (
				'Кастомный промпт для создания общего резюме из мультимедийного анализа. Оставьте пустым для дефолтного.'
			)
		},
		'scope': {
			'description': (
				'Дополнительные параметры для анализа. '
				'event_based: true - анализ по дням (для мониторинга активности), '
				'event_based: false - анализ по темам (ИИ сам определяет темы). '
				'Также может содержать конфигурацию для analysis_types (sentiment, keywords и др.). '
				'Пример: '
				'{"event_based": true, "sentiment": {"categories": ["Позитивный", "Негативный"]}, "max_events_per_analysis": 50}'
			)
		},
		**BaseAdmin.form_args
	}

	# Column formatters
	column_formatters = {
		"trigger_type": lambda m, a: (
			m.trigger_type.label
			if m.trigger_type and hasattr(m.trigger_type, 'label')
			else str(m.trigger_type) if m.trigger_type else "—"
		),
		"action_type": lambda m, a: (
			m.action_type.label
			if m.action_type and hasattr(m.action_type, 'label')
			else str(m.action_type) if m.action_type else "—"
		),
		**BaseAdmin.column_formatters
	}
	form_widget_args = {
		# Media prompts with placeholders
		"text_prompt": {
			"rows": 10,
			"placeholder": (
				"Проанализируй следующий текстовый контент из {platform}.\n\n"
				"Контент: {text}\n"
				"Всего постов: {total_posts}\n\n"
				"Определи основные темы, тональность и ключевые моменты."
			)
		},
		"image_prompt": {
			"rows": 10,
			"placeholder": (
				"Проанализируй {count} изображений из {platform}.\n\n"
				"Опиши визуальные элементы, стиль, основные объекты и общую тематику."
			)
		},
		"video_prompt": {
			"rows": 10,
			"placeholder": (
				"Проанализируй {count} видео из {platform}.\n\n"
				"Опиши контент видео, основные темы, стиль подачи."
			)
		},
		"audio_prompt": {
			"rows": 10,
			"placeholder": (
				"Проанализируй {count} аудиозаписей из {platform}.\n\n"
				"Определи темы обсуждения, тональность речи, ключевые моменты."
			)
		},
		"unified_summary_prompt": {
			"rows": 10,
			"placeholder": (
				"Создай единое резюме на основе следующих анализов:\n\n"
				"Текст: {text_analysis}\nИзображения: {image_analysis}\n"
				"Видео: {video_analysis}\n\nВыдели общие темы и ключевые инсайты."
			)
		},
		"description": {"rows": 2},
		"trigger_config": {
			"rows": 5,
			"placeholder": '{\n  "keywords": ["жалоба", "проблема"],\n  "mode": "any"\n}'
		},
		"scope": {
			"rows": 8,
			"placeholder": '{\n  "event_based": true,\n  "sentiment": {\n    "categories": ["Позитивный", "Негативный", "Нейтральный"]\n  },\n  "keywords": {\n    "max_keywords": 20\n  }\n}'
		},
	}

	create_template = "sqladmin/bot_scenario_create.html"
	edit_template = "sqladmin/bot_scenario_edit.html"

	async def scaffold_form(self, rules=None):
		"""Provide enum types and presets to template."""
		from app.core.scenario_presets import get_all_presets
		from app.core.trigger_hints import TRIGGER_HINTS, SCOPE_HINTS
		from app.core.analysis_constants import ANALYSIS_TYPE_DEFAULTS
		from app.core.trigger_constants import TRIGGER_CONFIG_DEFAULTS

		form = await super().scaffold_form(rules)

		form.content_types_enum = list(ContentType)
		form.analysis_types_enum = list(AnalysisType)
		form.trigger_types_enum = list(BotTriggerType)
		form.action_types_enum = list(BotActionType)
		form.trigger_hints = TRIGGER_HINTS
		form.scope_hints = SCOPE_HINTS

		# Convert list of presets to dict with keys (for template iteration)
		presets_list = get_all_presets()
		form.presets = {f"preset_{i}": preset for i, preset in enumerate(presets_list)}

		# Provide analysis defaults and all types for JavaScript
		form.analysis_defaults = ANALYSIS_TYPE_DEFAULTS
		form.all_analysis_types = [at.db_value for at in AnalysisType]

		# Provide trigger defaults for JavaScript
		form.trigger_defaults = TRIGGER_CONFIG_DEFAULTS

		return form

	def _parse_json_fields(self, data: dict) -> None:
		"""Parse JSON fields from form data (hidden inputs and textareas)."""
		# Parse content_types from a hidden field (JSON string)
		if "content_types" in data and isinstance(data["content_types"], str):
			try:
				data["content_types"] = json.loads(data["content_types"])
			except (json.JSONDecodeError, TypeError):
				data["content_types"] = []

		# Parse analysis_types from a hidden field (JSON string)
		if "analysis_types" in data and isinstance(data["analysis_types"], str):
			try:
				data["analysis_types"] = json.loads(data["analysis_types"])
			except (json.JSONDecodeError, TypeError):
				data["analysis_types"] = []

		# Parse scope from a textarea (JSON string)
		# Scope now supports unified format: {analysis: {...}, response_format: {...}, display: {...}}
		if "scope" in data and isinstance(data["scope"], str):
			try:
				scope_data = json.loads(data["scope"]) if data["scope"].strip() else {}

				# Check if unified format
				if "analysis" in scope_data:
					# Split unified config into separate fields
					data["scope"] = scope_data.get("analysis", {})
				else:
					data["scope"] = scope_data
			except (json.JSONDecodeError, TypeError):
				data["scope"] = {}

		# Parse trigger_config from a textarea (JSON string)
		if "trigger_config" in data and isinstance(data["trigger_config"], str):
			try:
				data["trigger_config"] = json.loads(data["trigger_config"]) if data["trigger_config"].strip() else {}
			except (json.JSONDecodeError, TypeError):
				data["trigger_config"] = {}

	async def _prepare_form_data(self, request: Request, data: dict) -> None:
		"""Extract and parse excluded fields from request."""
		form_data = await request.form()

		# Add excluded fields back to data
		for field in ["content_types", "analysis_types", "scope", "trigger_config"]:
			if field in form_data:
				data[field] = form_data.get(field)

		# Add trigger_type and action_type from hidden fields (they send NAME strings)
		if "trigger_type" in form_data:
			trigger_value = form_data.get("trigger_type")
			if trigger_value:
				# Convert NAME string to enum object
				try:
					data["trigger_type"] = BotTriggerType[trigger_value]
				except (KeyError, TypeError):
					data["trigger_type"] = None
			else:
				data["trigger_type"] = None

		if "action_type" in form_data:
			action_value = form_data.get("action_type")
			if action_value:
				# Convert NAME string to enum object
				try:
					data["action_type"] = BotActionType[action_value]
				except (KeyError, TypeError):
					data["action_type"] = None
			else:
				data["action_type"] = None

		# Parse JSON strings to Python objects
		self._parse_json_fields(data)

	async def insert_model(self, request: Request, data: dict) -> Any:
		"""Parse JSON fields before creating scenario."""
		await self._prepare_form_data(request, data)
		return await super().insert_model(request, data)

	async def update_model(self, request: Request, pk: Any, data: dict) -> Any:
		"""Parse JSON fields before updating scenario."""
		await self._prepare_form_data(request, data)
		return await super().update_model(request, pk, data)

	@action(
		name="view_prompts",
		label="👁️ Просмотр промптов",
		add_in_list=False,
		add_in_detail=True,
	)
	async def view_prompts_action(self, request: Request):
		"""View full prompts with JSON instructions."""
		from starlette.templating import Jinja2Templates
		from app.services.ai.prompts import PromptBuilder
		from app.types import MediaType
		from pathlib import Path
		import sqladmin

		# Get scenario ID
		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		scenario_id = int(pks.split(",")[0])

		# Load scenario
		try:
			scenario = await BotScenario.objects.get(id=scenario_id)
		except Exception:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		# Build prompts with auto-append JSON
		prompts_data = {}

		# Text prompt
		text_prompt = PromptBuilder.get_prompt(
			MediaType.TEXT,
			scenario=scenario,
			text="{text}",
			platform_name="{platform}",
			source_type="{source_type}",
			stats={"total_posts": "{total_posts}", "avg_reactions": "{avg_reactions}"}
		)
		prompts_data['text'] = {
			'custom': scenario.text_prompt if scenario.text_prompt else None,
			'full': text_prompt,
			'has_custom': bool(scenario.text_prompt)
		}

		# Image prompt
		image_prompt = PromptBuilder.get_prompt(
			MediaType.IMAGE,
			scenario=scenario,
			count="{count}",
			platform_name="{platform}"
		)
		prompts_data['image'] = {
			'custom': scenario.image_prompt if scenario.image_prompt else None,
			'full': image_prompt,
			'has_custom': bool(scenario.image_prompt)
		}

		# Video prompt
		video_prompt = PromptBuilder.get_prompt(
			MediaType.VIDEO,
			scenario=scenario,
			count="{count}",
			platform_name="{platform}"
		)
		prompts_data['video'] = {
			'custom': scenario.video_prompt if scenario.video_prompt else None,
			'full': video_prompt,
			'has_custom': bool(scenario.video_prompt)
		}

		# Audio prompt
		audio_prompt = PromptBuilder.get_prompt(
			MediaType.AUDIO,
			scenario=scenario,
			count="{count}",
			platform_name="{platform}"
		)
		prompts_data['audio'] = {
			'custom': scenario.audio_prompt if scenario.audio_prompt else None,
			'full': audio_prompt,
			'has_custom': bool(scenario.audio_prompt)
		}

		# Unified summary prompt
		unified_prompt = PromptBuilder.get_unified_summary_prompt(
			text_analysis={},
			image_analysis={},
			video_analysis={},
			scenario=scenario
		)
		prompts_data['unified'] = {
			'custom': scenario.unified_summary_prompt if scenario.unified_summary_prompt else None,
			'full': unified_prompt,
			'has_custom': bool(scenario.unified_summary_prompt)
		}

		# Setup templates
		sqladmin_path = Path(sqladmin.__file__).parent
		template_dirs = [
			str(Path(__file__).parent.parent / "templates"),
			str(sqladmin_path / "templates")
		]
		templates = Jinja2Templates(directory=template_dirs)

		# Prepare scope and trigger_config for display
		scope_display = {}
		if scenario.scope:
			# Separate analysis type configs from custom variables
			analysis_configs = {}
			custom_vars = {}

			for key, value in scenario.scope.items():
				if scenario.analysis_types and key in scenario.analysis_types:
					analysis_configs[key] = value
				else:
					custom_vars[key] = value

			scope_display = {
				'analysis_configs': analysis_configs,
				'custom_variables': custom_vars,
				'all': scenario.scope
			}

		# Pre-format JSON strings with ensure_ascii=False for proper Unicode display
		import json

		# Format analysis configs as JSON strings
		analysis_configs_json = {}
		if scope_display.get('analysis_configs'):
			for key, value in scope_display['analysis_configs'].items():
				analysis_configs_json[key] = json.dumps(value, indent=2, ensure_ascii=False)

		# Format custom variables as JSON strings
		custom_vars_json = {}
		if scope_display.get('custom_variables'):
			for key, value in scope_display['custom_variables'].items():
				custom_vars_json[key] = json.dumps(value, ensure_ascii=False)

		# Format trigger_config as JSON string
		trigger_config_json = ""
		if scenario.trigger_config:
			trigger_config_json = json.dumps(scenario.trigger_config, indent=2, ensure_ascii=False)

		return templates.TemplateResponse(
			"sqladmin/scenario_prompts.html",
			{
				"request": request,
				"scenario": scenario,
				"prompts": prompts_data,
				"scope_display": scope_display,
				"analysis_configs_json": analysis_configs_json,
				"custom_vars_json": custom_vars_json,
				"trigger_config": scenario.trigger_config if scenario.trigger_config else {},
				"trigger_config_json": trigger_config_json,
			}
		)

	@action(
		name="toggle_active",
		label="Активировать/Деактивировать",
		add_in_list=True,
		add_in_detail=True,
	)
	async def toggle_active_action(self, request: Request):
		"""Toggle scenario active status."""
		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		for pk in pks.split(","):
			try:
				scenario = await BotScenario.objects.get(id=int(pk))
				if scenario:
					new_status = not scenario.is_active
					await BotScenario.objects.update_by_id(int(pk), is_active=new_status)
					logger.info(f"Scenario {pk} status changed to {new_status}")
			except Exception as e:
				logger.error(f"Error toggling scenario {pk}: {e}")

		return RedirectResponse(
			url=request.url_for("admin:list", identity=self.identity),
			status_code=303,
		)


class AIAnalyticsAdmin(BaseAdmin, model=AIAnalytics):
	name = "AI Аналитика"
	name_plural = "AI Аналитика"
	icon = "fa fa-chart-bar"

	column_list = ["id", "source", "period_type", "topic_chain_id", "analysis_date", "created_at"]
	column_searchable_list = ["source.name", "period_type"]
	column_sortable_list = ["analysis_date", "topic_chain_id"]
	column_labels = dict({
		"id": "ID",
		"source": "Источник",
		"source_id": "ID источника",
		"analysis_date": "Дата анализа",
		"summary_data": "Данные анализа",
		"main_topics": "Основные темы",
		"period_type": "Период",
		"topic_chain_id": "Цепочка",
		"llm_model": "Модель ИИ",
	}, **BaseAdmin.column_labels)

	form_excluded_columns = ["summary_data"] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		"analysis_date": {
			"readonly": True,
		},
	}

	column_formatters = {
		"analysis_date": lambda m, a: m.analysis_date.strftime("%d.%m.%Y %H:%M") if hasattr(m, 'analysis_date') else "",
		"period_type": lambda m, a: (
			m.period_type.label
			if m.period_type and hasattr(m.period_type, 'label')
			else str(m.period_type) if m.period_type else "—"
		),
	}

	details_template = "sqladmin/ai_analytics_detail.html"

	@action(
		name="view_analysis",
		label="Просмотр анализа",
		add_in_list=True,
		add_in_detail=True,
	)
	async def view_analysis_action(self, request: Request):
		"""View detailed analysis."""
		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		analysis_id = pks.split(",")[0]
		# Правильный роут sqladmin: /admin/{identity}/details/{pk}
		return RedirectResponse(
			url=request.url_for("admin:details", identity=self.identity, pk=analysis_id),
			status_code=303,
		)


class NotificationAdmin(BaseAdmin, model=Notification):
	name = "Уведомление"
	name_plural = "Уведомления"
	icon = "fa fa-bell"
	column_list = ["id", "title", "notification_type", "is_read", "created_at"]
	column_searchable_list = ["title", "notification_type"]
	column_sortable_list = ["created_at", "is_read"]
	column_default_sort = [("created_at", True)]
	column_labels = dict({
		"id": "ID",
		"title": "Заголовок",
		"message": "Сообщение",
		"notification_type": "Тип уведомления",
		"is_read": "Прочитано",
		"related_entity_type": "Тип сущности",
		"related_entity_id": "ID сущности",
	}, **BaseAdmin.column_labels)

	column_formatters = {
		"is_read": lambda m, a: "✅ Прочитано" if m.is_read else "📬 Новое",
		"notification_type": lambda m, a: (
			m.notification_type.label
			if m.notification_type and hasattr(m.notification_type, 'label')
			else str(m.notification_type) if m.notification_type else "—"
		),
		**BaseAdmin.column_formatters
	}

	form_excluded_columns = [] + BaseAdmin.form_excluded_columns

	form_overrides = {
		"notification_type": SelectField,
		'is_read': SelectField,
		**BaseAdmin.form_overrides
	}

	# Form arguments with choices from MediaType enum
	form_args = {
		"notification_type": {
			'choices': NotificationType.choices(),  # Use MediaType enum
			"coerce": str,
		},
		'is_read': {
			'choices': [(True, 'Да'), (False, 'Нет')],
			'coerce': lambda x: x == 'True' if isinstance(x, str) else bool(x)
		},
		**BaseAdmin.form_args
	}

	@action(
		name="mark_read",
		label="Пометить прочитанным",
		add_in_list=True,
		add_in_detail=True,
	)
	async def mark_read_action(self, request: Request):
		"""Mark notifications as read."""

		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		count = 0
		for pk in pks.split(","):
			try:
				notification = await Notification.objects.get(id=int(pk))
				if notification and not notification.is_read:
					await Notification.objects.update_by_id(int(pk), is_read=True)
					count += 1
					logger.info(f"Notification {pk} marked as read")
			except Exception as e:
				logger.error(f"Error marking notification {pk} as read: {e}")

		logger.info(f"Marked {count} notifications as read")

		return RedirectResponse(
			url=request.url_for("admin:list", identity=self.identity),
			status_code=303,
		)

	@action(
		name="send_to_messenger",
		label="Отправить в мессенджер",
		add_in_list=True,
		add_in_detail=True,
	)
	async def send_to_messenger_action(self, request: Request):
		"""Send notification to messenger (Telegram/VK)."""
		from app.services.notifications.messenger import messenger_service

		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		pk = pks.split(",")[0]
		try:
			notification = await Notification.objects.get(id=int(pk))
			if notification:
				await messenger_service.send_notification(
					title=notification.title,
					message=notification.message,
					notification_type=notification.notification_type,
					messenger="telegram",
				)
				logger.info(f"Notification {pk} sent to messenger")
		except Exception as e:
			logger.error(f"Error sending notification {pk} to messenger: {e}")

		return RedirectResponse(
			url=request.url_for("admin:list", identity=self.identity),
			status_code=303,
		)


class LLMProviderAdmin(BaseAdmin, model=LLMProvider):
	"""
	Admin for LLM Providers.

	Note: Models are managed separately in LLMModelAdmin.
	"""

	name = "Провайдер LLM"
	name_plural = "Провайдеры LLM"
	icon = "fa fa-server"

	# Column configuration - only provider info, no models
	column_list = ["id", "name", "api_url", "is_active"]
	column_searchable_list = ["name", "api_url"]
	column_sortable_list = ["name", "is_active"]

	column_labels = dict({
		"id": "ID",
		"name": "Название",
		"description": "Описание",
		"api_url": "API URL",
		"api_key_env": "Переменная окружения для API ключа",
		"config": "Дополнительные параметры (JSON)",
		"is_active": "Активен",
	}, **BaseAdmin.column_labels)

	form_excluded_columns = BaseAdmin.form_excluded_columns + [
		"models",  # Managed in LLMModelAdmin
	]

	# Form arguments
	form_args = {
		"name": {
			"label": "Название",
			"description": "Например: OpenAI, DeepSeek, SambaNova"
		},
		"api_url": {
			"label": "API URL",
			"description": "Базовый URL для API провайдера. Например: https://api.openai.com/v1"
		},
		"api_key_env": {
			"label": "Переменная окружения для API ключа",
			"description": "Название переменной окружения с API ключом. Например: OPENAI_API_KEY"
		},
		"config": {
			"label": "Дополнительные настройки",
			"description": "JSON с дополнительными параметрами провайдера (rate limits, timeout и т.д.)"
		},
		**BaseAdmin.form_args
	}

	# Column formatters
	column_formatters = {
	}

	# Custom templates with JS injection
	create_template = "llm_provider/create.html"
	edit_template = "llm_provider/edit.html"


class LLMModelAdmin(BaseAdmin, model=LLMModel):
	"""
	Admin for LLM Models.
	
	Each provider can have multiple models with different capabilities and costs.
	"""

	name = "Модель LLM"
	name_plural = "Модели LLM"
	icon = "fa fa-microchip"

	column_list = ["id", "name", "provider", "capabilities", "input_cost", "output_cost", "is_active", "is_default"]
	column_searchable_list = ["name", "description"]
	column_sortable_list = ["name", "input_cost", "output_cost", "is_active", "is_default"]

	column_labels = dict({
		"id": "ID",
		"name": "Название модели",
		"description": "Описание",
		"provider_id": "Провайдер",
		"provider": "Провайдер",
		"input_cost": "Цена входа ($/1M токенов)",
		"output_cost": "Цена выхода ($/1M токенов)",
		"capabilities": "Возможности",
		"config": "Настройки (JSON)",
		"is_active": "Активна",
		"is_default": "По умолчанию для провайдера",
	}, **BaseAdmin.column_labels)

	# Form configuration
	form_excluded_columns = BaseAdmin.form_excluded_columns + [
		"text_scenarios",
		"image_scenarios",
		"video_scenarios",
	]

	form_overrides = {
		"capabilities": SelectMultipleField,
		**BaseAdmin.form_overrides
	}

	form_args = {
		"provider_id": {
			"label": "Провайдер",
			"description": "Выберите провайдера для этой модели"
		},
		"name": {
			"label": "Название модели",
			"description": "Например: gpt-4-turbo, deepseek-chat, llama-3.1-70b"
		},
		"description": {
			"label": "Описание",
			"description": "Краткое описание модели и её особенностей"
		},
		"capabilities": {
			"label": "Возможности",
			"description": "Выберите типы контента, которые может обрабатывать модель",
			"choices": MediaType.choices(),
			"coerce": str,
		},
		"input_cost": {
			"label": "Цена входящих токенов",
			"description": "Стоимость в долларах за 1 миллион входящих токенов"
		},
		"output_cost": {
			"label": "Цена исходящих токенов",
			"description": "Стоимость в долларах за 1 миллион исходящих токенов"
		},
		"config": {
			"label": "Настройки модели",
			"description": "JSON с параметрами: temperature, max_tokens, top_p и т.д."
		},
		"is_active": {
			"label": "Активна",
			"description": "Доступна ли модель для использования"
		},
		"is_default": {
			"label": "По умолчанию",
			"description": "Использовать эту модель по умолчанию для текущего провайдера"
		},
		**BaseAdmin.form_args
	}

	# Column formatters
	column_formatters = {
		"provider": lambda m, a: m.provider.name if m.provider else "—",
		"capabilities": lambda m, a: ", ".join(m.capabilities) if m.capabilities else "—",
		"input_cost": lambda m, a: f"${m.input_cost:.2f}",
		"output_cost": lambda m, a: f"${m.output_cost:.2f}",
	}

	async def insert_model(self, request: Request, data: dict) -> Any:
		"""Ensure capabilities is always a list."""
		if "capabilities" in data:
			if not isinstance(data["capabilities"], list):
				data["capabilities"] = [data["capabilities"]] if data["capabilities"] else []
			data["capabilities"] = [c for c in data["capabilities"] if c]

		return await super().insert_model(request, data)

	async def update_model(self, request: Request, pk: Any, data: dict) -> Any:
		"""Ensure capabilities is always a list."""
		if "capabilities" in data:
			if not isinstance(data["capabilities"], list):
				data["capabilities"] = [data["capabilities"]] if data["capabilities"] else []
			data["capabilities"] = [c for c in data["capabilities"] if c]

		return await super().update_model(request, pk, data)

	async def scaffold_form(self, rules=None):
		form = await super().scaffold_form(rules)

		# Загрузка провайдеров для выпадающего списка
		providers = await LLMProvider.objects.all().order_by("name")

		# Используем правильное имя поля формы (может быть provider_id или provider)
		if hasattr(form, 'provider_id'):
			field_name = 'provider_id'
		elif hasattr(form, 'provider'):
			field_name = 'provider'
		else:
			# Если ни одно не найдено, пропускаем настройку
			return form

		getattr(form, field_name).kwargs.update({
			"data": [(str(p.id), p) for p in providers],
			"get_label": lambda obj: obj.name,
		})

		return form

	async def after_model_change(self, data: dict, model: LLMModel, is_created: bool, request=None) -> None:
		"""Ensure only one model can be default at a time."""
		await super().after_model_change(data, model, is_created, request)

		# Если модель установлена как по умолчанию
		if model.is_default:
			# Сбросить флаг is_default для всех других моделей
			objects_to_update = await LLMModel.objects.filter(provider_id=model.provider_id).exclude(id=model.id).all()
			for obj in objects_to_update:
				obj.is_default = False
				obj.save()

	@action(
		name="test-model",
		label="🧪 Тестировать модель",
		add_in_list=True,
		add_in_detail=True
	)
	async def test_model_action(self, request: Request):
		"""Test the selected LLM model."""
		pks = request.query_params.get("pks", "")

		if not pks:
			request.session["admin_message"] = {
				"type": "error",
				"message": "Не выбрана модель для тестирования"
			}
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		return await LLMModelActions.test_model(self, request, pks, self.identity)
