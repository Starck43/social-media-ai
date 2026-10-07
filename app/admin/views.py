import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Union

from fastapi import HTTPException
from sqladmin import action
from sqladmin.fields import SelectField
from sqlalchemy import Select
from starlette.requests import Request
from starlette.responses import RedirectResponse
from wtforms import validators, widgets

from app.admin.actions import LLMModelActions
from app.admin.formatters import format_date, format_usd, plan_badge
from app.models import (
	User,
	Role,
	Permission,
	Notification,
	Platform,
	Source,
	Tenant,
	UserCredential,
	AgentScenario,
	AgentTask,
	BotAction,
	AIAnalytics,
	LLMProvider,
	LLMModel,
	CollectedItem,
)
from app.tasks.cron import cron_to_human
from app.types import (
	SourceType,
	PlatformType,
	PeriodType,
	UserRoleType,
	ActionType,
	ContentType,
	AnalysisType,
	LLMStrategyType,
	BotActionStatus,
	JobType,
	NotificationType,
)
from app.types.enums.llm_types import APIFormatType
from .base import BaseAdmin
from .widgets import EuropeanDateField, CommaSeparatedSelectMultipleField
from ..services.ai.llm_client import LLMClientFactory

logger = logging.getLogger(__name__)


class UserAdmin(BaseAdmin, model=User):
	name = "Пользователь"
	name_plural = "Пользователи"
	icon = "fa fa-user"

	# Право на смену чужого пароля — это право на изменение пользователя.
	# Ключ — slug, который sqladmin ставит на кнопку (`@action` slugify'ит
	# имя: `change_password` -> `change-password`).
	action_permissions = {"change-password": ActionType.UPDATE}

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
		"hashed_password": {"type": "password"},
		"email": {"placeholder": "user@example.com"},
		"username": {"placeholder": "username"},
	}

	form_args = {
		"username": {
			"label": "Имя пользователя",
			"description": "Логин для входа в /app и /admin",
		},
		"email": {
			"label": "Email",
			"description": "Используется для идентификации и восстановления доступа",
		},
		"hashed_password": {
			"label": "Пароль",
			"description": "Задаётся при создании пользователя; для смены используйте действие «Изменить пароль»",
		},
		"role": {
			"label": "Роль",
			"description": "Определяет набор разрешений пользователя — см. раздел «Роли»",
		},
		"is_superuser": {
			"label": "Администратор",
			"description": "Полный доступ независимо от роли",
		},
		**BaseAdmin.form_args,
	}

	async def on_model_change(
		self, data: dict, model: Any, is_created: bool, request=None
	) -> None:
		"""Perform actions before model is created/updated."""
		await super().on_model_change(data, model, is_created, request)

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

	form_overrides = {
		# SelectField override keeps the choices from form_args below (the default
		# enum converter would replace them with raw enum names)
		"codename": SelectField,
		**BaseAdmin.form_overrides,
	}

	form_args = {
		"name": {
			"label": "Название",
			"description": "Отображаемое имя роли, например «Модератор сообщества»",
		},
		"codename": {
			"label": "Кодовое наименование",
			"description": "Системный уровень роли из общего enum; определяет место в иерархии прав",
			"choices": UserRoleType.choices(),
			"coerce": str,
			"validators": [validators.AnyOf([value for value, _ in UserRoleType.choices()])],
		},
		"description": {
			"label": "Описание",
			"description": "Для справки: кто и зачем получает эту роль",
		},
		"permissions": {
			"label": "Разрешения",
			"description": "Набор прав роли; суперпользователь игнорирует этот список",
		},
		**BaseAdmin.form_args,
	}

	column_formatters_detail = {
		"codename": lambda m, a: m.codename.label if hasattr(m.codename, "label") else "—",
	}


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

	form_overrides = {
		# SelectField override keeps the choices from form_args below (the default
		# enum converter would replace them with raw enum names)
		"action_type": SelectField,
		**BaseAdmin.form_overrides,
	}

	form_args = {
		"name": {
			"label": "Название",
			"description": "Человекочитаемое описание права, например «Просмотр источников»",
		},
		"action_type": {
			"label": "Вид разрешения",
			"description": "Какое действие разрешает право; кодовое имя строится как приложение.таблица.действие",
			"choices": ActionType.choices(),
			"coerce": str,
			"validators": [validators.AnyOf([value for value, _ in ActionType.choices()])],
		},
		"description": {
			"label": "Описание",
			"description": "Для справки: что именно разрешает право",
		},
		"roles": {
			"label": "Роли",
			"description": "Роли, которым выдано это право",
		},
		**BaseAdmin.form_args,
	}

	column_formatters = {
		"action_type": lambda m, a: m.action_type.label if hasattr(m.action_type, "label") else "—",
		**BaseAdmin.column_formatters,
	}

	column_formatters_detail = {
		"action_type": lambda m, a: m.action_type.label if hasattr(m.action_type, "label") else "—",
	}


class PlatformAdmin(BaseAdmin, model=Platform):
	name = "Платформа"
	name_plural = "Платформы"
	icon = "fa fa-globe"
	column_list = ["id", "name", "platform_type", "is_active"]
	column_searchable_list = ["name"]
	column_sortable_list = ["name", "is_active"]
	column_labels = dict(
		{
			"id": "ID",
			"name": "Название",
			"platform_type": "Тип платформы",
			"is_active": "Активна",
			"base_url": "URL платформы",
			"params": "Настройки API запросов",
			"sources": "Источники",
		},
		**BaseAdmin.column_labels,
	)

	column_formatters = {
		# Show the localized label from the shared enum instead of the raw DB value
		"platform_type": lambda m, a: m.platform_type.label if m.platform_type is not None else "—",
		**BaseAdmin.column_formatters,
	}

	column_formatters_detail = {
		"platform_type": lambda m, a: m.platform_type.label if m.platform_type is not None else "—",
	}

	form_excluded_columns = ["sources"] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		"params": {"rows": 6},
	}

	form_overrides = {
		# SelectField override keeps the choices from form_args below (the default
		# enum converter would replace them with raw DB values)
		"platform_type": SelectField,
		**BaseAdmin.form_overrides,
	}

	form_args = {
		"name": {
			"label": "Название",
			"description": "Системное имя платформы (vk, telegram, max) — используется в коде, логах и кредах",
		},
		"platform_type": {
			"label": "Тип платформы",
			"description": "Определяет, какой API-клиент и какие типы источников доступны",
			"choices": PlatformType.choices(),
			"coerce": str,
			"validators": [validators.AnyOf([value for value, _ in PlatformType.choices()])],
		},
		"base_url": {
			"label": "URL платформы",
			"description": "Публичный адрес для ссылок, например https://vk.com (без завершающего слеша)",
		},
		"params": {
			"label": "Настройки API запросов",
			"description": (
				"JSON. api_base_url — базовый URL метода (пусто = значение из настроек приложения); "
				"api_version — версия API (VK); auth_type — тип авторизации. "
				'Пример: {"api_base_url": "https://api.vk.com/method", "api_version": "5.199", "auth_type": "oauth"}'
			),
		},
		**BaseAdmin.form_args,
	}


