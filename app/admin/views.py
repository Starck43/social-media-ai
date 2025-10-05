from typing import Any

from fastapi import HTTPException
from sqladmin import action
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.models import (
	User, Role, Permission, SocialAccount, SocialGroup,
	Post, Comment, Statistics, AIAnalysisResult, Notification
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
	form_ajax_refs = {
		'role': {
			'fields': ('name', 'description'),
			'placeholder': 'Поиск по роли'
		}
	}
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


class SocialAccountAdmin(BaseAdmin, model=SocialAccount):
	name = "Соц. аккаунт"
	name_plural = "Соц. аккаунты"
	icon = "fa fa-globe"
	column_list = [
		'id', 'platform', 'username', 'is_active', 'social_groups', 'updated_at'
	]
	column_searchable_list = ['username', 'platform']
	column_sortable_list = ['username', 'is_active']
	column_labels = {
		"id": "ID",
		"platform": "Платформа",
		"platform_user_id": "ID пользователя на платформе",
		"username": "Имя пользователя",
		"is_active": "Активен",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"user": "Пользователь",
		"user_id": "ID пользователя",
		"social_groups": "Соц. группы"
	}

	form_ajax_refs = {
		'user': {
			'fields': ('username', 'email'),
			'placeholder': 'Поиск по имени или e-mail'
		}
	}


class SocialGroupAdmin(BaseAdmin, model=SocialGroup):
	name = "Соц. группа"
	name_plural = "Соц. группы"
	icon = "fa fa-users"
	column_list = [
		'id', 'platform', 'name', 'platform_user_id', 'members_count', 'is_tracking',
	]
	column_searchable_list = ['name', 'platform']
	column_sortable_list = ['members_count', 'is_tracking']
	column_labels = {
		"id": "ID",
		"platform": "Платформа",
		"platform_group_id": "ID группы на платформе",
		"name": "Название",
		"members_count": "Количество участников",
		"is_tracking": "Отслеживается",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"social_account": "Соц. аккаунт",
		"social_account_id": "ID соц. аккаунта",
		"posts": "Посты",
		"statistics": "Статистика",
		"ai_analysis_results": "Аналитика ИИ"
	}
	form_ajax_refs = {
		'social_account': {
			'fields': ('platform_user_id', 'platform'),
			'placeholder': 'Search by platform user ID or platform'
		}
	}


class PostAdmin(BaseAdmin, model=Post):
	name = "Пост"
	name_plural = "Посты"
	icon = "fa fa-file-text"
	column_list = [
		'id', 'platform', 'platform_post_id', 'content',
		'likes_count', 'comments_count', 'post_date', 'created_at'
	]
	column_searchable_list = ['platform_post_id', 'content']
	column_sortable_list = ['post_date', 'likes_count', 'comments_count']
	column_labels = {
		"id": "ID",
		"platform": "Платформа",
		"platform_post_id": "ID поста на платформе",
		"content": "Содержание",
		"likes_count": "Количество лайков",
		"comments_count": "Количество комментариев",
		"post_date": "Дата публикации",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"group": "Группа",
		"group_id": "ID группы",
		"comments": "Комментарии"
	}
	form_ajax_refs = {
		'group': {
			'fields': ('name', 'platform_group_id'),
			'placeholder': 'Search by group name or platform ID'
		}
	}


class CommentAdmin(BaseAdmin, model=Comment):
	name = "Комментарий"
	name_plural = "Комментарии"
	icon = "fa fa-comment"
	column_list = [
		'id', 'text', 'platform_comment_id', 'post', 'author',
		'likes_count', 'created_at', 'updated_at'
	]
	column_searchable_list = ['platform_comment_id', 'text']
	column_sortable_list = ['created_at', 'likes_count']
	column_labels = {
		"id": "ID",
		"platform": "Платформа",
		"platform_post_id": "ID поста на платформе",
		"content": "Содержание",
		"likes_count": "Количество лайков",
		"comments_count": "Количество комментариев",
		"post_date": "Дата публикации",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"group": "Группа",
		"group_id": "ID группы",
		"comments": "Комментарии"
	}
	form_ajax_refs = {
		'post': {
			'fields': ('platform_post_id', 'text'),
			'placeholder': 'Search by post ID or text'
		}
	}


class StatisticsAdmin(BaseAdmin, model=Statistics):
	name = "Статистика"
	name_plural = "Статистика"
	icon = "fa fa-chart-line"
	column_list = [
		'id', 'date', 'group',
		'followers_count', 'engagement_rate', 'reach', 'impressions', 'created_at'
	]
	column_searchable_list = ['group.name']
	column_sortable_list = ['date', 'engagement_rate', 'followers_count']
	column_labels = {
		"id": "ID",
		"date": "Дата",
		"followers_count": "Количество подписчиков",
		"engagement_rate": "Вовлеченность (%)",
		"reach": "Охват",
		"impressions": "Показы",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"group": "Группа",
		"group_id": "ID группы"
	}
	form_ajax_refs = {
		'group': {
			'fields': ('name', 'platform_group_id'),
			'placeholder': 'Search by group name or platform ID'
		}
	}


class AIAnalysisResultAdmin(BaseAdmin, model=AIAnalysisResult):
	name = "Анализ ИИ"
	name_plural = "Аналитика ИИ"
	icon = "fa fa-robot"
	column_list = [
		'id', 'analysis_type', 'status',
		'period_start', 'period_end', 'confidence_score', 'created_at'
	]
	column_searchable_list = ['analysis_type', 'status']
	column_sortable_list = ['created_at', 'period_start', 'confidence_score']
	column_labels = {
		"id": "ID",
		"analysis_type": "Тип анализа",
		"status": "Статус",
		"period_start": "Начало периода",
		"period_end": "Конец периода",
		"confidence_score": "Уверенность (%)",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"group": "Группа",
		"group_id": "ID группы",
		"analysis_data": "Данные анализа"
	}

	form_ajax_refs = {
		'group': {
			'fields': ('name', 'platform_group_id'),
			'placeholder': 'Search by group name or platform ID'
		}
	}


class NotificationAdmin(BaseAdmin, model=Notification):
	name = "Уведомление"
	name_plural = "Уведомления"
	icon = "fa fa-bell"
	column_list = [
		'id', 'title', 'message', 'notification_type',
		'is_read', 'user', 'created_at'
	]
	column_searchable_list = ['title', 'message', 'notification_type']
	column_sortable_list = ['created_at', 'is_read']
	column_labels = {
		"id": "ID",
		"title": "Заголовок",
		"message": "Сообщение",
		"notification_type": "Тип уведомления",
		"is_read": "Прочитано",
		"created_at": "Дата создания",
		"updated_at": "Дата обновления",
		"user": "Пользователь",
		"user_id": "ID пользователя"
	}

	form_excluded_columns = ['created_at', 'updated_at']
	form_ajax_refs = {
		'user': {
			'fields': ('username', 'email'),
			'placeholder': 'Search by username or email'
		}
	}
