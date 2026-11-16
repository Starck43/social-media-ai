"""Sources SQLAdmin views."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_object_session

from app.models.managers.source_manager import _protect_telegram_binding

from ._shared import (
    ActionType,
    Any,
    BaseAdmin,
    HTTPException,
    Platform,
    PlatformType,
    RedirectResponse,
    Request,
    Select,
    SelectField,
    Source,
    SourceType,
    action,
    datetime,
    logger,
    logging,
    validators,
)

logger = logging.getLogger(__name__)


class PlatformAdmin(BaseAdmin, model=Platform):
    name = "Платформа"
    name_plural = "Платформы"
    icon = "fa fa-globe"
    category = "Источники и сбор"
    category_icon = "fa fa-rss"
    # A platform cascade cannot run the per-workspace source-admission policy.
    # Disable both the SQLAdmin route/buttons and direct view-method callers.
    can_delete = False

    async def delete_model(self, request: Request, pk: Any) -> None:
        raise HTTPException(status_code=403, detail="admin_platform_delete_disabled")

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
                "JSON — единственный источник общих настроек платформы (переменные окружения его не перекрывают). "
                "Обязательные ключи: api_base_url — URL API без завершающего слеша; для VK ещё api_version. "
                "Ключи VK OAuth: auth_type=oauth, oauth_base_url — адрес авторизации VK ID, "
                "oauth_token_url — адрес обмена кода (обычно <oauth_base_url>/oauth2/auth, "
                "для VK ID с PKCE это именно /oauth2/auth, не /oauth2/access_token), "
                "redirect_uri — адрес возврата, дословно совпадающий с зарегистрированным в кабинете VK ID; "
                "после смены redirect_uri повторное подключение ВК обязательна — старая авторизация его не знает. "
                "Прочие ключи: auth_type, category, data_boundary (local_only|company|cloud), capabilities — "
                "список доступных методов, write_capabilities — методы, меняющие данные (для VK — []), "
                "rate_limit_per_hour — потолок запросов. Правка применяется без перезапуска. "
                'Пример: {"api_base_url": "https://api.vk.com/method", "api_version": "5.199", '
                '"auth_type": "oauth", "redirect_uri": "http://localhost/api/v1/social/callback"}'
            ),
        },
        **BaseAdmin.form_args,
    }


class SourceAdmin(BaseAdmin, model=Source):
    name = "Источник"
    name_plural = "Источники"
    icon = "fa fa-rss"
    category = "Источники и сбор"
    category_icon = "fa fa-rss"
    # Generic SQLAdmin deletion bypasses the admission lock and task-scope guard.
    # Use the existing workspace source-deletion surfaces instead.
    can_delete = False

    async def delete_model(self, request: Request, pk: Any) -> None:
        raise HTTPException(status_code=403, detail="admin_source_delete_disabled")

    async def insert_model(self, request: Request, data: dict) -> Any:
        params = data.get("params")
        if type(params) is dict and "telegram_authorization" in params:
            raise HTTPException(status_code=403, detail="telegram_source_picker_required")
        return await super().insert_model(request, data)

    async def on_model_change(self, data: dict, model: Source, is_created: bool, request: Request) -> None:
        """Protect Telegram picker consent in SQLAdmin's own write transaction."""
        if is_created:
            params = data.get("params")
            if type(params) is dict and "telegram_authorization" in params:
                raise HTTPException(status_code=403, detail="telegram_source_picker_required")
            return
        if model is None:
            raise HTTPException(status_code=404, detail="admin_source_not_found")
        session = async_object_session(model)
        if session is None:
            raise HTTPException(status_code=403, detail="telegram_source_update_unavailable")
        # SQLAdmin selected the row before this hook without a lock. Re-read it
        # under FOR UPDATE in the same transaction and refresh the identity map:
        # an intervening picker change cannot be overwritten from stale form data.
        current = await session.scalar(
            select(Source).where(Source.id == model.id).with_for_update().execution_options(populate_existing=True)
        )
        if current is None:
            raise HTTPException(status_code=404, detail="admin_source_not_found")
        changes = dict(data)
        for relationship, column in (("tenant", "tenant_id"), ("platform", "platform_id")):
            if relationship in changes:
                value = changes.pop(relationship)
                value = getattr(value, "id", value)
                if type(value) is str and value.isdecimal():
                    value = int(value)
                changes[column] = value
        try:
            _protect_telegram_binding(current, changes)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="telegram_source_picker_required") from exc

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
    # Ownership is actor-derived at creation and must remain read-only here.
    column_labels = {**column_labels, "owner_user_id": "Пользователь для сбора"}

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
        template_dirs = [str(Path(__file__).resolve().parents[2] / "templates"), str(sqladmin_path / "templates")]
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


__all__ = [name for name in globals() if not name.startswith("__")]
