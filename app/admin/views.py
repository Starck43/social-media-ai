from typing import Any

from fastapi import HTTPException
from sqladmin import action
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.models import (
	User, Role, Permission, Notification, Platform, Source, SourceUserRelationship, BotScenario, AIAnalytics
)
from .base import BaseAdmin
from ..core.hashing import pwd_context


class UserAdmin(BaseAdmin, model=User):
	name = "Пользователь"
	name_plural = "Пользователи"
	icon = "fa fa-user"

	column_list = ["id", "username", "email", "is_active", "role", "updated_at"]
	column_labels = {
		"id": "ID",
		"username": "Имя пользователя",
		"email": "Email",
		"is_active": "Активен",
		"role": "Роль",
		"hashed_password": "Пароль",
		"is_superuser": "Администратор",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
	}
	column_searchable_list = ["username", "email"]
	column_sortable_list = ["is_active", "username"]
	column_default_sort = [("updated_at", True)]
	column_details_exclude_list = [
		"social_accounts",
		"notifications",
		"role_id",
	]
	form_excluded_columns = [
								"role_id",
								"hashed_password",
								"social_accounts",
								"notifications",
							] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		'password': {
			'type': 'password'
		}
	}

	async def on_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
		"""Handle password hashing on user creation/update."""
		if 'password' in data and data['password']:
			model.hashed_password = pwd_context.hash(data['password'])
		await super().on_model_change(data, model, is_created)

	async def insert_model(self, request, data: dict) -> Any:
		"""Ensure a password is set on user creation."""
		if 'password' not in data or not data['password']:
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
			return RedirectResponse(
				request.url_for("admin:list", identity=self.identity)
			)

		user_id = pks.split(",")[0]
		return RedirectResponse(
			url=f"/admin/user/change-password/{user_id}",
			status_code=303,
		)


class RoleAdmin(BaseAdmin, model=Role):
	name = "Роль"
	name_plural = "Роли"
	icon = "fa fa-shield"
	column_list = ['id', 'name', 'description']
	column_searchable_list = ['name']
	column_labels = {
		"id": "ID",
		"name": "Название",
		"codename": "Кодовое наименование",
		"description": "Описание",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"users": "Пользователи",
		"permissions": "Разрешения"
	}


class PermissionAdmin(BaseAdmin, model=Permission):
	name = "Разрешение"
	name_plural = "Разрешения"
	icon = "fa fa-key"
	column_list = ['id', 'codename', 'name', 'description']
	column_searchable_list = ['codename', 'name']
	column_sortable_list = ['codename']
	column_labels = {
		"id": "ID",
		"model_type": "Приложение.Модель",
		"action_type": "Вид разрешения",
		"codename": "Кодовое имя",
		"name": "Название",
		"description": "Описание",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"roles": "Роли"
	}

	form_excluded_columns = [
								'model_type_id',
								'codename'
							] + BaseAdmin.form_excluded_columns


class PlatformAdmin(BaseAdmin, model=Platform):
	name = "Платформа"
	name_plural = "Платформы"
	icon = "fa fa-globe"
	column_list = [
		'id', 'name', 'platform_type', 'is_active',
		'rate_limit_remaining', 'last_sync', 'updated_at'
	]
	column_searchable_list = ['name']
	column_sortable_list = ['name', 'is_active', 'last_sync']
	column_labels = {
		"id": "ID",
		"name": "Название",
		"platform_type": "Тип платформы",
		"is_active": "Активна",
		"rate_limit_remaining": "Остаток лимитов",
		"rate_limit_reset_at": "Сброс лимитов",
		"last_sync": "Последняя синхронизация",
		"sources": "Источники"
	}
	form_widget_args = {
		"rate_limit_remaining": {
			"readonly": True,
		},
		"rate_limit_reset_at": {
			"readonly": True,
		},
		"last_sync": {
			"readonly": True,
		},
	}
	form_excluded_columns = [
								'sources'
							] + BaseAdmin.form_excluded_columns

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
			return RedirectResponse(
				request.url_for("admin:list", identity=self.identity)
			)

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
		'id', 'platform', 'name', 'source_type', 'external_id',
		'is_active', 'last_checked', 'updated_at'
	]
	column_searchable_list = ['name', 'external_id']
	column_sortable_list = ['name', 'is_active', 'last_checked']
	column_labels = {
		"id": "ID",
		"platform": "Платформа",
		"name": "Название",
		"platform_id": "ID платформы",
		"source_type": "Тип источника",
		"external_id": "Внешний ID источника",
		"params": "Параметры",
		"is_active": "Активен",
		"last_checked": "Последняя проверка",
		"analytics": "Аналитика",
		"monitored_users": "Отслеживаемые пользователи",
		"tracked_in_sources": "Отслеживается в источниках"
	}

	form_widget_args = {
		"last_checked": {
			"readonly": True,
		},
	}
	form_excluded_columns = [
								'analytics',
								'monitored_users',
								'tracked_in_sources'
							] + BaseAdmin.form_excluded_columns

	@action(
		name="check_source",
		label="Проверить сейчас",
		add_in_list=True,
		add_in_detail=True,
	)
	async def check_source_action(self, request: Request):
		"""Trigger immediate source check."""
		pks = request.query_params.get("pks", "")
		if not pks:
			return RedirectResponse(
				request.url_for("admin:list", identity=self.identity)
			)

		# TODO: Implement immediate source check logic
		return RedirectResponse(
			url=request.url_for("admin:list", identity=self.identity),
			status_code=303,
		)