class SourceAdmin(BaseAdmin, model=Source):
	name = "Источник"
	name_plural = "Источники"
	icon = "fa fa-rss"

	# Проверка источника ставит задачу сбора и пишет в карточку источника.
	action_permissions = {"check-source": ActionType.UPDATE}
	column_list = [
		"id",
		"tenant",
		"platform",
		"name",
		"source_type",
		"external_id",
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
			"tenant": "Рабочее пространство",
			"platform": "Платформа",
			"name": "Название",
			"platform_id": "ID платформы",
			"source_type": "Тип источника",
			"external_id": "Внешний ID источника",
			"params": "Параметры",
			"last_checked": "Последняя проверка",
			"date_from": "Дата начала сбора",
			"date_to": "Дата окончания сбора",
			"analytics": "Аналитика",
		},
		**BaseAdmin.column_labels,
	)
	column_details_exclude_list = ["platform_id"]

	form_columns = [
		"tenant",
		"platform",
		"name",
		"source_type",
		"external_id",
		"params",
		"is_active",
	]
	form_widget_args = {
		"last_checked": {
			"readonly": True,
		},
	}
	form_overrides = {
		# SelectField override keeps the choices from form_args below (the default
		# enum converter would replace them with raw DB values)
		"source_type": SelectField,
		**BaseAdmin.form_overrides,
	}
	form_args = {
		"tenant": {
			"label": "Рабочее пространство",
			"description": "Владелец источника (пространство); выбирается из списка",
		},
		"platform": {
			"label": "Платформа",
			"description": "Определяет API-клиент и источник токенов для сбора",
		},
		"name": {
			"label": "Название",
			"description": "Отображаемое имя; если пусто, в списках используется внешний ID",
		},
		"source_type": {
			"label": "Тип источника",
			"description": "Личный профиль, сообщество, канал или чат — тип определяет доступные методы API",
			"choices": SourceType.choices(),
			"coerce": str,
			"validators": [validators.AnyOf([value for value, _ in SourceType.choices()])],
		},
		"external_id": {
			"label": "Внешний ID источника",
			"description": "Идентификатор на платформе: короткое имя (screen_name) или числовой id",
		},
		"params": {
			"label": "Параметры",
			"description": (
				"JSON. mode — api (Bot API, по умолчанию) | user (MTProto-сессия) | browser (план); "
				"monitored_users — список username для отслеживания; "
				"incremental_mode — собирать только новое; collection.count / collection.limit — размер "
				"страницы и лимит за прогон; collection.filter — фильтр стены VK; "
				"force_refresh и cli_dates — разовые переопределения дат"
			),
		},
		**BaseAdmin.form_args,
	}
	column_formatters = {
		# Localized labels come from the shared enums (never duplicated as string maps)
		"source_type": lambda m, a: m.source_type.label if m.source_type is not None else "—",
		**BaseAdmin.column_formatters,
	}

	column_formatters_detail = {
		"source_type": lambda m, a: m.source_type.label if m.source_type is not None else "—",
	}

	# Use custom templates for create/edit/details to inject per-view JS
	create_template = "sqladmin/source_create.html"
	edit_template = "sqladmin/source_edit.html"
	details_template = "sqladmin/source_details.html"

	def list_query(self, request: Request) -> Select:
		return Source.objects.filter().to_select()

	def details_query(self, request: Request) -> Select:
		pk = int(request.path_params["pk"])

		return (
			Source.objects.prefetch_related(
				"analytics",
				"platform",
			)
			.filter(id=pk)
			.to_select()
		)

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
				request=request,
				name="sqladmin/source_check_results_standalone.html",
				context={
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
				request=request,
				name="sqladmin/source_check_results_standalone.html",
				context={
					"request": request,
					"source": source,
					"error": error_msg,
					"error_type": error_type,
					"error_details": error_details,
					"checked_at": datetime.now(),
				}
			)


