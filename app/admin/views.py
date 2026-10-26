import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Union

from fastapi import HTTPException
from sqladmin import action
from sqladmin.fields import SelectField
from sqladmin.forms import get_model_form
from sqlalchemy import Select
from starlette.requests import Request
from starlette.responses import RedirectResponse
from wtforms import FloatField, Form, validators, widgets

from app.admin.actions import LLMModelActions
from app.admin.formatters import format_date, format_usd, model_price, plan_badge
from app.core.hashing import get_password_hash
from app.core.personal_catalog_context import catalog_execution_scope
from app.core.provider_uid import slugify_uid
from app.models import (
    AgentScenario,
    AgentTask,
    AIAnalytics,
    BotAction,
    CollectedItem,
    LLMModel,
    LLMProvider,
    Notification,
    Permission,
    Platform,
    PlatformRole,
    Source,
    Tenant,
    User,
    UserCredential,
)
from app.models.managers.user_manager import UserManager
from app.tasks.cron import cron_to_human
from app.types import (
    ActionType,
    AnalysisType,
    BotActionStatus,
    JobType,
    LLMStrategyType,
    MediaType,
    NotificationType,
    PeriodType,
    PlatformRoleType,
    PlatformType,
    SourceType,
)
from app.types.enums.llm_types import APIFormatType

from ..services.ai.llm_client import LLMClientFactory
from .base import BaseAdmin
from .catalog import PermissionLabelAdmin, PlatformRoleGrantAdmin
from .filtered_select import FilteredSelectMultipleField
from .price_fields import ExactModelPriceField
from .role_picker import RoleChoiceField, RolePickerConverter, role_choices, role_edit
from .widgets import CommaSeparatedSelectMultipleField, EuropeanDateField

logger = logging.getLogger(__name__)


class UserAdmin(BaseAdmin, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa fa-user"

    # Право на смену чужого пароля — это право на изменение пользователя.
    # Ключ — slug, который sqladmin ставит на кнопку (`@action` slugify'ит
    # имя: `change_password` -> `change-password`).
    action_permissions = {"change-password": ActionType.UPDATE}

    inline_columns = ["id", "username", "email", "is_active"]
    column_list = ["id", "username", "email", "is_active", "role", "updated_at"]
    column_labels = dict(
        {
            "id": "ID",
            "username": "Имя пользователя",
            "email": "Электронная почта",
            "hashed_password": "Пароль",
            "role": "Платформенная роль",
        },
        **BaseAdmin.column_labels,
    )
    column_searchable_list = ["username", "email"]
    column_sortable_list = ["is_active", "username"]
    column_default_sort = [("updated_at", True)]
    column_details_exclude_list = [
        "social_accounts",
        "notifications",
        "role_id",
        "hashed_password",
    ]
    form_create_rules = ["username", "email", "hashed_password", "is_active"]
    # The role select is edit-only: new accounts start at `user` (see below).
    form_edit_rules = ["username", "email", "is_active", "role"]
    form_overrides = {**BaseAdmin.form_overrides, "role": RoleChoiceField}
    # The role select is the only field with a fixed, actor-dependent choice
    # list; `role_picker.RolePickerConverter` reads it from `form_converter`.
    form_converter = RolePickerConverter
    form_excluded_columns = ["role_id"] + BaseAdmin.form_excluded_columns

    form_widget_args = {
        "hashed_password": {"type": "password"},
        "email": {"placeholder": "user@example.com"},
        "username": {"placeholder": "ivan_petrov"},
    }

    form_args = {
        "username": {
            "label": "Имя пользователя",
            "description": "Логин для входа в личный кабинет",
        },
        "email": {
            "label": "Электронная почта",
            "description": "Используется для идентификации и восстановления доступа",
        },
        "hashed_password": {
            "label": "Пароль",
            "description": "Задаётся при создании пользователя; для смены используйте действие «Изменить пароль»",
        },
        **BaseAdmin.form_args,
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
        """New accounts always start with the ordinary platform role."""
        if is_created and not getattr(model, "role_id", None):
            role = await PlatformRole.objects.get_by_codename(PlatformRoleType.USER)
            if role is None:
                raise HTTPException(status_code=500, detail="Platform role catalog is not configured")
            model.role_id = role.id
            data["role_id"] = role.id
        if is_created and data.get("hashed_password"):
            data["hashed_password"] = get_password_hash(data["hashed_password"])
            model.hashed_password = data["hashed_password"]
        await super().on_model_change(data, model, is_created, request)

    async def insert_model(self, request, data: dict) -> Any:
        """Ensure a password is set on user creation."""
        if "hashed_password" not in data or not data["hashed_password"]:
            raise HTTPException(status_code=400, detail="Требуется заполнить поле 'Пароль'")
        return await super().insert_model(request, data)

    async def update_model(self, request: Request, pk: str, data: dict) -> Any:
        """Write identity fields generically; the role goes through its manager.

        The manager is the single place that knows the assignment rules (rank,
        self-edit, the owner account), so the console must not write `role_id`
        itself, even with a forged payload.

        The owner's account is out of reach for anyone else: the role picker
        renders it read-only, but a hand-crafted POST never touches that form.
        The owner keeps the right to change their own name, email and password.
        """
        # sqladmin hands `pk` over as a string from the URL while the column is
        # an integer: asyncpg refuses `integer = varchar`, so cast before the read.
        target = await User.objects.prefetch_related("role").get(id=int(pk))
        actor = self.get_admin_user(request)
        if UserManager._is_platform_owner(target) and (actor is None or actor.id != target.id):
            raise HTTPException(status_code=403, detail="Нельзя изменять аккаунт собственника платформы")
        role_name = data.pop("role", None)
        if data.get("role_id"):
            raise HTTPException(status_code=400, detail="Используйте отдельную операцию назначения платформенной роли")
        if role_name:
            await User.objects.set_platform_role(
                user_id=int(pk),
                role_code=PlatformRoleType(str(getattr(role_name, "codename", role_name))),
                actor_id=actor.id,
            )
        return await super().update_model(request, pk, data)

    async def scaffold_form(self, rules: list[str] | None = None):
        """Build the edit form with the choices the signed-in actor may assign.

        sqladmin passes no `request` here, so the actor comes from the request
        the authorization backend bound for this task. That binding is per
        request (`load()` runs before the view renders), so one operator's rank
        never rides along on a class attribute shared by every request.

        With nothing bound the choices are empty and the select is disabled:
        fail closed, never "every role".

        The edit form also knows *whom* it edits: the actor's own account and the
        platform owner's account render the role read-only (`role_edit`), because
        `UserManager.set_platform_role` refuses those writes.
        """
        from app.admin.authorization import bound_console_request

        request = bound_console_request()
        actor = self.get_admin_user(request) if request is not None else None
        choices, locked = role_choices(actor), None
        if request is not None and (pk := request.path_params.get("pk")) is not None:
            current = await User.objects.prefetch_related("role").get(id=int(pk))
            if current is not None:
                choices, locked = role_edit(actor, current)
        converter = type("_RolePicker", (RolePickerConverter,), {"choices": choices, "locked_reason": locked})
        form = await get_model_form(
            model=self.model,
            session_maker=self.session_maker,
            only=self._form_prop_names,
            column_labels=self._column_labels,
            form_args=self.form_args,
            form_widget_args=self.form_widget_args,
            form_class=self.form_base_class,
            form_overrides=self._form_overrides,
            form_ajax_refs=self._form_ajax_refs,
            form_include_pk=self.form_include_pk,
            form_converter=converter,
        )
        if rules is not None and "role" not in rules and hasattr(form, "role"):
            delattr(form, "role")
        return form

    async def delete_model(self, request: Request, pk: Any) -> bool:
        """The platform-owner account is never deleted from the console."""
        # sqladmin hands `pk` over as a string from the URL; the column is an
        # integer, and asyncpg refuses `integer = varchar` instead of casting.
        target = await User.objects.prefetch_related("role").get(id=int(pk))
        if UserManager._is_platform_owner(target):
            raise HTTPException(status_code=403, detail="Нельзя удалить аккаунт собственника платформы")
        return await super().delete_model(request, pk)

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


class RoleAdmin(PlatformRoleGrantAdmin, model=PlatformRole):
    name = "Платформенная роль"
    name_plural = "Роли"
    icon = "fa fa-shield"
    inline_columns = ["id", "codename", "description"]
    column_list = ["id", "codename", "description"]
    column_searchable_list = ["codename"]
    column_labels = dict(
        {
            "id": "ID",
            "codename": "Кодовое наименование",
            "description": "Описание",
            "users": "Пользователи",
            "permissions": "Разрешения роли",
        },
        **BaseAdmin.column_labels,
    )
    form_columns = ["permissions"]
    edit_template = "sqladmin/role_edit.html"
    form_overrides = {"permissions": FilteredSelectMultipleField}
    form_args = {"permissions": {"label": "Разрешения роли", "transfer_mode": "permissions"}}
    column_formatters_detail = {"codename": lambda m, a: m.label}


class PermissionAdmin(PermissionLabelAdmin, model=Permission):
    name = "Разрешение"
    name_plural = "Разрешения"
    icon = "fa fa-key"
    inline_columns = ["id", "name", "action_type"]
    column_list = ["id", "codename", "name", "action_type"]
    column_searchable_list = ["name"]
    column_labels = dict(
        {
            "id": "ID",
            "model_type": "Доменный ресурс",
            "model_type_id": "ID типа модели",
            "action_type": "Вид разрешения",
            "codename": "Кодовое имя",
            "name": "Название",
            "roles": "Роли",
        },
        **BaseAdmin.column_labels,
    )
    form_columns = ["name"]
    form_args = {"name": {"label": "Название", "validators": [validators.DataRequired(), validators.Length(max=200)]}}
    column_formatters = {
        "action_type": lambda m, a: m.action_type.label if hasattr(m.action_type, "label") else "—",
        **BaseAdmin.column_formatters,
    }
    column_formatters_detail = {"action_type": lambda m, a: m.action_type.label}


class PlatformAdmin(BaseAdmin, model=Platform):
    name = "Платформа"
    name_plural = "Платформы"
    icon = "fa fa-globe"
    inline_columns = ["id", "name", "platform_type", "is_active"]
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
    inline_columns = ["id", "name", "source_type", "is_active", "last_checked"]
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
            "owner_task_id": "Поиск задачи (lane)",
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
            "description": (
                "Личный профиль, сообщество, канал или чат — тип определяет доступные методы API. "
                "SEARCH — поисковый запрос ветки задачи (owner_task_id), технический источник"
            ),
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
                "incremental_mode — собирать только новое; collection.count / collection.limit — размер "
                "страницы и лимит за прогон; collection.filter — фильтр стены VK; "
                "force_refresh и cli_dates — разовые переопределения дат. "
                "Кого анализировать, задаётся в задаче (tracked_users), а не в источнике"
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
        "owner_task_id": lambda m, a: str(m.owner_task_id) if m.owner_task_id else "—",
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
        from pathlib import Path

        import sqladmin
        from starlette.templating import Jinja2Templates

        from app.services.social.factory import get_social_client

        # Include both app templates and sqladmin templates
        sqladmin_path = Path(sqladmin.__file__).parent
        template_dirs = [str(Path(__file__).parent.parent / "templates"), str(sqladmin_path / "templates")]
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
            if "collection" not in source.params:
                source.params["collection"] = {}

            source.params["collection"]["offset"] = offset
            source.params["collection"]["count"] = per_page

            content = await client.collect_data(source=source, content_type="posts")

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
                        "offset": offset,
                    },
                },
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
                        "3. Выбрать 'Все пользователи' или 'Друзья и друзья друзей'",
                    ],
                    "alternative": "Или используйте другой источник с открытой стеной",
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
                },
            )


