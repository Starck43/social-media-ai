from contextlib import nullcontext
from datetime import date, datetime
from typing import Any, ClassVar

from sqladmin import ModelView
from sqladmin.fields import SelectField
from sqladmin.forms import ModelConverter, converts
from sqlalchemy import Select, false
from starlette.requests import Request
from wtforms import FloatField

from app.core.personal_catalog_context import catalog_execution_scope
from app.types import ActionType

from .formatters import bool_formatter, date_formatter, datetime_formatter, empty_formatter


class AdminFormConverter(ModelConverter):
    """Keep floating point forms compatible with SQLAlchemy's type hierarchy."""

    async def convert(self, model, prop, session_maker, field_args, field_widget_args, form_include_pk, **kwargs):
        # A foreign-key column is normally a hidden `provider`-style relation, so
        # sqladmin drops it from the form. The two views that edit an ownership
        # pointer by hand (`owner_user_id` on a provider/model) submit an id, not
        # a relation, so this column is kept and rendered by its own override.
        if kwargs.get("override") is not None and prop.key in _EXPLICIT_FK_COLUMNS:
            form_include_pk = True
        return await super().convert(
            model=model,
            prop=prop,
            session_maker=session_maker,
            field_args=field_args,
            field_widget_args=field_widget_args,
            form_include_pk=form_include_pk,
            **kwargs,
        )

    @converts("Float", "FLOAT", "Double", "DOUBLE", "DOUBLE_PRECISION", "REAL")
    def conv_float(self, model, prop, kwargs):
        # SQLAlchemy 2.1 Float no longer inherits Numeric, which SQLAdmin's
        # decimal converter relies on. Numeric/Decimal money stays unchanged.
        return FloatField(**kwargs)


# Foreign-key columns the console edits as a plain integer select, by column
# name. `owner_user_id` is the same column on both the provider and the model.
_EXPLICIT_FK_COLUMNS = frozenset({"owner_user_id"})