class AgentScenarioAdmin(BaseAdmin, model=AgentScenario):
	name = "Сценарий бота"
	name_plural = "Сценарии ботов"
	icon = "fa fa-robot"

	action_permissions = {
		"view-prompts": ActionType.VIEW,
		"toggle-active": ActionType.UPDATE,
	}
	column_list = ["id", "name", "description", "is_active", "max_tokens"]
	column_searchable_list = ["name", "description"]
	column_sortable_list = ["name", "is_active", "max_tokens"]
	column_labels = dict({
		"id": "ID",
		"name": "Название",
		"description": "Описание сценария",

		# Prompt fields
		"base_prompt": "Базовый промпт анализа",
		"media_overrides": "Переопределения по типам контента",
		"summary_prompt": "Промпт общего резюме",

		"analysis_types": "Типы анализа",
		"content_types": "Типы контента",
		"scope": "Дополнительные параметры",
		"sources": "Источники",
		"text_llm_model": "Модель для текста",
		"image_llm_model": "Модель для изображений",
		"video_llm_model": "Модель для видео",
		"text_llm_provider_id": "ID провайдера для текста",
		"image_llm_provider_id": "ID провайдера для изображений",
		"video_llm_provider_id": "ID провайдера для видео",
		"llm_strategy": "Стратегия выбора модели"
	}, **BaseAdmin.column_labels)

	form_excluded_columns = BaseAdmin.form_excluded_columns + [
		"sources",
		"agent_tasks",
		"llm_mapping",
		# Exclude these fields — we handle them manually in custom template
		"content_types",
		"analysis_types",
		"scope",
	]
	form_overrides = {
		'llm_strategy': SelectField,
		**BaseAdmin.form_overrides
	}
	form_args = {
		'llm_strategy': {
			'label': 'Стратегия выбора модели',
			'description': (
				'Как подбирается модель, если конкретная не задана: '
				'cost_efficient — дешевле, quality — качественнее, multimodal — с поддержкой медиа'
			),
			'choices': LLMStrategyType.choices(),
			'coerce': str,
			'validators': [validators.AnyOf([value for value, _ in LLMStrategyType.choices()])],
		},
		'description': {
			'label': 'Описание сценария',
			'description': 'Для справки: что делает сценарий и на каких источниках применяется',
		},
		'name': {
			'label': 'Название',
			'description': 'Короткое понятное имя сценария, например «Комментарии к постам VK»',
		},
		'max_tokens': {
			'label': 'Лимит токенов ответа',
			'description': 'Максимальное число токенов в ответе модели (пусто — использовать значение из настроек модели)',
		},
		'text_llm_model': {
			'label': 'Модель для текста',
			'description': 'Конкретная модель для анализа текста. Если не указана, выбирается по стратегии сценария',
		},
		'image_llm_model': {
			'label': 'Модель для изображений',
			'description': 'Конкретная модель для анализа изображений. Если не указана, выбирается по стратегии сценария',
		},
		'video_llm_model': {
			'label': 'Модель для видео',
			'description': 'Конкретная модель для анализа видео. Если не указана, выбирается по стратегии сценария',
		},
		'base_prompt': {
			'description': (
				'Базовый промпт для анализа любого контента. Оставьте пустым для дефолтного. '
				'Переменные: {text}, {platform}, {source_type}, {total_posts}, {date_range}, '
				'{source_name}, {scenario_name}, {max_keywords}, {max_topics}, {sentiment_categories}'
			)
		},
		'media_overrides': {
			'description': (
				'Переопределения промпта для отдельных типов контента: '
				'{"image": "…", "video": "…", "audio": "…"}. Оставьте пустым, чтобы для всех типов '
				'использовался базовый промпт.'
			)
		},
		'summary_prompt': {
			'description': (
				'Кастомный промпт для создания общего резюме из мультимедийного анализа. '
				'Оставьте пустым для дефолтного.'
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
		**BaseAdmin.column_formatters
	}
	form_widget_args = {
		# Prompt fields with placeholders
		"base_prompt": {
			"rows": 10,
			"placeholder": (
				"Проанализируй следующий контент из {platform}.\n\n"
				"Контент: {text}\n"
				"Период: {date_range}\n\n"
				"Определи основные темы, тональность и ключевые моменты."
			)
		},
		"media_overrides": {
			"rows": 8,
			"placeholder": (
				'{\n  "image": "Проанализируй {count} изображений из {platform}.",\n'
				'  "video": "Проанализируй {count} видео из {platform}."\n}'
			)
		},
		"summary_prompt": {
			"rows": 10,
			"placeholder": (
				"Создай единое резюме на основе следующих анализов:\n\n"
				"Текст: {text_analysis}\nИзображения: {image_analysis}\n"
				"Видео: {video_analysis}\n\nВыдели общие темы и ключевые инсайты."
			)
		},
		"description": {"rows": 2},
		"scope": {
			"rows": 8,
			"placeholder": '{\n  "event_based": true,\n  "sentiment": {\n    "categories": ["Позитивный", "Негативный", "Нейтральный"]\n  },\n  "keywords": {\n    "max_keywords": 20\n  }\n}'
		},
	}

	create_template = "sqladmin/agent_scenario_create.html"
	edit_template = "sqladmin/agent_scenario_edit.html"

	async def scaffold_form(self, rules=None):
		"""Provide enum types and presets to template."""
		from app.core.scenario_presets import get_all_presets
		from app.core.trigger_hints import SCOPE_HINTS
		from app.core.analysis_constants import ANALYSIS_TYPE_DEFAULTS

		form = await super().scaffold_form(rules)

		form.content_types_enum = list(ContentType)
		form.analysis_types_enum = list(AnalysisType)
		form.scope_hints = SCOPE_HINTS

		# Convert list of presets to dict with keys (for template iteration)
		presets_list = get_all_presets()
		form.presets = {f"preset_{i}": preset for i, preset in enumerate(presets_list)}

		# Provide analysis defaults and all types for JavaScript
		form.analysis_defaults = ANALYSIS_TYPE_DEFAULTS
		form.all_analysis_types = [at.db_value for at in AnalysisType]

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

	async def _prepare_form_data(self, request: Request, data: dict) -> None:
		"""Extract and parse excluded fields from request."""
		form_data = await request.form()

		# Add excluded fields back to data
		for field in ["content_types", "analysis_types", "scope"]:
			if field in form_data:
				data[field] = form_data.get(field)

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
		from app.services.ai.scenario_builder import ScenarioBuilder
		from pathlib import Path
		import sqladmin

		# Get scenario ID
		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		scenario_id = int(pks.split(",")[0])

		# Load scenario
		try:
			scenario = await AgentScenario.objects.get(id=scenario_id)
		except Exception:
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		# Build prompts with the shared builder core, so the admin preview is
		# byte-identical to the web wizard's — same production prompt path.
		draft = ScenarioBuilder.draft_from_scenario(scenario)
		preview = ScenarioBuilder.preview_blocks(draft)

		prompts_data = {}
		media_overrides = scenario.media_overrides or {}
		for media_value, full in preview["media_previews"].items():
			# The base prompt drives text; image/video/audio fall back to it
			# unless a `media_overrides` key replaces it.
			custom = scenario.base_prompt if media_value == "text" else media_overrides.get(media_value)
			prompts_data[media_value] = {
				"custom": custom,
				"full": full,
				"has_custom": bool(custom),
			}

		unified_prompt = PromptBuilder.get_unified_summary_prompt(
			text_analysis={},
			image_analysis={},
			video_analysis={},
			scenario=scenario
		)
		prompts_data["unified"] = {
			"custom": scenario.summary_prompt,
			"full": unified_prompt,
			"has_custom": bool(scenario.summary_prompt),
		}

		# Setup templates
		sqladmin_path = Path(sqladmin.__file__).parent
		template_dirs = [
			str(Path(__file__).parent.parent / "templates"),
			str(sqladmin_path / "templates")
		]
		templates = Jinja2Templates(directory=template_dirs)

		# Prepare scope for display
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


		return templates.TemplateResponse(
			request=request,
			name="sqladmin/scenario_prompts.html",
			context={
				"request": request,
				"scenario": scenario,
				"prompts": prompts_data,
				"scope_display": scope_display,
				"analysis_configs_json": analysis_configs_json,
				"custom_vars_json": custom_vars_json,
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
				scenario = await AgentScenario.objects.get(id=int(pk))
				if scenario:
					new_status = not scenario.is_active
					await AgentScenario.objects.update_by_id(int(pk), is_active=new_status)
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

	# Rows are written by the analyzer only — the admin may inspect, correct or
	# remove them, but never fabricate an analysis run.
	can_create = False

	# Просмотр разбора — то же право, что и просмотр карточки аналитики.
	action_permissions = {"view-analysis": ActionType.VIEW}

	# topic_chain_id and content_hash are machine keys kept out of the crowded list
	column_list = ["id", "source", "analysis_date", "period_type", "llm_model", "estimated_cost", "created_at"]
	column_searchable_list = ["source.name", "period_type", "llm_model"]
	column_sortable_list = ["analysis_date", "created_at", "period_type", "estimated_cost"]
	column_default_sort = [("analysis_date", True), ("id", True)]
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
		"content_hash": "Хэш контента",
		"prompt_text": "Промпт",
		"provider_type": "Провайдер",
		"request_tokens": "Токенов на вход",
		"response_tokens": "Токенов на выход",
		"estimated_cost": "Стоимость, $",
		"parent_analysis_id": "Родительский анализ",
		"parent": "Родительский анализ",
		"children": "Дочерние анализы",
	}, **BaseAdmin.column_labels)

	# summary_data is rendered by the custom detail template; the metrics and keys
	# below are filled in by the analyzer and must not be hand-edited.
	form_excluded_columns = [
		"summary_data",
		"content_hash",
		"topic_chain_id",
		"provider_type",
		"request_tokens",
		"response_tokens",
		"estimated_cost",
		"parent_analysis_id",
		"children",
	] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		"analysis_date": {
			"readonly": True,
		},
		"prompt_text": {"rows": 6},
	}

	form_overrides = {
		# SelectField override keeps the choices from form_args below (the default
		# enum converter would replace them with raw DB values)
		"period_type": SelectField,
		**BaseAdmin.form_overrides,
	}

	form_args = {
		# Only the operator-correctable fields are listed; the machine-written
		# metrics above are excluded from the form and shown read-only in details.
		"analysis_date": {
			"label": "Дата анализа",
			"description": "Заполняется автоматически при создании записи",
		},
		"period_type": {
			"label": "Период",
			"description": "За какой интервал агрегирован анализ: день, неделя, месяц",
			"choices": PeriodType.choices(),
			"coerce": str,
			"validators": [validators.AnyOf([value for value, _ in PeriodType.choices()])],
		},
		"main_topics": {
			"label": "Основные темы",
			"description": "Список тем, выделенных моделью; заполняется автоматически",
		},
		"llm_model": {
			"label": "Модель ИИ",
			"description": "Модель, которой выполнен анализ; влияет на тарификацию",
		},
		"prompt_text": {
			"label": "Промпт",
			"description": "Полный текст промпта, отправленного модели; для разбора расхождений",
		},
		**BaseAdmin.form_args,
	}

	column_formatters = {
		# analysis_date is a Date column — the shared formatter renders DD.MM.YYYY
		"period_type": lambda m, a: (
			m.period_type.label
			if m.period_type and hasattr(m.period_type, 'label')
			else str(m.period_type) if m.period_type else "—"
		),
		"estimated_cost": lambda m, a: format_usd(m.estimated_cost),
		**BaseAdmin.column_formatters,
	}

	column_formatters_detail = {
		"period_type": lambda m, a: (
			m.period_type.label
			if m.period_type and hasattr(m.period_type, 'label')
			else str(m.period_type) if m.period_type else "—"
		),
		"estimated_cost": lambda m, a: format_usd(m.estimated_cost),
		"content_hash": lambda m, a: (m.content_hash[:12] + "…") if m.content_hash else "—",
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

	# Обе кнопки меняют состояние уведомления (локально и в мессенджере).
	action_permissions = {
		"mark-read": ActionType.UPDATE,
		"send-to-messenger": ActionType.UPDATE,
	}
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
		"title": {
			"label": "Заголовок",
			"description": "Краткая суть уведомления, показывается в списке",
		},
		"message": {
			"label": "Сообщение",
			"description": "Полный текст уведомления для пользователя",
		},
		"notification_type": {
			"label": "Тип уведомления",
			"description": "Определяет иконку и категорию; значения берутся из общего enum",
			'choices': NotificationType.choices(),  # Use MediaType enum
			"coerce": str,
			"validators": [validators.AnyOf([value for value, _ in NotificationType.choices()])],
		},
		"related_entity_type": {
			"label": "Тип сущности",
			"description": "Тип объекта, к которому относится уведомление: source, platform, analysis",
		},
		"related_entity_id": {
			"label": "ID сущности",
			"description": "ID объекта указанного типа; служит для перехода из уведомления",
		},
		"tenant_id": {
			"label": "Рабочее пространство",
			"description": "Пусто — уведомление видно всем рабочим пространствам",
		},
		'is_read': {
			'label': "Прочитано",
			'description': "Снятая галочка означает, что уведомление ещё не просмотрено пользователем",
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
	name = "Провайдер LLM"
	name_plural = "Провайдеры LLM"
	icon = "fa fa-server"

	# Проба подключения ходит в провайдера его же ключом и тратит токены —
	# это часть настройки, а не чтение.
	action_permissions = {"test-connection": ActionType.CONFIGURE}

	column_list = ["id", "name", "api_format", "base_url", "is_default", "is_active"]
	column_searchable_list = ["name", "base_url"]
	column_sortable_list = ["name", "api_format", "is_default", "is_active"]

	def details_query(self, request: Request) -> Select:
		# `LLMModel.__str__` renders the provider name, so the related models must
		# arrive with their `provider` already loaded — the session is closed
		# before the template renders, and a lazy load there raises
		# DetachedInstanceError.
		return (
			LLMProvider.objects.select_related("models__provider").filter(id=int(request.path_params["pk"])).to_select()
		)

	column_labels = dict(
		{
			"id": "ID",
			"name": "Название",
			"description": "Описание",
			"api_format": "Формат API",
			"base_url": "Базовый URL",
			"auth_header": "Заголовок авторизации",
			"encrypted_api_key": "API ключ (зашифрован)",
			"is_active": "Активен",
			"is_default": "По умолчанию",
		},
		**BaseAdmin.column_labels,
	)

	form_excluded_columns = BaseAdmin.form_excluded_columns + ["models"]

	form_overrides = {
		"api_format": SelectField,
		**BaseAdmin.form_overrides,
	}

	form_widget_args = {
		"base_url": {"placeholder": "https://api.openai.com/v1"},
		"auth_header": {"placeholder": "Authorization: Bearer {key}"},
		"encrypted_api_key": {"type": "password", "placeholder": "sk-..."},
	}

	form_args = {
		"name": {"label": "Название", "description": "Например: OpenAI, DeepSeek, Anthropic"},
		"description": {"label": "Описание", "description": "Для справки"},
		"api_format": {
			"label": "Формат API",
			"description": "'openai' для OpenAI-совместимых, 'anthropic' для Anthropic Messages API",
			"choices": APIFormatType.choices(),
			"coerce": str,
			"validators": [validators.AnyOf(APIFormatType.values(), message="Выберите формат API из списка")],
		},
		"base_url": {
			"label": "Базовый URL",
			"description": "Например: https://api.openai.com/v1 или https://api.anthropic.com/v1",
		},
		"auth_header": {
			"label": "Заголовок",
			"description": "Оставьте пустым для Authorization: Bearer. Для кастомных: 'x-api-key: {key}'",
		},
		"encrypted_api_key": {
			"label": "API ключ",
			"description": "Ключ будет зашифрован перед сохранением в БД",
		},
		"is_default": {
			"label": "По умолчанию",
			"description": "Этот провайдер будет в приоритете при авто-выборе",
		},
		"is_active": {
			"label": "Активен",
			"description": "Отключите, чтобы исключить провайдера из маршрутизации и авто-выбора моделей",
		},
		**BaseAdmin.form_args,
	}

	@action(name="test-connection", label="🔌 Проверить подключение", add_in_list=True, add_in_detail=True)
	async def test_connection_action(self, request: Request):
		pks = request.query_params.get("pks", "")
		if not pks:
			request.session["admin_message"] = {"type": "error", "message": "Не выбран провайдер"}
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))
		provider_id = int(pks.split(",")[0])
		provider = await LLMProvider.objects.get(id=provider_id)
		models = await LLMModel.objects.select_related("provider").filter(provider_id=provider_id, is_active=True).all()
		if not models:
			request.session["admin_message"] = {"type": "error", "message": "Нет активных моделей у провайдера"}
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))
		client = LLMClientFactory.create(models[0])
		try:
			import time

			t0 = time.monotonic()
			result = await client.analyze("Say 'ok' and nothing else.", max_tokens=10)
			elapsed = time.monotonic() - t0
			content = str(result.get("parsed", {}))
			request.session["admin_message"] = {
				"type": "success",
				"message": f"✅ {provider.name} ({provider.api_format}): {elapsed:.2f}s — {content}",
			}
		except Exception as e:
			logger.error(f"Connection test for {provider.name} failed: {e}")
			request.session["admin_message"] = {"type": "error", "message": f"❌ {provider.name}: {e}"}
		return RedirectResponse(request.url_for("admin:list", identity=self.identity))

	async def on_model_change(self, data: dict, model: LLMProvider, is_created: bool, request=None) -> None:
		await super().on_model_change(data, model, is_created, request)

		if getattr(model, "is_default", False):
			# Single default provider fleet-wide: clear the flag on every
			# other row *before* this one commits. Every default-model
			# resolver ranks on provider.is_default first, so two defaults
			# fork the fleet into an id-lottery.
			qs = LLMProvider.objects.filter(is_default=True)
			if getattr(model, "id", None) is not None:
				qs = qs.exclude(id=model.id)
			await qs.update(is_default=False)

	async def after_model_change(self, data: dict, model: LLMProvider, is_created: bool, request=None) -> None:
		if model.encrypted_api_key and not model.encrypted_api_key.startswith("gAAAAA"):
			from app.utils.crypto import encrypt_secret

			model.encrypted_api_key = encrypt_secret(model.encrypted_api_key)
			await LLMProvider.objects.update_by_id(model.id, encrypted_api_key=model.encrypted_api_key)
		await super().after_model_change(data, model, is_created, request)


