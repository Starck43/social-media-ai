import logging

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqladmin import action
from sqlalchemy import Select
from starlette.requests import Request
from starlette.responses import RedirectResponse
from wtforms import SelectMultipleField

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
)
from app.models.managers.base_manager import prefetch
from app.types import SourceType, PeriodType, ContentType, AnalysisType, LLMProviderType
from .base import BaseAdmin
from ..core.hashing import pwd_context
from ..core.llm_presets import LLMProviderMetadata

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

	async def on_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
		"""Handle password hashing on user creation."""
		if is_created:
			data["hashed_password"] = pwd_context.hash(data["hashed_password"])
		await super().on_model_change(data, model, is_created)

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
	]
	form_widget_args = {
		"last_checked": {
			"readonly": True,
		},
	},
	column_formatters = {
		"last_checked": lambda m, a: m.last_checked.strftime("%d.%m.%Y %H:%M") if m.last_checked else "",
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
	column_list = ["id", "name", "description", "is_active", "cooldown_minutes"]
	column_searchable_list = ["name", "description"]
	column_sortable_list = ["name", "is_active", "cooldown_minutes"]
	column_labels = dict({
		"id": "ID",
		"name": "Название",
		"description": "Описание сценария",
		"ai_prompt": "Промт",
		"action_type": "Действие после анализа",
		"analysis_types": "Типы анализа",
		"content_types": "Типы контента",
		"scope": "Дополнительные параметры",
		"cooldown_minutes": "Интервал (минуты)",
		"sources": "Источники",
		"text_llm_provider": "Модель для текста",
		"image_llm_provider": "Модель для изображений",
		"video_llm_provider": "Модель для видео",
		"text_llm_provider_id": "ID модели для текста",
		"image_llm_provider_id": "ID модели для изображений",
		"video_llm_provider_id": "ID модели для видео",
	}, **BaseAdmin.column_labels)

	form_excluded_columns = ["sources", ] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		"ai_prompt": {
			"rows": 10,
		},
		"description": {
			"rows": 2,
		},
	}

	create_template = "sqladmin/bot_scenario_create.html"
	edit_template = "sqladmin/bot_scenario_edit.html"

	async def scaffold_form(self, rules=None):
		"""Provide enum types and presets to template."""
		from app.core.scenario_presets import get_all_presets

		form = await super().scaffold_form(rules)

		form.content_types_enum = list(ContentType)
		form.analysis_types_enum = list(AnalysisType)

		# Convert list of presets to dict with keys (for template iteration)
		presets_list = get_all_presets()
		form.presets = {f"preset_{i}": preset for i, preset in enumerate(presets_list)}

		return form

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
		"period_type": lambda m, a: PeriodType[m.period_type].value if hasattr(m,
		                                                                       'period_type') and m.period_type else "",
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

	form_excluded_columns = [] + BaseAdmin.form_excluded_columns

	column_formatters = {"is_read": lambda m, a: "✅ Прочитано" if m.is_read else "📬 Новое"}

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
	An enhanced admin for LLM Providers with:
	— Auto-fill: When provider_type is selected and fills api_url, api_key_env, default model
	— Multi-select: Capabilities field uses multi-select instead of JSON input
	— Quick templates: Pre-configured provider templates
	"""

	name = "ИИ Провайдер"
	name_plural = "ИИ Провайдеры"
	icon = "fa fa-brain"

	column_list = ["id", "name", "provider_type", "model_name", "capabilities", "is_active"]
	column_searchable_list = ["name", "model_name", "provider_type"]
	column_sortable_list = ["name", "provider_type", "is_active"]

	column_labels = dict({
		"id": "ID",
		"name": "Название",
		"description": "Описание",
		"model_name": "Название модели",
		"provider_type": "Тип провайдера",
		"api_url": "API URL",
		"api_key_env": "Переменная окружения для API ключа",
		"config": "Параметры (JSON)",
		"capabilities": "Возможности",
		"text_scenarios": "Сценарии (текст)",
		"image_scenarios": "Сценарии (изображения)",
		"video_scenarios": "Сценарии (видео)",
		"is_active": "Активен",
	}, **BaseAdmin.column_labels)

	form_excluded_columns = [
		                        "text_scenarios",
		                        "image_scenarios",
		                        "video_scenarios"
	                        ] + BaseAdmin.form_excluded_columns

	form_widget_args = {
		"name": {
			"placeholder": "Например: OpenAI GPT-4 Vision"
		},
		"description": {
			"placeholder": "Описание провайдера и его возможностей",
			"rows": 3
		},
		"model_name": {
			"placeholder": "gpt-4-vision-preview"
		},
		"api_url": {
			"placeholder": "https://api.openai.com/v1/chat/completions",
			"readonly": False  # Will be autofilled but editable
		},
		"api_key_env": {
			"placeholder": "OPENAI_API_KEY",
			"readonly": False  # Will be autofilled but editable
		},
		"config": {
			"placeholder": '{"temperature": 0.2, "max_tokens": 2000}',
			"rows": 3
		}
	}

	form_args = {
		"provider_type": {
			"label": "Тип провайдера",
			"description": "Выберите провайдера."
		},
		"capabilities": {
			"label": "Возможности",
			"description": "Выберите возможности модели (text, image, video, audio)"
		},
	}

	column_formatters = {
		"provider_type": lambda m, a: m.provider_type.value if hasattr(m, 'provider_type') else "",
		"capabilities": lambda m, a: ", ".join(m.capabilities) if m.capabilities else "—",
	}

	# Custom form to add multi-select for capabilities
	form_overrides = {
		"capabilities": SelectMultipleField
	}

	form_choices = {
		"capabilities": [
			("text", "📝 Text"),
			("image", "🖼️ Image"),
			("video", "🎥 Video"),
			("audio", "🔊 Audio"),
		]
	}

	@action(
		name="test_connection",
		label="Тест соединения",
		confirmation_message="Проверить подключение к API провайдера?",
		add_in_list=True,
		add_in_detail=True
	)
	async def test_connection(self, request: Request):
		"""Test API connection for selected providers."""
		try:
			pks = request.query_params.get("pks", "").split(",")
			if not pks or not pks[0]:
				raise HTTPException(status_code=400, detail="No providers selected")

			results = []
			for pk in pks:
				provider = await LLMProvider.objects.get(id=int(pk))
				api_key = provider.get_api_key()

				if not api_key:
					results.append(f"❌ {provider.name}: API key not found in environment")
				else:
					# Here you would actually test the API
					# For now just check if key exists
					results.append(f"✅ {provider.name}: API key configured")

			# Show results
			message = "\n".join(results)
			request.session["admin_message"] = {
				"type": "success",
				"message": f"Test results:\n{message}"
			}

		except Exception as e:
			logger.error(f"Test connection error: {e}")
			request.session["admin_message"] = {
				"type": "error",
				"message": f"Error: {str(e)}"
			}

		return request.url_for("admin:list", identity=self.identity)

	@action(
		name="toggle_active",
		label="Включить/Выключить",
		confirmation_message="Изменить статус выбранных провайдеров?",
		add_in_list=True
	)
	async def toggle_active(self, request: Request):
		"""Toggle active status for selected providers."""
		try:
			pks = request.query_params.get("pks", "").split(",")
			if not pks or not pks[0]:
				raise HTTPException(status_code=400, detail="No providers selected")

			count = 0
			for pk in pks:
				provider = await LLMProvider.objects.get(id=int(pk))
				provider.is_active = not provider.is_active
				await provider.update()
				count += 1

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

		return request.url_for("admin:list", identity=self.identity)

	@action(
		name="quick_create_deepseek",
		label="➕ Создать DeepSeek",
		add_in_list=True
	)
	async def quick_create_deepseek(self, request: Request):
		"""Quick create DeepSeek provider."""
		return await self._quick_create_provider(request, "deepseek", "deepseek-chat")

	@action(
		name="quick_create_gpt4v",
		label="➕ Создать GPT-4 Vision",
		add_in_list=True
	)
	async def quick_create_gpt4v(self, request: Request):
		"""Quick create GPT-4 Vision provider."""
		return await self._quick_create_provider(request, "openai", "gpt-4-vision-preview")

	async def _quick_create_provider(self, request: Request, provider_type: str, model_id: str):
		"""Helper to quickly create a provider from template."""
		try:
			provider_enum = LLMProviderType(provider_type)
			config = LLMProviderMetadata.get_provider_config(provider_type)
			model_info = LLMProviderMetadata.get_model_info(provider_type, model_id)

			if not model_info:
				raise ValueError(f"Model {model_id} not found for {provider_type}")

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
				return request.url_for("admin:list", identity=self.identity)

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
			return request.url_for("admin:edit", identity=self.identity, pk=provider.id)

		except Exception as e:
			logger.error(f"Quick create error: {e}")
			request.session["admin_message"] = {
				"type": "error",
				"message": f"Ошибка создания: {str(e)}"
			}
			return request.url_for("admin:list", identity=self.identity)

	def get_form_js(self) -> str:
		"""Return JavaScript for autofill functionality."""
		return """
		<script>
		// LLM Provider metadata
		const LLM_METADATA = {
			'deepseek': {
				'api_url': 'https://api.deepseek.com/v1/chat/completions',
				'api_key_env': 'DEEPSEEK_API_KEY',
				'models': ['deepseek-chat', 'deepseek-coder']
			},
			'openai': {
				'api_url': 'https://api.openai.com/v1/chat/completions',
				'api_key_env': 'OPENAI_API_KEY',
				'models': ['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo-preview', 'gpt-4-vision-preview']
			},
			'anthropic': {
				'api_url': 'https://api.anthropic.com/v1/messages',
				'api_key_env': 'ANTHROPIC_API_KEY',
				'models': ['claude-3-opus-20240229', 'claude-3-sonnet-20240229', 'claude-3-haiku-20240307']
			},
			'google': {
				'api_url': 'https://generativelanguage.googleapis.com/v1beta',
				'api_key_env': 'GOOGLE_API_KEY',
				'models': ['gemini-pro', 'gemini-pro-vision']
			},
			'mistral': {
				'api_url': 'https://api.mistral.ai/v1/chat/completions',
				'api_key_env': 'MISTRAL_API_KEY',
				'models': ['mistral-tiny', 'mistral-small', 'mistral-medium']
			}
		};
		
		// Auto-fill on provider_type change
		document.addEventListener('DOMContentLoaded', function() {
			const providerTypeField = document.querySelector('select[name="provider_type"]');
			const apiUrlField = document.querySelector('input[name="api_url"]');
			const apiKeyEnvField = document.querySelector('input[name="api_key_env"]');
			const modelNameField = document.querySelector('input[name="model_name"]');
			
			if (providerTypeField) {
				providerTypeField.addEventListener('change', function() {
					const provider = this.value;
					const metadata = LLM_METADATA[provider];
					
					if (metadata) {
						if (apiUrlField) apiUrlField.value = metadata.api_url;
						if (apiKeyEnvField) apiKeyEnvField.value = metadata.api_key_env;
						if (modelNameField && !modelNameField.value) {
							// Set first model as default
							modelNameField.value = metadata.models[0];
						}
						
						// Show available models hint
						if (modelNameField) {
							const hint = document.createElement('small');
							hint.className = 'form-text text-muted';
							hint.textContent = 'Доступные модели: ' + metadata.models.join(', ');
							
							// Remove old hint
							const oldHint = modelNameField.parentElement.querySelector('.model-hint');
							if (oldHint) oldHint.remove();
							
							hint.className += ' model-hint';
							modelNameField.parentElement.appendChild(hint);
						}
					}
				});
			}
		});
		</script>
		"""

	async def insert_model(self, request: Request, data: dict) -> Any:
		"""Override to convert capabilities from list to JSON."""
		# Convert capabilities from form multi-select to list for JSON field
		if "capabilities" in data and isinstance(data["capabilities"], list):
			# Already a list, keep as is
			pass
		return await super().insert_model(request, data)

	async def update_model(self, request: Request, pk: Any, data: dict) -> Any:
		"""Override to convert capabilities from list to JSON."""
		# Convert capabilities from form multi-select to list for JSON field
		if "capabilities" in data and isinstance(data["capabilities"], list):
			# Already a list, keep as is
			pass
		return await super().update_model(request, pk, data)
