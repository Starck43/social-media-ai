import logging

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqladmin import action
from sqlalchemy import Select
from starlette.requests import Request
from starlette.responses import RedirectResponse

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
)
from app.models.managers.base_manager import prefetch
from app.types.models import SourceType, PeriodType, ContentType, AnalysisType
from .base import BaseAdmin
from ..core.hashing import pwd_context

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
		"updated_at",
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

			# Try to fetch current source id (edit mode)
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
		"""Collect content and display in template."""
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

		# Collect content in real-time
		try:
			client = get_social_client(source.platform)
			content = await client.collect_data(
				source=source,
				content_type="posts"
			)

			return templates.TemplateResponse(
				"sqladmin/source_check_results_standalone.html",
				{
					"request": request,
					"source": source,
					"content": content[:20],  # Show first 20 items
					"total_count": len(content),
					"checked_at": datetime.now(),
					"stats": {
						"total_likes": sum(item.get("likes", 0) for item in content),
						"total_comments": sum(item.get("comments", 0) for item in content),
						"total_views": sum(item.get("views", 0) for item in content),
					}
				}
			)
		except Exception as e:
			logger.error(f"Error checking source {source_id}: {e}")
			return templates.TemplateResponse(
				"sqladmin/source_check_results_standalone.html",
				{
					"request": request,
					"source": source,
					"error": str(e),
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
		"ai_prompt": "Промт для ИИ агента",
		"action_type": "Действие после анализа",
		"analysis_types": "Типы анализа",
		"content_types": "Типы контента",
		"scope": "Дополнительные параметры",
		"cooldown_minutes": "Интервал (минуты)",
		"sources": "Источники",
	}, **BaseAdmin.column_labels)

	form_excluded_columns = ["sources",] + BaseAdmin.form_excluded_columns
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
		"llm_model": "LLM Модель",
	}, **BaseAdmin.column_labels)

	form_excluded_columns = ["summary_data"] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		"analysis_date": {
			"readonly": True,
		},
	}

	column_formatters = {
		"analysis_date": lambda m, a: m.analysis_date.strftime("%d.%m.%Y %H:%M") if hasattr(m, 'analysis_date') else "",
		"period_type": lambda m, a: PeriodType[m.period_type].value if hasattr(m, 'period_type') and m.period_type else "",
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
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
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
		import logging

		logger = logging.getLogger(__name__)

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
		import logging
		from app.services.notifications.messenger import messenger_service

		logger = logging.getLogger(__name__)

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