class LLMModelAdmin(BaseAdmin, model=LLMModel):
	name = "Модель LLM"
	name_plural = "Модели LLM"
	icon = "fa fa-microchip"

	# Как и у провайдера: реальный вызов модели по сохранённому ключу.
	action_permissions = {"test-model": ActionType.CONFIGURE}

	column_list = [
		"id",
		"name",
		"model_id",
		"provider",
		"model_type",
		"max_tokens",
		"input_cost_per_1k",
		"output_cost_per_1k",
		"is_default",
		"is_active",
	]
	column_searchable_list = ["name", "model_id", "description"]
	column_sortable_list = [
		"name",
		"model_type",
		"max_tokens",
		"input_cost_per_1k",
		"output_cost_per_1k",
		"is_default",
		"is_active",
	]

	column_labels = dict(
		{
			"id": "ID",
			"name": "Название",
			"model_id": "API модель",
			"description": "Описание",
			"provider_id": "Провайдер",
			"provider": "Провайдер",
			"model_type": "Тип модели",
			"input_cost_per_1k": "Вход $/1K",
			"output_cost_per_1k": "Выход $/1K",
			"max_tokens": "Макс. токенов",
			"default_temperature": "Температура",
			"is_active": "Активна",
			"is_default": "По умолчанию",
		},
		**BaseAdmin.column_labels,
	)

	form_excluded_columns = BaseAdmin.form_excluded_columns + ["text_scenarios", "image_scenarios", "video_scenarios"]

	form_overrides = {
		"model_type": CommaSeparatedSelectMultipleField,
		**BaseAdmin.form_overrides,
	}

	form_args = {
		"provider_id": {"label": "Провайдер", "description": "Выберите провайдера"},
		"name": {"label": "Название", "description": "Например: GPT-4 Turbo"},
		"model_id": {
			"label": "API модель",
			"description": "Идентификатор в API провайдера: gpt-4-turbo, claude-3-opus-20240229",
		},
		"description": {
			"label": "Описание",
			"description": "Для справки оператора: чем эта модель отличается от других",
		},
		"model_type": {
			"label": "Тип модели",
			"description": "Выберите возможности модели",
			"choices": [("text", "text"), ("image", "image"), ("video", "video"), ("audio", "audio")],
			"coerce": str,
			"widget": widgets.ListWidget(prefix_label=False),
			"option_widget": widgets.CheckboxInput(),
		},
		"input_cost_per_1k": {"label": "Цена входа $/1K токенов", "description": "Например: 0.0015 для GPT-3.5"},
		"output_cost_per_1k": {"label": "Цена выхода $/1K токенов", "description": "Например: 0.002"},
		"max_tokens": {"label": "Макс. токенов", "description": "Лимит контекстного окна модели"},
		"default_temperature": {
			"label": "Температура",
			"description": "Температура генерации (0.0 — максимальная точность, 1.0 — творческий ответ). По умолчанию 0.3",
		},
		"is_active": {
			"label": "Активна",
			"description": "Отключите, чтобы модель не использовалась сценариями и авто-выбором",
		},
		"is_default": {"label": "По умолчанию", "description": "Дефолтная модель этого типа (одна на тип, остальные сбросятся)"},
		"last_request_cost": {
			"label": "Стоимость последнего запроса",
			"description": "Заполняется автоматически после теста модели",
		},
		"last_request_cost_at": {
			"label": "Когда был последний запрос",
			"description": "Заполняется автоматически после теста модели",
		},
		**BaseAdmin.form_args,
	}

	column_formatters = {
		"provider": lambda m, a: m.provider.name if m.provider else "—",
		"model_type": lambda m, a: m.model_type if m.model_type else "—",
		"input_cost_per_1k": lambda m, a: f"${m.input_cost_per_1k:.4f}",
		"output_cost_per_1k": lambda m, a: f"${m.output_cost_per_1k:.4f}",
		**BaseAdmin.column_formatters,
	}

	async def scaffold_form(self, rules=None):
		form = await super().scaffold_form(rules)

		# Ensure SelectMultipleField for model_type uses proper multi-value coercion/processing
		if hasattr(form, "model_type"):
			# If the model has a comma-separated string, process_data/process_formdata needs to handle it.
			# Actually WTForms SelectMultipleField handles list of strings, but model.model_type is a string.
			pass

		providers = await LLMProvider.objects.all().order_by("name")

		if hasattr(form, "provider_id"):
			field_name = "provider_id"
		elif hasattr(form, "provider"):
			field_name = "provider"
		else:
			return form

		getattr(form, field_name).kwargs.update(
			{
				"data": [(str(p.id), p) for p in providers],
				"get_label": lambda obj: obj.name,
			}
		)

		return form

	async def on_model_change(self, data: dict, model: LLMModel, is_created: bool, request=None) -> None:
		if "model_type" in data and isinstance(data["model_type"], list):
			data["model_type"] = ",".join(data["model_type"])
		await super().on_model_change(data, model, is_created, request)

	async def after_model_change(self, data: dict, model: LLMModel, is_created: bool, request=None) -> None:
		await super().after_model_change(data, model, is_created, request)

		if model.is_default:
			# Single default per model_type fleet-wide (same rule as
			# LLMModelManager.create_model/update_model): clear the siblings
			# in one statement instead of loading and re-saving each one.
			await LLMModel.objects.filter(model_type=model.model_type, is_default=True).exclude(id=model.id).update(
				is_default=False
			)

	async def delete_model(self, request: Request, pk: Union[int, str]) -> None:
		"""Override default delete to use manager's reassignment logic.

		sqladmin passes `pk` as a string from query params — cast to int
		before forwarding to the manager which expects an integer id.
		"""
		model_id = int(pk) if isinstance(pk, str) else pk
		result = await LLMModel.objects.delete_with_default_reassignment(model_id)
		warnings = result.get("warnings", [])
		if warnings:
			msg = "; ".join(warnings)
			request.session["admin_message"] = {"type": "warning", "message": msg}
		elif result.get("new_default_id"):
			request.session["admin_message"] = {
				"type": "success",
				"message": f"Модель удалена. Default перейдёт к {result['new_default_name']} (сценариев сброшено: {result['scenarios_reset']})",
			}
		else:
			request.session["admin_message"] = {"type": "success", "message": "Модель удалена"}

	@action(name="test-model", label="🧪 Тестировать модель", add_in_list=True, add_in_detail=True)
	async def test_model_action(self, request: Request):
		pks = request.query_params.get("pks", "")

		if not pks:
			request.session["admin_message"] = {"type": "error", "message": "Не выбрана модель для тестирования"}
			return RedirectResponse(request.url_for("admin:list", identity=self.identity))

		return await LLMModelActions.test_model(self, request, pks, self.identity)