class AgentScenarioAdmin(BaseAdmin, model=AgentScenario):
    name = "Сценарий"
    name_plural = "Сценарии"
    icon = "fa fa-robot"

    action_permissions = {
        "view-prompts": ActionType.VIEW,
        "toggle-active": ActionType.UPDATE,
    }
    inline_columns = ["id", "name", "is_active"]
    column_list = ["id", "name", "description", "is_active", "max_tokens"]
    column_searchable_list = ["name", "description"]
    column_sortable_list = ["name", "is_active", "max_tokens"]
    column_labels = dict(
        {
            "id": "ID",
            "name": "Название",
            "description": "Описание сценария",
            # Prompt fields
            "base_prompt": "Базовый промпт анализа",
            "media_overrides": "Переопределения по типам контента",
            "summary_prompt": "Промпт общего резюме",
            "analysis_types": "Типы анализа",
            "content_types": "Поддерживаемые медиа",
            "scope": "Дополнительные параметры",
            "sources": "Источники",
            "text_llm_model": "Модель для текста",
            "image_llm_model": "Модель для изображений",
            "video_llm_model": "Модель для видео",
            "text_llm_provider_id": "ID провайдера для текста",
            "image_llm_provider_id": "ID провайдера для изображений",
            "video_llm_provider_id": "ID провайдера для видео",
            "llm_strategy": "Стратегия выбора модели",
        },
        **BaseAdmin.column_labels,
    )

    form_excluded_columns = BaseAdmin.form_excluded_columns + [
        "sources",
        "agent_tasks",
        "llm_mapping",
        # Exclude these fields — we handle them manually in custom template
        "content_types",
        "analysis_types",
        "scope",
    ]
    form_overrides = {"llm_strategy": SelectField, **BaseAdmin.form_overrides}
    form_args = {
        "llm_strategy": {
            "label": "Стратегия выбора модели",
            "description": (
                "Как подбирается модель, если конкретная не задана: "
                "cost_efficient — дешевле, quality — качественнее, multimodal — с поддержкой медиа"
            ),
            "choices": LLMStrategyType.choices(),
            "coerce": str,
            "validators": [validators.AnyOf([value for value, _ in LLMStrategyType.choices()])],
        },
        "description": {
            "label": "Описание сценария",
            "description": "Для справки: что делает сценарий и на каких источниках применяется",
        },
        "name": {
            "label": "Название",
            "description": "Короткое понятное имя сценария, например «Комментарии к постам VK»",
        },
        "max_tokens": {
            "label": "Лимит токенов ответа",
            "description": "Максимальное число токенов в ответе модели (пусто — использовать значение из настроек модели)",
        },
        "text_llm_model": {
            "label": "Модель для текста",
            "description": "Конкретная модель для анализа текста. Если не указана, выбирается по стратегии сценария",
        },
        "image_llm_model": {
            "label": "Модель для изображений",
            "description": "Конкретная модель для анализа изображений. Если не указана, выбирается по стратегии сценария",
        },
        "video_llm_model": {
            "label": "Модель для видео",
            "description": "Конкретная модель для анализа видео. Если не указана, выбирается по стратегии сценария",
        },
        "base_prompt": {
            "description": (
                "Базовый промпт для анализа любого контента. Оставьте пустым для дефолтного. "
                "Переменные: {text}, {platform}, {source_type}, {total_posts}, {date_range}, "
                "{source_name}, {scenario_name}, {max_keywords}, {max_topics}, {sentiment_categories}"
            )
        },
        "media_overrides": {
            "description": (
                "Переопределения промпта для отдельных типов контента: "
                '{"image": "…", "video": "…", "audio": "…"}. Оставьте пустым, чтобы для всех типов '
                "использовался базовый промпт."
            )
        },
        "summary_prompt": {
            "description": (
                "Кастомный промпт для создания общего резюме из мультимедийного анализа. "
                "Оставьте пустым для дефолтного."
            )
        },
        "scope": {
            "description": (
                "Дополнительные параметры для анализа. "
                "event_based: true - анализ по дням (для мониторинга активности), "
                "event_based: false - анализ по темам (ИИ сам определяет темы). "
                "Также может содержать конфигурацию для analysis_types (sentiment, keywords и др.). "
                "Пример: "
                '{"event_based": true, "sentiment": {"categories": ["Позитивный", "Негативный"]}, "max_events_per_analysis": 50}'
            )
        },
        **BaseAdmin.form_args,
    }

    # Column formatters
    column_formatters = {**BaseAdmin.column_formatters}
    form_widget_args = {
        # Prompt fields with placeholders
        "base_prompt": {
            "rows": 10,
            "placeholder": (
                "Проанализируй следующий контент из {platform}.\n\n"
                "Контент: {text}\n"
                "Период: {date_range}\n\n"
                "Определи основные темы, тональность и ключевые моменты."
            ),
        },
        "media_overrides": {
            "rows": 8,
            "placeholder": (
                '{\n  "image": "Проанализируй {count} изображений из {platform}.",\n'
                '  "video": "Проанализируй {count} видео из {platform}."\n}'
            ),
        },
        "summary_prompt": {
            "rows": 10,
            "placeholder": (
                "Создай единое резюме на основе следующих анализов:\n\n"
                "Текст: {text_analysis}\nИзображения: {image_analysis}\n"
                "Видео: {video_analysis}\n\nВыдели общие темы и ключевые инсайты."
            ),
        },
        "description": {"rows": 2},
        "scope": {
            "rows": 8,
            "placeholder": '{\n  "event_based": true,\n  "sentiment": {\n    "categories": ["Позитивный", "Негативный", "Нейтральный"]\n  },\n  "keywords": {\n    "max_keywords": 20\n  }\n}',
        },
    }

    create_template = "sqladmin/agent_scenario_create.html"
    edit_template = "sqladmin/agent_scenario_edit.html"

    async def scaffold_form(self, rules=None):
        """Provide enum types and presets to template."""
        from app.core.analysis_constants import ANALYSIS_TYPE_DEFAULTS
        from app.core.scenario_presets import get_all_presets
        from app.core.trigger_hints import SCOPE_HINTS

        form = await super().scaffold_form(rules)

        form.content_types_enum = list(MediaType)
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
        from pathlib import Path

        import sqladmin
        from starlette.templating import Jinja2Templates

        from app.services.ai.prompts import PromptBuilder
        from app.services.ai.scenario_builder import ScenarioBuilder

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
            text_analysis={}, image_analysis={}, video_analysis={}, scenario=scenario
        )
        prompts_data["unified"] = {
            "custom": scenario.summary_prompt,
            "full": unified_prompt,
            "has_custom": bool(scenario.summary_prompt),
        }

        # Setup templates
        sqladmin_path = Path(sqladmin.__file__).parent
        template_dirs = [str(Path(__file__).parent.parent / "templates"), str(sqladmin_path / "templates")]
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
                "analysis_configs": analysis_configs,
                "custom_variables": custom_vars,
                "all": scenario.scope,
            }

        # Pre-format JSON strings with ensure_ascii=False for proper Unicode display
        import json

        # Format analysis configs as JSON strings
        analysis_configs_json = {}
        if scope_display.get("analysis_configs"):
            for key, value in scope_display["analysis_configs"].items():
                analysis_configs_json[key] = json.dumps(value, indent=2, ensure_ascii=False)

        # Format custom variables as JSON strings
        custom_vars_json = {}
        if scope_display.get("custom_variables"):
            for key, value in scope_display["custom_variables"].items():
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
            },
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
    name = "ИИ-аналитика"
    name_plural = "ИИ-аналитика"
    icon = "fa fa-chart-bar"

    # Rows are written by the analyzer only — the admin may inspect, correct or
    # remove them, but never fabricate an analysis run.
    can_create = False

    # Просмотр разбора — то же право, что и просмотр карточки аналитики.
    action_permissions = {"view-analysis": ActionType.VIEW}

    # topic_chain_id and content_hash are machine keys kept out of the crowded list
    inline_columns = ["id", "analysis_date", "period_type", "llm_model", "estimated_cost"]
    column_list = ["id", "source", "analysis_date", "period_type", "llm_model", "estimated_cost", "created_at"]
    column_searchable_list = ["source.name", "period_type", "llm_model"]
    column_sortable_list = ["analysis_date", "created_at", "period_type", "estimated_cost"]
    column_default_sort = [("analysis_date", True), ("id", True)]
    column_labels = dict(
        {
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
        },
        **BaseAdmin.column_labels,
    )

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
            if m.period_type and hasattr(m.period_type, "label")
            else str(m.period_type) if m.period_type else "—"
        ),
        "estimated_cost": lambda m, a: format_usd(m.estimated_cost),
        **BaseAdmin.column_formatters,
    }

    column_formatters_detail = {
        "period_type": lambda m, a: (
            m.period_type.label
            if m.period_type and hasattr(m.period_type, "label")
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
    inline_columns = ["id", "title", "notification_type", "is_read", "created_at"]
    column_list = ["id", "title", "notification_type", "is_read", "created_at"]
    column_searchable_list = ["title", "notification_type"]
    column_sortable_list = ["created_at", "is_read"]
    column_default_sort = [("created_at", True)]
    column_labels = dict(
        {
            "id": "ID",
            "title": "Заголовок",
            "message": "Сообщение",
            "notification_type": "Тип уведомления",
            "is_read": "Прочитано",
            "related_entity_type": "Тип сущности",
            "related_entity_id": "ID сущности",
        },
        **BaseAdmin.column_labels,
    )

    column_formatters = {
        "is_read": lambda m, a: "✅ Прочитано" if m.is_read else "📬 Новое",
        "notification_type": lambda m, a: (
            m.notification_type.label
            if m.notification_type and hasattr(m.notification_type, "label")
            else str(m.notification_type) if m.notification_type else "—"
        ),
        **BaseAdmin.column_formatters,
    }

    form_excluded_columns = [] + BaseAdmin.form_excluded_columns

    form_overrides = {"notification_type": SelectField, "is_read": SelectField, **BaseAdmin.form_overrides}

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
            "choices": NotificationType.choices(),  # Use MediaType enum
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
        "is_read": {
            "label": "Прочитано",
            "description": "Снятая галочка означает, что уведомление ещё не просмотрено пользователем",
            "choices": [(True, "Да"), (False, "Нет")],
            "coerce": lambda x: x == "True" if isinstance(x, str) else bool(x),
        },
        **BaseAdmin.form_args,
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
        add_in_list=False,
        add_in_detail=False,
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
            if notification is not None and notification.tenant_id is not None:
                result = await messenger_service.send_notification(
                    title=notification.title,
                    message=notification.message,
                    notification_type=notification.notification_type,
                    messenger="telegram",
                    recipient_id=request.query_params.get("recipient_id"),
                    tenant_id=notification.tenant_id,
                )
                if (result.get("telegram") or {}).get("success") is True:
                    logger.info("Notification %s delivered to messenger", pk)
                else:
                    logger.warning(
                        "Notification %s not delivered: explicit owned recipient required or transport failed", pk
                    )
            elif notification is not None:
                logger.warning("Ownerless notification cannot be delivered to a workspace")
        except Exception:
            logger.warning("Notification messenger action failed")

        return RedirectResponse(
            url=request.url_for("admin:list", identity=self.identity),
            status_code=303,
        )


class LLMProviderAdmin(BaseAdmin, model=LLMProvider):
    name = "Провайдер ИИ"
    name_plural = "Провайдеры ИИ"
    icon = "fa fa-server"
    # The stock templates have no hook for a page script; these two add the UID
    # autofill next to them.
    create_template = "sqladmin/llm_create.html"
    edit_template = "sqladmin/llm_edit.html"

    # Проба подключения ходит в провайдера его же ключом и тратит токены —
    # это часть настройки, а не чтение.
    action_permissions = {"test-connection": ActionType.CONFIGURE}

    inline_columns = ["id", "name", "api_format", "is_active"]
    column_list = ["id", "name", "api_format", "base_url", "is_active"]
    column_searchable_list = ["name", "base_url"]
    column_sortable_list = ["name", "api_format", "is_active"]

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
        },
        **BaseAdmin.column_labels,
    )

    # Explicit order, so the two identity fields sit under the name instead of
    # wherever the mapper happens to list them; `models` is the relationship the
    # console never edits inline. `scaffold_form` drops the owner for an actor
    # who does not choose it.
    form_columns = [
        "owner_user_id",
        "name",
        "uid",
        "description",
        "api_format",
        "base_url",
        "auth_header",
        "encrypted_api_key",
        "is_active",
    ]

    form_overrides = {
        "api_format": SelectField,
        # A plain foreign-key column is skipped by sqladmin's form builder; the
        # owner is still a real field, so it is declared as a select.
        "owner_user_id": SelectField,
        **BaseAdmin.form_overrides,
    }

    form_widget_args = {
        "base_url": {"placeholder": "https://api.openai.com/v1"},
        "auth_header": {"placeholder": "Authorization: Bearer {key}"},
        "encrypted_api_key": {"type": "password", "placeholder": "Enter API key"},
        "owner_user_id": {"placeholder": "Выберите владельца"},
        "uid": {
            "placeholder": "unorouter",
            # Read-only for everyone but the owner: a slug someone else's links
            # already point at is not a value an admin edits by hand.
            "data-uid-autofill": "name",
            "spellcheck": "false",
            "autocomplete": "off",
        },
    }

    form_args = {
        "owner_user_id": {
            "label": "Владелец",
            "description": "Чей это провайдер. Каталог, ключ и модели видны только этому пользователю.",
            # WTForms snapshots `choices` when the form class is built, so the
            # actor's list has to be part of the arguments, not patched after.
            "choices": [],
            "coerce": str,
        },
        "uid": {
            "label": "UID провайдера",
            "description": "Короткий идентификатор латиницей, например unorouter. По умолчанию берётся из названия "
            "до точки; меняет только владелец платформы.",
        },
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
        "is_active": {
            "label": "Активен",
            "description": "Отключите, чтобы исключить провайдера из маршрутизации и авто-выбора моделей",
        },
        **BaseAdmin.form_args,
    }

    async def scaffold_form(self, rules=None):
        """Hide the fields the signed-in actor may not write.

        The owner is the platform owner's field: an admin or operator creates
        providers in their own catalog, so a user picker would only invite a
        choice the server overrides. The UID stays visible to everyone — it is
        part of the provider's identity and links point at it — but read-only
        outside the platform owner, because renaming it breaks those links. The
        server normalises whatever a readonly field still submits.

        The owner select is built here, through `get_model_form` and
        `_form_overrides`, the same way `UserAdmin` builds the role select.
        `scaffold_form` caches its first form on the instance, so mutating the
        cached one would freeze the first actor's choices for every later
        request; a fresh form also means "no owner field for an admin" is a
        property of the actor, not of whoever opened the page first.
        """
        from app.admin.authorization import bound_console_request

        request = bound_console_request()
        actor = self.get_admin_user(request) if request is not None else None
        may_choose = bool(actor is not None and getattr(actor, "is_superuser", False))

        columns = [name for name in self._form_prop_names if may_choose or name != "owner_user_id"]
        form_args = dict(self.form_args)
        if may_choose and "owner_user_id" in columns:
            # The select carries usernames, not ids: a console operator who *may*
            # assign the catalog should not read `42` and guess. The choices are
            # frozen into the class, so they pass through `form_args`.
            from app.models import User as PlatformUser

            owners = await PlatformUser.objects.filter(is_active=True).order_by("username")
            form_args["owner_user_id"] = {
                **form_args.get("owner_user_id", {}),
                "choices": [(str(user.id), user.username) for user in owners],
                "coerce": str,
            }
        form = await get_model_form(
            model=self.model,
            session_maker=self.session_maker,
            only=columns,
            column_labels=self._column_labels,
            form_args=form_args,
            form_widget_args=self.form_widget_args,
            form_class=self.form_base_class,
            form_overrides=self._form_overrides,
            form_ajax_refs=self._form_ajax_refs,
            form_include_pk=self.form_include_pk,
            form_converter=self.form_converter,
        )
        if hasattr(form, "uid"):
            # An empty UID is "derive it from the name", not a missing field:
            # the browser previews the slug and skips the release, so the server
            # fills it here — from the submitted name, before validation.
            #
            # `form` is a class here; `meta.bind_field` is the one seam that sees
            # both the bound field and the form data, for every instance.
            base_meta = form.Meta if "Meta" in dir(form) else Form.Meta
            form_meta = type("ProviderFormMeta", (base_meta,), {})

            def bind_field(meta_self, form_instance, unbound_field, options):
                field = base_meta.bind_field(meta_self, form_instance, unbound_field, options)
                if unbound_field.name != "uid":
                    return field
                original = field.process

                def process(formdata, data=None, **kwargs):
                    original(formdata, data=data, **kwargs)
                    if formdata is None:
                        return
                    raw = (field.raw_data or [""])[0]
                    source = raw or formdata.get("name") or ""
                    slug = slugify_uid(source)
                    if not slug and not raw:
                        return
                    # WTForms validates `raw_data`, so the derived value has to
                    # land there too — an empty list means "missing".
                    field.data = slug or None
                    if slug:
                        field.raw_data = [slug]

                field.process = process
                return field

            form_meta.bind_field = bind_field
            form.Meta = form_meta
        if rules:
            self._validate_form_class(rules, form)
        return form

    async def on_model_change(self, data: dict, model: LLMProvider, is_created: bool, request=None) -> None:
        """Who owns the catalog row is a rule, not a text field.

        The platform owner may hand a catalog to anyone. An admin or operator
        never picks a stranger's owner: their new provider belongs to them, and
        the owner of an existing row stays as it is. A forged POST is refused
        the same way, because this runs on the data the form accepted.
        """
        actor = self.get_admin_user(request)
        may_choose = bool(actor is not None and getattr(actor, "is_superuser", False))
        if not may_choose:
            data.pop("owner_user_id", None)
            if is_created:
                if actor is None:
                    raise HTTPException(status_code=403, detail="Не удалось определить владельца провайдера")
                data["owner_user_id"] = actor.id
                model.owner_user_id = actor.id
        elif is_created and not data.get("owner_user_id"):
            data["owner_user_id"] = actor.id
        if "owner_user_id" in data and data["owner_user_id"] is not None:
            try:
                data["owner_user_id"] = int(data["owner_user_id"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Не удалось определить владельца провайдера")

        uid = data.get("uid")
        if uid is not None:
            slug = slugify_uid(uid)
            if not slug:
                raise HTTPException(status_code=400, detail="UID провайдера может содержать только a-z, 0-9, «-» и «_»")
            data["uid"] = slug
            model.uid = slug

        with self._catalog_scope(request):
            await super().on_model_change(data, model, is_created, request)

    @action(name="test-connection", label="🔌 Проверить подключение", add_in_list=True, add_in_detail=True)
    async def test_connection_action(self, request: Request):
        with self._catalog_scope(request):
            return await self._test_connection(request)

    async def _test_connection(self, request: Request):
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

    async def after_model_change(self, data: dict, model: LLMProvider, is_created: bool, request=None) -> None:
        with self._catalog_scope(request):
            if model.encrypted_api_key and not model.encrypted_api_key.startswith("gAAAAA"):
                from app.utils.crypto import encrypt_secret

                model.encrypted_api_key = encrypt_secret(model.encrypted_api_key)
                await LLMProvider.objects.update_by_id(model.id, encrypted_api_key=model.encrypted_api_key)
            await super().after_model_change(data, model, is_created, request)


def _catalog_rate(value, token_unit: int) -> str:
    unit_label = "1K" if token_unit == 1000 else "1M"
    return f"{model_price(value)} / {unit_label}"


class LLMModelAdmin(BaseAdmin, model=LLMModel):
    name = "Модель ИИ"
    name_plural = "Модели ИИ"
    icon = "fa fa-microchip"
    create_template = "sqladmin/llm_create.html"
    edit_template = "sqladmin/llm_edit.html"

    # Local checks and an explicitly confirmed real probe require operator configuration rights.
    action_permissions = {"test-model": ActionType.CONFIGURE}

    inline_columns = ["id", "name", "model_id", "model_type", "is_default", "is_active"]
    column_list = [
        "id",
        "name",
        "provider",
        "model_id",
        "model_type",
        "pricing_token_unit",
        "input_cost_per_1k",
        "output_cost_per_1k",
        "cache_read_cost_per_1m",
        "cache_write_5m_cost_per_1m",
        "cache_write_1h_cost_per_1m",
        "is_default",
        "is_active",
    ]
    column_searchable_list = ["name", "model_id", "description"]
    column_sortable_list = [
        "name",
        "model_type",
        "max_tokens",
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
            "custom_endpoint_path": "Кастомный endpoint",
            "pricing_token_unit": "Единица обычного тарифа",
            "input_cost_per_1k": "Входной тариф",
            "output_cost_per_1k": "Выходной тариф",
            "cache_read_cost_per_1m": "Чтение кэша $/1M",
            "cache_write_5m_cost_per_1m": "Запись кэша 5 мин $/1M",
            "cache_write_1h_cost_per_1m": "Запись кэша 1 час $/1M",
            "max_tokens": "Макс. токенов",
            "default_temperature": "Температура",
            "is_active": "Активна",
            "is_default": "По умолчанию",
            "use_count": "Вызовов всего",
            "fail_count": "Ошибок всего",
            "last_request_cost": "Оценка последней пробы, $",
            "last_request_cost_at": "Время последней оценки",
            "last_used_at": "Последний вызов",
            "last_success_at": "Последний успех",
            "last_error_at": "Последняя ошибка",
        },
        **BaseAdmin.column_labels,
    )

    column_details_list = column_list + [
        "description",
        "custom_endpoint_path",
        "max_tokens",
        "default_temperature",
        "last_request_cost",
        "last_request_cost_at",
        "use_count",
        "fail_count",
        "last_used_at",
        "last_success_at",
        "last_error_at",
        "created_at",
        "updated_at",
    ]
    form_columns = [
        "provider",
        "name",
        "model_id",
        "description",
        "model_type",
        "custom_endpoint_path",
        "pricing_token_unit",
        "input_cost_per_1k",
        "output_cost_per_1k",
        "cache_read_cost_per_1m",
        "cache_write_5m_cost_per_1m",
        "cache_write_1h_cost_per_1m",
        "max_tokens",
        "default_temperature",
        "is_active",
        "is_default",
    ]

    form_overrides = {
        "model_type": CommaSeparatedSelectMultipleField,
        "pricing_token_unit": SelectField,
        # Parse decimal amounts exactly and store the entered raw rate unchanged.
        "input_cost_per_1k": ExactModelPriceField,
        "output_cost_per_1k": ExactModelPriceField,
        "cache_read_cost_per_1m": ExactModelPriceField,
        "cache_write_5m_cost_per_1m": ExactModelPriceField,
        "cache_write_1h_cost_per_1m": ExactModelPriceField,
        "default_temperature": FloatField,
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
            "choices": [("text", "Текст"), ("image", "Изображение"), ("video", "Видео"), ("audio", "Аудио")],
            "coerce": str,
            "widget": widgets.ListWidget(prefix_label=False),
            "option_widget": widgets.CheckboxInput(),
        },
        "pricing_token_unit": {
            "label": "Единица обычного тарифа",
            "description": "Выбранная единица не пересчитывает цены при изменении.",
            "choices": [(1000, "USD за 1 000 токенов"), (1000000, "USD за 1 000 000 токенов")],
            "coerce": int,
            "default": 1000000,
        },
        "input_cost_per_1k": {
            "label": "Цена входа, USD за выбранное число токенов",
            "description": "Введите сумму как указано в поле единицы тарифа; изменение единицы не меняет сумму.",
        },
        "output_cost_per_1k": {
            "label": "Цена выхода, USD за выбранное число токенов",
            "description": "Введите сумму как указано в поле единицы тарифа; изменение единицы не меняет сумму.",
        },
        "cache_read_cost_per_1m": {
            "label": "Чтение кэша $/1M токенов",
            "description": "Пусто означает, что тариф не настроен",
        },
        "cache_write_5m_cost_per_1m": {
            "label": "Запись кэша 5 мин $/1M токенов",
            "description": "Пусто означает, что тариф не настроен",
        },
        "cache_write_1h_cost_per_1m": {
            "label": "Запись кэша 1 час $/1M токенов",
            "description": "Пусто означает, что тариф не настроен",
        },
        "max_tokens": {
            "label": "Макс. токенов",
            "description": "Максимум токенов генерации (не размер контекстного окна)",
        },
        "default_temperature": {
            "label": "Температура",
            "description": "Температура генерации (0.0 — максимальная точность, 1.0 — творческий ответ). По умолчанию 0.3",
        },
        "is_active": {
            "label": "Активна",
            "description": "Отключите, чтобы модель не использовалась сценариями и авто-выбором",
        },
        "is_default": {
            "label": "По умолчанию",
            "description": "Дефолтная модель этого типа (одна на тип, остальные сбросятся)",
        },
        "custom_endpoint_path": {
            "label": "Кастомный endpoint",
            "description": "Для api_format=custom: путь endpoint'а, например /v1/systemone",
        },
        "last_request_cost": {
            "label": "Стоимость последнего запроса",
            "description": "Заполняется автоматически после теста модели",
        },
        "last_request_cost_at": {
            "label": "Когда был последний запрос",
            "description": "Заполняется автоматически после теста модели",
        },
        "use_count": {
            "label": "Вызовов всего",
            "description": "Заполняется автоматически при каждом вызове модели",
        },
        "fail_count": {
            "label": "Ошибок всего",
            "description": "Заполняется автоматически при неуспешных вызовах (5xx, таймаут, сеть)",
        },
        "last_used_at": {
            "label": "Последний вызов",
            "description": "Заполняется автоматически",
        },
        "last_success_at": {
            "label": "Последний успех",
            "description": "Заполняется автоматически при успешном вызове",
        },
        "last_error_at": {
            "label": "Последняя ошибка",
            "description": "Заполняется автоматически при неуспешном вызове",
        },
        **BaseAdmin.form_args,
    }

    column_formatters = {
        "provider": lambda m, a: m.provider.name if m.provider else "—",
        "model_type": lambda m, a: m.model_type if m.model_type else "—",
        "pricing_token_unit": lambda m, a: "1K tokens" if m.pricing_token_unit == 1000 else "1M tokens",
        "input_cost_per_1k": lambda m, a: _catalog_rate(m.input_cost_per_1k, m.pricing_token_unit),
        "output_cost_per_1k": lambda m, a: _catalog_rate(m.output_cost_per_1k, m.pricing_token_unit),
        "cache_read_cost_per_1m": lambda m, a: (
            model_price(m.cache_read_cost_per_1m) if m.cache_read_cost_per_1m is not None else "—"
        ),
        "cache_write_5m_cost_per_1m": lambda m, a: (
            model_price(m.cache_write_5m_cost_per_1m) if m.cache_write_5m_cost_per_1m is not None else "—"
        ),
        "cache_write_1h_cost_per_1m": lambda m, a: (
            model_price(m.cache_write_1h_cost_per_1m) if m.cache_write_1h_cost_per_1m is not None else "—"
        ),
        **BaseAdmin.column_formatters,
    }

    column_formatters_detail = column_formatters

    async def scaffold_form(self, rules=None):
        from app.admin.authorization import bound_console_request

        form = await super().scaffold_form(rules)

        # Ensure SelectMultipleField for model_type uses proper multi-value coercion/processing
        if hasattr(form, "model_type"):
            # If the model has a comma-separated string, process_data/process_formdata needs to handle it.
            # Actually WTForms SelectMultipleField handles list of strings, but model.model_type is a string.
            pass

        # The signed-in operator's own providers only: a model is filed in the
        # catalog it belongs to, and a foreign provider is not even offered.
        with self._catalog_scope(bound_console_request()):
            providers = await LLMProvider.objects.all().order_by("name")

        if hasattr(form, "provider_id"):
            field_name = "provider_id"
        elif hasattr(form, "provider"):
            field_name = "provider"
        else:
            return form

        # `choices` is what validation and the widget both read; a `data` list of
        # model instances renders the options but never validates them, and the
        # submitted id is then rejected as "Not a valid choice".
        field = getattr(form, field_name)
        field.data = [(str(p.id), p.name) for p in providers]
        field.choices = list(field.data)
        field.coerce = str
        field.get_label = lambda option, field=field: dict(field.choices).get(str(option), "")

        return form

    async def insert_model(self, request: Request, data: dict) -> Any:
        """Fill the owner before sqladmin touches the relation.

        The field is out of the form (`owner_user_id` is half of the composite
        key to the provider and NOT NULL), so the provider decides it. sqladmin
        fills a many-to-one relation from the related row's *primary key* only,
        while this relation carries two FK columns — a `KeyError` naming
        `owner_user_id` is exactly that. The value is already set here, so the
        relation is dropped and the scalar FK goes in instead.
        """
        provider_id = data.pop("provider_id", None) or data.pop("provider", None)
        with self._catalog_scope(request):
            provider = await LLMProvider.objects.get(id=int(provider_id)) if provider_id not in (None, "") else None
        if provider is None:
            raise HTTPException(status_code=400, detail="Выберите провайдера — от него берётся владелец модели")

        # The catalog scope already answered "is this provider in *my* catalog":
        # a foreign or unknown id is simply not found, so the case never reaches
        # here. The owner comes from the provider — the form has no such field.
        data["owner_user_id"] = provider.owner_user_id
        data["provider_id"] = provider.id
        with self._catalog_scope(request):
            return await super().insert_model(request, data)

    async def on_model_change(self, data: dict, model: LLMModel, is_created: bool, request=None) -> None:
        if "model_type" in data and isinstance(data["model_type"], list):
            data["model_type"] = ",".join(data["model_type"])
        if is_created:
            model.owner_user_id = data["owner_user_id"]
            model.provider_id = data["provider_id"]
        else:
            # SQLAdmin resolves the `provider` relation from the provider's
            # primary key alone, and this relation carries two FK columns — a
            # `KeyError` on `owner_user_id`. The model stays in its provider (the
            # pair is half of the composite key and immutable by design), so the
            # field is dropped and the stored scalars answer for it.
            data.pop("provider", None)
            data.pop("provider_id", None)
            data.pop("owner_user_id", None)
        with self._catalog_scope(request):
            await super().on_model_change(data, model, is_created, request)

    async def after_model_change(self, data: dict, model: LLMModel, is_created: bool, request=None) -> None:
        with self._catalog_scope(request):
            await super().after_model_change(data, model, is_created, request)

        if model.is_default:
            # One default per capability fleet-wide — the same rule
            # LLMModelManager.create_model/update_model enforce, through the
            # same helper. Resolution filters with `can_handle()`, so a
            # "text,video" model and a "text" model both answer a "text"
            # request; matching on the model_type *string* left both flagged
            # and forked the fleet into a provider-priority lottery. Runs
            # after the commit, where a freshly created row already has its id.
            with self._catalog_scope(request):
                await LLMModel.objects.set_default(model.id)

    async def delete_model(self, request: Request, pk: Union[int, str]) -> None:
        """Override default delete to use manager's reassignment logic.

        sqladmin passes `pk` as a string from query params — cast to int
        before forwarding to the manager which expects an integer id.
        """
        model_id = int(pk) if isinstance(pk, str) else pk
        with self._catalog_scope(request):
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

        with self._catalog_scope(request):
            return await LLMModelActions.test_model(self, request, pks, self.identity)