class BaseAdmin(ModelView):
    """Base admin view with common configurations."""

    form_converter = AdminFormConverter

    # Common settings
    icon = "fa fa-table"
    page_size = 50
    page_size_options = [25, 50, 100, 200]
    save_as = False

    # Target-view defaults for every reverse-FK inline. Columns remain subject
    # to the shared scalar/list/detail safety rules, never an access override.
    inline_columns: ClassVar[list[str] | None] = None
    inline_page_size = 25
    inline_page_size_options = (10, 25, 50, 100)
    inline_exclude = False

    # Which `ActionType` each custom `@action` of this view needs, by the slug
    # the `action` decorator generates. Unlisted actions default to `update`
    # (they are mutations by nature); a pure read declares `view` explicitly, a
    # probe against a provider declares `configure`. `AdminAuthorizationBackend`
    # reads this when it turns the operator's role into the grants of the
    # console, so the button and its endpoint share one answer.
    action_permissions: ClassVar[dict[str, ActionType]] = {}

    @staticmethod
    def get_admin_user(request: Request) -> Any | None:
        """The operator on this request.

        Resolved once per request by the authorization backend and kept on
        `request.state`; the admin views that need an extra check of their own
        read it from here instead of re-querying.
        """
        return getattr(request.state, "admin_user", None)

    # Type-driven rendering: applied to every column of every admin view. sqladmin
    # mirrors these into the detail pipeline as long as column_type_formatters_detail
    # is left at its default, so one registration covers list, detail and CSV export.
    # Day-first format is the project-wide convention for operators:
    # dates -> 05.10.2025, timestamps -> 05.10.2025 14:30, NULL -> —.
    column_type_formatters = {
        type(None): empty_formatter,
        bool: bool_formatter,
        datetime: datetime_formatter,
        date: date_formatter,
    }

    column_labels = {
        "id": "ID",
        "name": "Название",
        "user_id": "Пользователь",
        "owner_user_id": "Владелец",
        "tenant_id": "Рабочее пространство",
        "provider_id": "Провайдер",
        "platform_id": "Платформа",
        "source_id": "Источник",
        "agent_task_id": "Задача",
        "agent_scenario_id": "Сценарий",
        "role_id": "Роль",
        "initiated_by_user_id": "Инициатор",
        "text_llm_model_id": "Текстовая модель",
        "image_llm_model_id": "Модель изображений",
        "video_llm_model_id": "Видеомодель",
        "agent_model_id": "Модель чата",
        "created_at": "Дата создания",
        "updated_at": "Дата обновления",
        "is_active": "Активен",
    }

    column_formatters = {
        "role": lambda m, a: m.role.label if m.role else "",
    }

    form_overrides = {
        "is_active": SelectField,
        "is_default": SelectField,
    }

    form_args = {
        "is_active": {
            "choices": [(True, "Да"), (False, "Нет")],
            "coerce": lambda x: x == "True" if isinstance(x, str) else bool(x),
            "description": "Выключенная запись не участвует в работе планировщика и сбора данных",
        },
        "is_default": {
            "choices": [(True, "Да"), (False, "Нет")],
            "coerce": lambda x: x == "True" if isinstance(x, str) else bool(x),
            "description": "Приоритетная запись: используется, когда явный выбор не задан",
        },
    }
    form_excluded_columns = ["created_at", "updated_at"]
    form_include_relationships = True

    # --- personal catalog isolation -----------------------------------------

    @property
    def catalog_scoped(self) -> bool:
        """True for views whose rows belong to a person, not to a workspace.

        `llm_provider` / `llm_model` are `__personal_scoped__`, and their manager
        filters every query by the *current* catalog owner. A console page must
        therefore run its queries as the signed-in operator, never as the
        request's platform bypass — otherwise `current_catalog_owner_id()` has no
        bound user and fails closed on every page. One hook serves all read paths
        (`get_object_for_edit`, `get_object_for_details`); the writing paths
        (`insert_model`, `on_model_change`) open the same scope themselves.
        """
        return bool(getattr(self.model, "__personal_scoped__", False))

    def _catalog_scope(self, request: Request | None):
        """The signed-in operator's catalog; no-op for any other view.

        Without an actor the scope stays unbound on purpose: the manager then
        fails closed instead of answering for somebody else.
        """
        user_id = self._catalog_owner_id(request)
        if not self.catalog_scoped or user_id is None:
            return nullcontext()
        return catalog_execution_scope(user_id)

    def _catalog_owner_id(self, request: Request) -> int | None:
        actor = self.get_admin_user(request) if request is not None else None
        user_id = getattr(actor, "id", None)
        return user_id if type(user_id) is int and user_id > 0 else None

    def _owned(self, stmt: Select, request: Request) -> Select:
        """Restrict a statement to the signed-in operator's own rows.

        The manager's `owner_user_id` filter cannot reach here: sqladmin builds
        its own `select(model)` for the list page, so the catalog row's owner is
        added as a plain `WHERE` clause. Applied to the count query too —
        otherwise the pager promises rows the page cannot show.
        """
        if not self.catalog_scoped:
            return stmt
        owner_user_id = self._catalog_owner_id(request)
        if owner_user_id is None:
            return stmt.where(false())
        return stmt.where(self.model.owner_user_id == owner_user_id)

    def list_query(self, request: Request) -> Select:
        return self._owned(super().list_query(request), request)

    def count_query(self, request: Request) -> Select:
        return self._owned(super().count_query(request), request)

    async def get_object_for_edit(self, request: Request) -> Any:
        with self._catalog_scope(request):
            return await super().get_object_for_edit(request)

    async def get_object_for_details(self, request: Request) -> Any:
        with self._catalog_scope(request):
            return await super().get_object_for_details(request)

    async def on_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
        """Perform actions before model is created/updated."""
        await super().on_model_change(data, model, is_created, request)

    async def after_model_change(self, data: dict, model: Any, is_created: bool, request=None) -> None:
        """Perform actions after model is created/updated."""
        await super().after_model_change(data, model, is_created, request)