class TenantAdmin(BaseAdmin, model=Tenant):
	name = "Рабочее пространство"
	name_plural = "Рабочие пространства"
	icon = "fa fa-building"

	column_list = [
		"id",
		"name",
		"slug",
		"plan",
		"timezone",
		"is_active",
		"max_sources",
		"daily_cost_limit",
		"updated_at",
	]
	column_searchable_list = ["name", "slug"]
	column_sortable_list = ["name", "slug", "plan", "is_active"]
	column_labels = dict({
		"id": "ID",
		"name": "Название",
		"slug": "Slug",
		"plan": "Тариф",
		"timezone": "Часовой пояс",
		"is_active": "Активно",
		"daily_cost_limit": "Дневной лимит затрат, $",
		"max_sources": "Макс. источников",
		"agent_style": "Стиль агента",
	}, **BaseAdmin.column_labels)

	# The tier as a coloured pill in the list, matching /app's badge. Without it
	# the column is a bare word ("pro") that carries no sense of weight.
	column_formatters = {
		"plan": lambda m, v: plan_badge(v),
		**BaseAdmin.column_formatters,
	}
	column_formatters_detail = {
		"plan": lambda m, v: plan_badge(v),
	}

	# A free-text plan field could be saved as "Pro " or "personal" and only fail
	# at the CHECK constraint, on save, as a 500. A select cannot express that.
	form_choices = {
		"plan": [(key, Tenant.PLAN_LIMITS[key]["label"]) for key in Tenant.PLANS],
	}

	form_columns = [
		"name",
		"slug",
		"plan",
		"timezone",
		"is_active",
		"max_sources",
		"daily_cost_limit",
		"agent_style",
	]
	form_widget_args = {
		"agent_style": {"rows": 6, "placeholder": '{"tone": "friendly", "length": "short"}'},
	}

	form_args = {
		"name": {"label": "Название", "description": "Отображаемое имя рабочего пространства"},
		"slug": {"label": "Slug", "description": "Уникальный короткий идентификатор, используется в URL и заголовках"},
		"plan": {
			"label": "Тариф",
			"description": (
				"Тарифный план. Определяет лимиты, которые применяются сразу: "
				"источники, каналы, сценарии, участники, задачи, дневной бюджет LLM. "
				"Понижение тарифа не удаляет данные — но превышенные лимиты "
				"придётся сократить, чтобы продолжить добавлять."
			),
		},
		"timezone": {"label": "Часовой пояс", "description": "IANA, например Europe/Moscow"},
		"daily_cost_limit": {
			"label": "Дневной лимит затрат, $",
			"description": "Лимит расходов на LLM за сутки (в долларах США)",
		},
		"max_sources": {
			"label": "Макс. источников",
			"description": "Ограничение на число источников рабочего пространства",
		},
		"agent_style": {
			"label": "Стиль агента",
			"description": "JSON-контракт стиля ответов агента: {tone, length, language, quiet_hours}",
		},
		**BaseAdmin.form_args,
	}

	def is_accessible(self, request: Request) -> bool:
		"""Workspaces are the owner console: platform superusers only.

		A model permission would be the wrong gate even here — the list spans
		every workspace, and the owner of one of them has no business seeing or
		editing the others.
		"""
		user = self.get_admin_user(request)
		return user is not None and bool(user._is_superuser_role())