class SourceUserRelationshipAdmin(BaseAdmin, model=SourceUserRelationship):
	name = "Отслеживание пользователя"
	name_plural = "Отслеживание пользователей"
	icon = "fa fa-user-plus"
	column_list = [
		'source_id', 'user_id', 'is_active', 'created_at'
	]
	column_labels = {
		"source_id": "ID источника",
		"user_id": "ID пользователя",
		"params": "Параметры отслеживания",
		"is_active": "Активно",
	}
	column_searchable_list = ['source_id', 'user_id']
	column_sortable_list = ['is_active', 'created_at']

	form_excluded_columns = BaseAdmin.form_excluded_columns


class BotScenarioAdmin(BaseAdmin, model=BotScenario):
	name = "Сценарий бота"
	name_plural = "Сценарии ботов"
	icon = "fa fa-robot"
	column_list = [
		'id', 'name', 'is_active', 'cooldown_minutes', 'created_at'
	]
	column_searchable_list = ['name']
	column_sortable_list = ['name', 'is_active', 'cooldown_minutes']
	column_labels = {
		"id": "ID",
		"name": "Название",
		"trigger_conditions": "Условия срабатывания",
		"ai_prompt": "Промт ИИ",
		"is_active": "Активен",
		"cooldown_minutes": "Оставшиеся минуты",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления"
	}

	form_excluded_columns = BaseAdmin.form_excluded_columns
	form_widget_args = {
		'cooldown_minutes': {
			"readonly": True,
		}
	}

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
			return RedirectResponse(
				request.url_for("admin:list", identity=self.identity)
			)

		# TODO: Implement toggle active logic
		return RedirectResponse(
			url=request.url_for("admin:list", identity=self.identity),
			status_code=303,
		)


class AIAnalyticsAdmin(BaseAdmin, model=AIAnalytics):
	name = "AI Аналитика"
	name_plural = "AI Аналитика"
	icon = "fa fa-chart-bar"
	column_list = [
		'id', 'source', 'analysis_date', 'period_type',
		'created_at'
	]
	column_searchable_list = ['source.name', 'period_type']
	column_sortable_list = ['analysis_date', 'created_at']
	column_labels = {
		"id": "ID",
		"source": "Источник",
		"source_id": "ID источника",
		"analysis_date": "Дата анализа",
		"period_type": "Тип периода",
		"summary_data": "Данные анализа",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления"
	}
	form_excluded_columns = [
								'summary_data'
							] + BaseAdmin.form_excluded_columns
	form_widget_args = {
		"analysis_date": {
			"readonly": True,
		},
	}

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
			return RedirectResponse(
				request.url_for("admin:list", identity=self.identity)
			)

		analysis_id = pks.split(",")[0]
		return RedirectResponse(
			url=f"/admin/ai-analytics/detail/{analysis_id}",
			status_code=303,
		)


class NotificationAdmin(BaseAdmin, model=Notification):
	name = "Уведомление"
	name_plural = "Уведомления"
	icon = "fa fa-bell"
	column_list = [
		'id', 'title', 'notification_type', 'is_read', 'created_at'
	]
	column_searchable_list = ['title', 'notification_type']
	column_sortable_list = ['created_at', 'is_read']
	column_labels = {
		"id": "ID",
		"title": "Заголовок",
		"message": "Сообщение",
		"notification_type": "Тип уведомления",
		"is_read": "Прочитано",
		"related_entity_type": "Тип сущности",
		"related_entity_id": "ID сущности",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
	}

	form_excluded_columns = ['created_at', 'updated_at'] + BaseAdmin.form_excluded_columns

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
			return RedirectResponse(
				request.url_for("admin:list", identity=self.identity)
			)

		# TODO: Implement mark as read logic
		return RedirectResponse(
			url=request.url_for("admin:list", identity=self.identity),
			status_code=303,
		)