class TenantAdmin(BaseAdmin, model=Tenant):
    name = "Рабочее пространство"
    name_plural = "Рабочие пространства"
    icon = "fa fa-building"

    inline_columns = ["id", "name", "slug", "plan", "is_active"]
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
    column_labels = dict(
        {
            "id": "ID",
            "name": "Название",
            "slug": "Слаг",
            "plan": "Тариф",
            "timezone": "Часовой пояс",
            "is_active": "Активно",
            "daily_cost_limit": "Дневной лимит затрат, $",
            "max_sources": "Макс. источников",
            "agent_style": "Стиль агента",
        },
        **BaseAdmin.column_labels,
    )

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
        "slug": {"label": "Слаг", "description": "Уникальный короткий идентификатор, используется в URL и заголовках"},
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
        """Workspace-wide administration is reserved for platform admins.

        The gate is the `tenancy.workspace` right, not the platform badge: the
        owner holds every registered action, an `admin` must be granted the
        right like any other role. A model permission is still the right
        question rather than a per-row one — the list spans every workspace.
        """
        user = self.get_admin_user(request)
        return bool(user is not None and user.has_perm_for("tenant", ActionType.VIEW))


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

    inline_columns = ["id", "platform", "kind", "label", "expires_at", "is_active"]
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

    # High-volume raw content stays on its dedicated list, never in parent inlines.
    inline_exclude = True
    column_list = [
        "id",
        "source_id",
        "platform",
        "external_id",
        "content_type",
        "selection_status",
        "text",
        "media_type",
        "published_at",
        "created_at",
    ]
    column_searchable_list = ["external_id", "platform"]
    column_sortable_list = ["published_at", "created_at", "id"]
    column_default_sort = [("created_at", True)]
    column_details_exclude_list = ["content_hash"]

    column_labels = dict(
        {
            "id": "ID",
            "run_id": "ID запуска",
            "source_id": "Источник",
            "external_id": "Внешний ID",
            "content_type": "Тип контента",
            "parent_external_id": "Родительская запись",
            "lane_id": "Ветка сбора",
            "selection_status": "Отбор",
            "selection_reason": "Причина отбора",
            "content_hash": "Хэш контента",
            "platform": "Платформа",
            "published_at": "Дата публикации",
            "media_type": "Тип медиа",
            "text": "Текст",
            "metrics": "Метрики",
            "author": "Автор",
            "permalink": "Ссылка",
        },
        **BaseAdmin.column_labels,
    )

    form_excluded_columns = BaseAdmin.form_excluded_columns + [
        "content_hash",
        # Written by the collector/selector, never by an operator.
        "lane_id",
        "selection_status",
        "selection_reason",
    ]
    form_widget_args = {"text": {"rows": 8}, "metrics": {"rows": 4, "placeholder": '{"likes": 10, "comments": 3}'}}

    form_args = {
        "source_id": {"label": "Источник", "description": "ID источника из таблицы sources"},
        "external_id": {"label": "Внешний ID", "description": "Идентификатор на платформе"},
        "platform": {"label": "Платформа", "description": "vk, telegram и т.д."},
        "content_type": {
            "label": "Тип контента",
            "description": "post — запись источника, comment — комментарий; входит в уникальный ключ",
        },
        "parent_external_id": {
            "label": "Родительская запись",
            "description": "Для комментария — внешний ID записи, под которой он оставлен",
        },
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

    inline_columns = ["id", "action_type", "status", "dry_run", "created_at"]
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
            "dry_run": "Тестовый запуск",
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

    inline_columns = ["id", "name", "job_type", "is_active", "next_run_at", "last_status"]
    column_list = [
        "id",
        "tenant",
        "name",
        "job_type",
        "cron_expr",
        "is_active",
        "dry_run",
        "next_run_at",
        "last_run_at",
        "last_status",
    ]
    column_searchable_list = ["name", "job_type"]
    column_sortable_list = ["name", "job_type", "is_active", "next_run_at"]
    column_labels = dict(
        {
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
            "dry_run": "Тестовый прогон (dry-run)",
        },
        **BaseAdmin.column_labels,
    )

    form_columns = [
        "tenant",
        "name",
        "job_type",
        "cron_expr",
        "sources",
        "agent_scenario",
        "payload",
        "is_active",
        # Trigger/action configuration lives here, not on the scenario: it
        # describes what this run does with its own analyses.
        "trigger_type",
        "trigger_config",
        "action_type",
        "rate_limit_per_hour",
        "cooldown_seconds",
        "dry_run",
    ]

    form_overrides = {
        "sources": FilteredSelectMultipleField,
        "is_active": SelectField,
        "job_type": SelectField,
        **BaseAdmin.form_overrides,
    }

    form_widget_args = {
        "name": {"placeholder": "Например: hourly-collect"},
        "cron_expr": {"placeholder": "0 * * * *"},
        "payload": {"rows": 8, "placeholder": '{"period": "day", "collection": {"content_types": ["posts"]}}'},
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
            "description": (
                'JSON задачи, например {"period": "day"}. Сбор (job_type=analyze): collection — '
                "content_types (posts|comments), engagement_types (likes|reposts), limit, queries, "
                "city {name, mode}, discovery {people, communities, queries}, comments "
                "{max_per_post, total}, candidates {limit, confirm}, date_window {start, end}. "
                "Выборка (docs/COLLECTION.md#task-selection-contract): tracked_users — авторы из источников задачи, "
                "чьи записи анализируются (пусто — записи самих источников); keywords_list + "
                "keywords_match (any|all) — фразы, которые должны быть в записи. Что и где собирать, "
                "задаёт задача; сценарий отвечает за методику анализа"
            ),
        },
        "dry_run": {
            "label": "Тестовый прогон (dry-run)",
            "description": "Задача отрабатывает без записи собранных данных и без вызова модели",
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
        "dry_run": lambda m, a: "Да" if m.dry_run else "Нет",
        "sources": lambda m, a: ", ".join(s.name for s in m.sources) or "Все активные",
        "agent_scenario": lambda m, a: m.agent_scenario.name if m.agent_scenario is not None else "—",
        **BaseAdmin.column_formatters,
    }

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
        """Schedule a task saved through the admin form.

        The admin form omits `next_run_at`, so a task created/edited here would
        otherwise never become due. Compute it from the cron expression so
        admin-created tasks actually fire (a @once task is scheduled ~1 min out).
        """
        # The payload is a JSON column, so the form can hand back any JSON value
        # (a list, a string, a number). The collection limits inside it are
        # parsed by the shared writer on every other write surface (web, API,
        # CLI, chat); the admin used to store them unchecked, so a malformed
        # limit reached the runner. Validate and canonicalise before any query.
        from collections.abc import Mapping

        from app.services.collection.intent import CollectionIntentError, apply_collection, collection_error_message

        job_type = data.get("job_type") or getattr(model, "job_type", "") or ""
        raw_payload = data["payload"] if "payload" in data else getattr(model, "payload", None)
        if raw_payload is None:
            raw_payload = {}
        if not isinstance(raw_payload, Mapping):
            raise ValueError('Параметры задачи: нужен JSON-объект, например {"period": "day"}')
        try:
            payload = apply_collection(dict(raw_payload), raw_payload.get("collection"), job_type=job_type)
            if "task_plan" in raw_payload:
                from app.services.task_plans import TaskPlanError, apply_task_plan, parse_task_plan

                plan_raw = raw_payload["task_plan"]
                plan = parse_task_plan(plan_raw)
                planned_cron = "@once" if plan.trigger.type == "once" else plan.trigger.cron
                cron_value = data.get("cron_expr") or getattr(model, "cron_expr", None)
                if cron_value != planned_cron:
                    raise TaskPlanError("cron_expr must match task_plan.trigger")
                raw_sources = data.get("sources")
                if plan.source_scope == "selected" and raw_sources is not None:
                    linked_ids = sorted(getattr(source, "id", source) for source in raw_sources)
                    if linked_ids != sorted(plan.source_ids):
                        raise TaskPlanError("Admin source selection must match task_plan.source_scope")
                raw_scenario = data.get("agent_scenario")
                selected_scenario = getattr(raw_scenario, "id", raw_scenario)
                if selected_scenario is not None and selected_scenario != plan.scenario_id:
                    raise TaskPlanError("Admin scenario must match task_plan.scenario_id")
                if plan.source_scope != "selected":
                    data["sources"] = []
                payload = apply_task_plan(payload, plan_raw, job_type=job_type)
            data["payload"] = payload
        except CollectionIntentError as exc:
            raise ValueError(collection_error_message(str(exc))) from None
        except ValueError as exc:
            raise ValueError(str(exc)) from None

        # Guard: an active source-based task (collect/analyze) needs at least one
        # active source to operate on. Empty links fall back to "all active
        # sources", so the check is on the workspace, mirroring the web rule.
        from app.core.tenant_context import tenant_scope
        from app.models import Source
        from app.models.managers.agent_task_manager import AgentTaskManager

        active = data.get("is_active", getattr(model, "is_active", False))
        # The platform admin has no personal payer context. A newly authored
        # paid plan must be activated by its workspace owner after consent.
        if is_created and isinstance(data.get("payload"), Mapping) and "task_plan" in data["payload"]:
            active = False
            data["is_active"] = False
            if request is not None and getattr(request, "session", None) is not None:
                request.session["admin_message"] = {
                    "type": "warning",
                    "message": "Новая задача сохранена неактивной: владелец пространства должен настроить оплату и активировать её.",
                }
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
                    has_active = (
                        await Source.objects.filter(Source.id.in_(submitted_ids), tenant_id=tenant_id, is_active=True)
                        .values(Source.id)
                        .rows()
                    )
                else:
                    has_active = (
                        await Source.objects.filter(tenant_id=tenant_id, is_active=True).values(Source.id).rows()
                    )
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
            scenario_pk = (
                int(scenario_id_val)
                if isinstance(scenario_id_val, (int, str))
                else getattr(scenario_id_val, "id", None)
            )
            if scenario_pk and request is not None:
                with tenant_scope(bypass=True):
                    scenario = await AgentScenario.objects.filter(id=scenario_pk).first()
                if scenario and scenario.analysis_types:
                    from app.services.ai.param_registry import missing_target_params

                    task_payload = data.get("payload") or {}
                    missing = missing_target_params(scenario.analysis_types, task_payload)
                    if missing:
                        msg = (
                            "⚠️ Для сценария «"
                            + (scenario.name or str(scenario_pk))
                            + "» укажите в параметрах задачи: "
                            + ", ".join(missing)
                        )
                        # Starlette's Session is dict-like and has no `flash()`
                        # (that is Flask) — the save used to die here with
                        # AttributeError. The admin layout renders this key as a toast.
                        session = getattr(request, "session", None)
                        if session is not None:
                            session["admin_message"] = {"type": "warning", "message": msg}

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
                from app.services.task_plans import task_plan_timezone

                task_plan = (data.get("payload") or {}).get("task_plan")
                data["next_run_at"] = compute_next(
                    cron_expr,
                    task_plan_timezone(task_plan, resolve_tz(tenant)),
                )
        await super().on_model_change(data, model, is_created, request)

    def list_query(self, request: Request) -> Select:
        return AgentTask.objects.prefetch_related("sources", "agent_scenario").filter().to_select()

    def details_query(self, request: Request) -> Select:
        pk = int(request.path_params["pk"])
        return AgentTask.objects.prefetch_related("sources", "agent_scenario").filter(id=pk).to_select()

    @action(
        name="run_now",
        label="Выполнить сейчас",
        add_in_list=True,
        add_in_detail=True,
    )
    async def run_now_action(self, request: Request):
        """Run the selected task's job immediately; completes a @once task."""
        from app.core.tenant_context import current_tenant_id, tenant_scope
        from app.jobs.dispatcher import run_task_directly

        pks = request.query_params.get("pks", "")
        if not pks:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=303)
        task_id = int(pks.split(",")[0])

        # The manual Job admission boundary (`_admit_task_run`) credits the
        # actor from the ambient permission_scope user into the Job row
        # (`initiated_by_user_id`). That identity comes only from the session
        # the admin authorization backend put on `request.state.admin_user` —
        # never from query/path/body or the task payload. Fail closed when the
        # trusted operator is absent or inactive.
        admin_user = self.get_admin_user(request)
        if admin_user is None or not getattr(admin_user, "is_active", False):
            raise HTTPException(status_code=403, detail="Доступ запрещён: оператор не аутентифицирован")

        # Read only inside the actor's selected workspace. Preserve its id;
        # opening a tenant-less scope would either leak rows via bypass or fail
        # closed for every ordinary operator. Only the owner retains bypass.
        with tenant_scope(current_tenant_id(), bypass=bool(getattr(admin_user, "is_superuser", False))):
            task = await AgentTask.objects.get(id=task_id)
        if task:
            from app.core.permissions import permission_scope

            with permission_scope(admin_user):
                outcome = await run_task_directly(task)
            if outcome and outcome.get("status") == "failed":
                request.session["admin_message"] = {
                    "type": "error",
                    "message": f"Задача «{task.name}» не выполнена: {outcome.get('error') or '?'}",
                }
            else:
                request.session["admin_message"] = {"type": "success", "message": f"Задача «{task.name}» выполнена"}
        return RedirectResponse(request.url_for("admin:list", identity=self.identity), status_code=303)


from app.models import Job


class JobAdmin(BaseAdmin, model=Job):
    """Global operator inspection; mutations use the scoped queue workflow."""

    name = "Выполнение"
    name_plural = "Очередь выполнения"
    icon = "fa fa-list"
    can_create = False
    can_edit = False
    can_delete = False
    can_export = False
    column_labels = {
        **BaseAdmin.column_labels,
        "job_type": "Тип задания",
        "status": "Статус",
        "run_at": "Запуск",
        "attempts": "Попытки",
    }
    inline_columns = ["id", "job_type", "status", "run_at", "attempts"]
    column_list = ["id", "tenant_id", "agent_task_id", "job_type", "status", "run_at", "attempts"]
    column_details_list = column_list + ["locked_at", "started_at", "finished_at", "created_at", "updated_at"]
    column_sortable_list = ["id", "run_at", "status"]
    column_searchable_list = ["job_type", "status"]