class UserCredentialAdmin(BaseAdmin, model=UserCredential):
	"""
	Personal (per-user) platform credentials: the L2 vault.

	Secrets that belong to a *person* (VK L2 `user_token`, Telegram MTProto
	`api_id`/`api_hash`/`session`), keyed by `users.id` and shared across the
	workspaces that user belongs to. The form takes the secret in plaintext and
	encrypts it before saving, so an operator never sees ciphertext; secrets are
	resolved by `app.services.social.credentials` and never displayed here.
	"""

	name = "Личные креды"
	name_plural = "Личные креды"
	icon = "fa fa-user-lock"

	column_list = ["id", "user_id", "platform", "kind", "label", "expires_at", "is_active", "updated_at"]
	column_searchable_list = ["platform", "kind", "label"]
	column_sortable_list = ["user_id", "platform", "kind", "expires_at", "is_active"]
	column_default_sort = [("updated_at", True)]
	column_details_exclude_list = ["secret_encrypted", "meta"]

	form_excluded_columns = BaseAdmin.form_excluded_columns + ["meta"]
	form_widget_args = {
		"secret_encrypted": {"type": "password", "placeholder": "Вставьте секрет / токен..."},
		"label": {"placeholder": "Например: VK OAuth (PKCE)"},
	}

	column_labels = dict(
		{
			"id": "ID",
			"user_id": "Пользователь",
			"platform": "Платформа",
			"kind": "Тип секрета",
			"label": "Метка",
			"secret_encrypted": "Секрет (зашифрован)",
			"expires_at": "Действует до",
			"meta": "Метаданные",
		},
		**BaseAdmin.column_labels,
	)

	form_args = {
		"user_id": {"label": "Пользователь", "description": "ID из таблицы users"},
		"platform": {
			"label": "Платформа",
			"description": "vk | telegram",
		},
		"kind": {
			"label": "Тип секрета",
			"description": "vk: user_token; telegram L2: api_id | api_hash | session",
		},
		"label": {"label": "Метка", "description": "Для справки, например «VK OAuth (PKCE)»"},
		"secret_encrypted": {
			"label": "Секрет",
			"description": "Вставьте токен открытым текстом — он будет зашифрован перед сохранением",
		},
		"expires_at": {"label": "Действует до", "description": "Оставьте пустым, если срок неизвестен"},
		**BaseAdmin.form_args,
	}

	async def after_model_change(self, data: dict, model: UserCredential, is_created: bool, request=None) -> None:
		if model.secret_encrypted and not model.secret_encrypted.startswith("gAAAAA"):
			from app.utils.crypto import encrypt_secret

			model.secret_encrypted = encrypt_secret(model.secret_encrypted)
			await UserCredential.objects.update_by_id(model.id, secret_encrypted=model.secret_encrypted)
		await super().after_model_change(data, model, is_created, request)


class CollectedItemAdmin(BaseAdmin, model=CollectedItem):
	name = "Собранный контент"
	name_plural = "Собранный контент"
	icon = "fa fa-database"

	column_list = [
		"id",
		"source_id",
		"platform",
		"external_id",
		"text",
		"media_type",
		"published_at",
		"created_at",
	]
	column_searchable_list = ["external_id", "platform"]
	column_sortable_list = ["published_at", "created_at", "id"]
	column_default_sort = [("created_at", True)]
	column_details_exclude_list = ["content_hash"]

	column_labels = dict({
		"id": "ID",
		"run_id": "ID запуска",
		"source_id": "Источник",
		"external_id": "Внешний ID",
		"content_hash": "Хэш контента",
		"platform": "Платформа",
		"published_at": "Дата публикации",
		"media_type": "Тип медиа",
		"text": "Текст",
		"metrics": "Метрики",
		"author": "Автор",
		"permalink": "Ссылка",
	}, **BaseAdmin.column_labels)

	form_excluded_columns = BaseAdmin.form_excluded_columns + ["content_hash"]
	form_widget_args = {
		"text": {"rows": 8},
		"metrics": {"rows": 4, "placeholder": '{"likes": 10, "comments": 3}'}
	}

	form_args = {
		"source_id": {"label": "Источник", "description": "ID источника из таблицы sources"},
		"external_id": {"label": "Внешний ID", "description": "Идентификатор на платформе"},
		"platform": {"label": "Платформа", "description": "vk, telegram и т.д."},
		"published_at": {"label": "Дата публикации", "description": "Дата публикации поста на платформе"},
		"media_type": {"label": "Тип медиа", "description": "post, photo, video и т.д."},
		"text": {"label": "Текст", "description": "Текстовое содержимое"},
		"metrics": {
			"label": "Метрики",
			"description": "JSON: likes, comments, views, shares",
		},
		"author": {
			"label": "Автор",
			"description": "JSON: id, name, screen_name, photo",
		},
		"permalink": {"label": "Ссылка", "description": "Полная ссылка на пост"},
		**BaseAdmin.form_args,
	}

	column_formatters = {
		"text": lambda m, a: (m.text[:80] + "…") if m.text else "—",
		"published_at": lambda m, a: format_date(m.published_at),
		"created_at": lambda m, a: format_date(m.created_at),
		**BaseAdmin.column_formatters,
	}

	column_formatters_detail = {
		"text": lambda m, a: (m.text[:200] + "…") if m.text else "—",
		"published_at": lambda m, a: format_date(m.published_at),
		"created_at": lambda m, a: format_date(m.created_at),
	}


class BotActionAdmin(BaseAdmin, model=BotAction):
	"""
	Ledger of bot actions, written by the analyze job and action_send.

	Read + delete in admin; only the status can be adjusted manually (e.g. to
	cancel a stale PENDING row). payload/result/confirmed_* stay untouched.
	"""

	name = "Действие бота"
	name_plural = "Действия ботов"
	icon = "fa fa-bolt"
	can_create = False
	form_columns = ["status"]

	column_list = ["id", "agent_scenario_id", "source_id", "action_type", "status", "dry_run", "created_at"]
	column_searchable_list = ["status", "action_type"]
	column_sortable_list = ["created_at", "status"]
	column_default_sort = [("created_at", True)]

	column_labels = dict(
		{
			"id": "ID",
			"agent_scenario_id": "Сценарий",
			"source_id": "Источник",
			"analytics_id": "Аналитика",
			"action_type": "Тип действия",
			"status": "Статус",
			"payload": "Данные действия",
			"result": "Результат",
			"error": "Ошибка",
			"dry_run": "Тестовый запуск (dry run)",
			"confirmed_by": "Подтверждено пользователем",
			"confirmed_at": "Время подтверждения",
			"attempts": "Попытки",
		},
		**BaseAdmin.column_labels,
	)

	form_widget_args = {
		"status": {"readonly": False},
	}

	form_overrides = {
		# SelectField override keeps the choices from form_args below (the default
		# enum converter would replace them with raw enum names)
		"status": SelectField,
		**BaseAdmin.form_overrides,
	}

	form_args = {
		"status": {
			"label": "Статус",
			"description": "Можно поправить вручную, например отменить зависшую запись PENDING",
			"choices": BotActionStatus.choices(),
			"coerce": str,
			"validators": [validators.AnyOf([value for value, _ in BotActionStatus.choices()])],
		},
		**BaseAdmin.form_args,
	}

	column_formatters = {
		"dry_run": lambda m, a: "✅ Да" if m.dry_run else "❌ Нет",
		"action_type": lambda m, a: m.action_type.label if m.action_type is not None else "—",
		"status": lambda m, a: m.status.label if m.status is not None else "—",
		**BaseAdmin.column_formatters,
	}

	column_formatters_detail = {
		"action_type": lambda m, a: m.action_type.label if m.action_type is not None else "—",
		"status": lambda m, a: m.status.label if m.status is not None else "—",
	}


class AgentTaskAdmin(BaseAdmin, model=AgentTask):
    name = "Задача"
    name_plural = "Задачи"
    icon = "fa fa-clock"

    # Запуск задачи ставит job в очередь — это изменение, а не просмотр.
    action_permissions = {"run-now": ActionType.UPDATE}

    column_list = ["id", "tenant", "name", "job_type", "cron_expr", "is_active", "next_run_at", "last_run_at", "last_status"]
    column_searchable_list = ["name", "job_type"]
    column_sortable_list = ["name", "job_type", "is_active", "next_run_at"]
    column_labels = dict({
        "id": "ID",
        "tenant": "Рабочее пространство",
        "name": "Название",
        "job_type": "Тип",
        "cron_expr": "Расписание",
        "payload": "Параметры",
        "is_active": "Активна",
        "next_run_at": "Следующий запуск",
        "last_run_at": "Последний запуск",
        "last_status": "Статус",
        "last_error": "Ошибка",
        "sources": "Источники",
        "agent_scenario": "Сценарий агента",
        "trigger_type": "Тип триггера",
        "trigger_config": "Настройки триггера",
        "action_type": "Действие после анализа",
        "rate_limit_per_hour": "Лимит действий в час",
        "cooldown_seconds": "Пауза между действиями (сек)",
        "requires_approval": "Требует подтверждения",
        "blacklist": "Чёрный список",
        "whitelist": "Белый список",
    }, **BaseAdmin.column_labels)

    form_columns = [
        "tenant", "name", "job_type", "cron_expr", "sources", "agent_scenario", "payload", "is_active",
        # Trigger/action configuration lives here, not on the scenario: it
        # describes what this run does with its own analyses.
        "trigger_type", "trigger_config", "action_type",
        "rate_limit_per_hour", "cooldown_seconds", "requires_approval", "blacklist", "whitelist",
    ]

    form_overrides = {
        "is_active": SelectField,
        "job_type": SelectField,
        **BaseAdmin.form_overrides,
    }

    form_widget_args = {
        "name": {"placeholder": "Например: hourly-collect"},
        "cron_expr": {"placeholder": "0 * * * *"},
        "payload": {"rows": 4, "placeholder": '{"period": "day"}'},
        "trigger_config": {
            "rows": 5,
            "placeholder": '{\n  "keywords": ["жалоба", "проблема"],\n  "mode": "any"\n}',
        },
    }

    form_args = {
        "tenant": {
            "label": "Рабочее пространство",
            "description": "Владелец задачи: выберите Ваше рабочее пространство из списка",
        },
        "name": {
            "label": "Название",
            "description": "Уникальное системное имя задачи, например daily-digest или hourly-collect",
        },
        "job_type": {
            "label": "Тип задачи",
            "description": "Вид операции: сбор данных, отправка дайджеста, анализ или рефлексия",
            "choices": JobType.choices(),
            "coerce": str,
        },
        "cron_expr": {
            "label": "Cron-выражение",
            "description": "Расписание из 5 полей: минута час день месяц день-недели (например, '0 * * * *')",
        },
        "sources": {
            "label": "Источники",
            "description": "Источники, над которыми работает задача. Пусто — все активные источники",
        },
        "agent_scenario": {
            "label": "Сценарий бота",
            "description": "Сценарий анализа и реакции; пусто — используется сценарий по умолчанию",
        },
        "payload": {
            "label": "Параметры",
            "description": "JSON с дополнительными параметрами задачи, например {\"period\": \"day\"}",
        },
        "is_active": {
            "label": "Активна",
            "choices": [(True, "Да"), (False, "Нет")],
            "coerce": lambda x: x == "True" if isinstance(x, str) else bool(x),
            "description": "Выключенная задача не запускается планировщиком",
        },
        **BaseAdmin.form_args,
    }

    column_formatters = {
        # next_run_at / last_run_at inherit the shared DD.MM.YYYY HH:MM formatter
        "last_status": lambda m, a: m.last_status or "—",
        "cron_expr": lambda m, a: cron_to_human(m.cron_expr),
        "sources": lambda m, a: ", ".join(s.name for s in m.sources) or "Все активные",
        "agent_scenario": lambda m, a: m.agent_scenario.name if m.agent_scenario is not None else "—",
        **BaseAdmin.column_formatters,
    }

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request=None
    ) -> None:
        """Schedule a task saved through the admin form.

        The admin form omits `next_run_at`, so a task created/edited here would
        otherwise never become due. Compute it from the cron expression so
        admin-created tasks actually fire (a @once task is scheduled ~1 min out).
        """
        # Guard: an active source-based task (collect/analyze) needs at least one
        # active source to operate on. Empty links fall back to "all active
        # sources", so the check is on the workspace, mirroring the web rule.
        from app.core.tenant_context import tenant_scope

        from app.models import Source
        from app.models.managers.agent_task_manager import AgentTaskManager

        active = data.get("is_active", getattr(model, "is_active", False))
        job_type = data.get("job_type") or getattr(model, "job_type", "") or ""
        tenant_val = data.get("tenant") or getattr(model, "tenant_id", None)
        tenant_id = tenant_val.id if hasattr(tenant_val, "id") else tenant_val
        # The form delivers the workspace as a string ("1"), and a `tenant_id`
        # compared against `sources.tenant_id` as VARCHAR fails with
        # `operator does not exist: integer = character varying`. Coerce once,
        # here, rather than trusting every caller to pass an int.
        if tenant_id is not None:
            try:
                tenant_id = int(tenant_id)
            except (TypeError, ValueError):
                tenant_id = None
        if active and AgentTaskManager.requires_sources(job_type):
            submitted = data.get("sources") or []
            submitted_ids = [int(v) if isinstance(v, (int, str)) else getattr(v, "id", None) for v in submitted]
            submitted_ids = [i for i in submitted_ids if i is not None]
            with tenant_scope(bypass=True):
                if submitted_ids:
                    has_active = await Source.objects.filter(
                        Source.id.in_(submitted_ids), tenant_id=tenant_id, is_active=True
                    ).values(Source.id).rows()
                else:
                    has_active = await Source.objects.filter(
                        tenant_id=tenant_id, is_active=True
                    ).values(Source.id).rows()
            if not has_active:
                raise ValueError(
                    "Задача типа «сбор данных»/«анализ» не может быть активной без активного источника: "
                    "выберите хотя бы один источник или снимите флаг «Активна»"
                )

        # Guard: missing target params for the scenario's analysis types.
        # Non-blocking warning: a task is still valid without them, but analysis
        # will have nothing specific to look for.
        scenario_id_val = data.get("agent_scenario") or getattr(model, "agent_scenario_id", None)
        if scenario_id_val is not None:
            scenario_pk = int(scenario_id_val) if isinstance(scenario_id_val, (int, str)) else getattr(scenario_id_val, "id", None)
            if scenario_pk and request is not None:
                with tenant_scope(bypass=True):
                    scenario = await AgentScenario.objects.filter(id=scenario_pk).first()
                if scenario and scenario.analysis_types:
                    from app.services.ai.param_registry import missing_target_params

                    task_payload = data.get("payload") or {}
                    missing = missing_target_params(scenario.analysis_types, task_payload)
                    if missing:
                        msg = "⚠️ Для сценария «" + (scenario.name or str(scenario_pk)) + "» укажите в параметрах задачи: " + ", ".join(missing)
                        if hasattr(request, "session") and request.session:
                            request.session.flash(msg, "warning")

        cron_expr = (data.get("cron_expr") or getattr(model, "cron_expr", "") or "").strip()
        if cron_expr and getattr(model, "next_run_at", None) is None:
            if cron_expr == "@once":
                data["next_run_at"] = datetime.now(timezone.utc) + timedelta(minutes=1)
            else:
                from app.models import Tenant
                from app.tasks.cron import next_run_at as compute_next
                from app.tasks.cron import resolve_tz

                # The workspace's own zone, same rule as the web form and the
                # runner; a task scheduled here in the global zone would move by
                # the offset between the two on its first fire.
                with tenant_scope(bypass=True):
                    tenant = await Tenant.objects.get(id=tenant_id) if tenant_id else None

                data["next_run_at"] = compute_next(cron_expr, resolve_tz(tenant))
        await super().on_model_change(data, model, is_created, request)

    def list_query(self, request: Request) -> Select:
        return AgentTask.objects.prefetch_related("sources", "agent_scenario").filter().to_select()

    def details_query(self, request: Request) -> Select:
        pk = int(request.path_params["pk"])
        return (
            AgentTask.objects.prefetch_related("sources", "agent_scenario")
            .filter(id=pk)
            .to_select()
        )

    @action(
        name="run_now",
        label="Выполнить сейчас",
        add_in_list=True,
        add_in_detail=True,
    )
    async def run_now_action(self, request: Request):
        """Run the selected task's job immediately; completes a @once task."""
        from app.core.tenant_context import tenant_scope
        from app.jobs.dispatcher import run_task_directly

        pks = request.query_params.get("pks", "")
        if not pks:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=303)
        task_id = int(pks.split(",")[0])

        # Operator console: read the task across workspaces (bypass), then act
        # inside the task's own workspace — the shared helper does the scoping.
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.get(id=task_id)
        if task:
            outcome = await run_task_directly(task)
            if outcome and outcome.get("status") == "failed":
                request.session["admin_message"] = {
                    "type": "error",
                    "message": f"Задача «{task.name}» не выполнена: {outcome.get('error') or '?'}",
                }
            else:
                request.session["admin_message"] = {"type": "success", "message": f"Задача «{task.name}» выполнена"}
        return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=303)
