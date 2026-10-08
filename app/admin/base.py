from datetime import date, datetime
from typing import Any, ClassVar

from sqladmin import ModelView
from sqladmin.fields import SelectField
from starlette.requests import Request

from app.types import ActionType

from .formatters import bool_formatter, date_formatter, datetime_formatter, empty_formatter


class BaseAdmin(ModelView):
    """Base admin view with common configurations."""

    # Common settings
    icon = "fa fa-table"
    page_size = 50
    page_size_options = [25, 50, 100, 200]
    save_as = False

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
        "created_at": "Дата создания",
        "updated_at": "Дата обновления",
        "is_active": "Активен",
    }

    column_formatters = {
        "role": lambda m, a: m.role.name.upper() if m.role else "",
    }

    form_overrides = {
        'is_active': SelectField,
        'is_default': SelectField,
    }

    form_args = {
        'is_active': {
            'choices': [(True, 'Да'), (False, 'Нет')],
            'coerce': lambda x: x == 'True' if isinstance(x, str) else bool(x),
            'description': 'Выключенная запись не участвует в работе планировщика и сбора данных',
        },
        'is_default': {
            'choices': [(True, 'Да'), (False, 'Нет')],
            'coerce': lambda x: x == 'True' if isinstance(x, str) else bool(x),
            'description': 'Приоритетная запись: используется, когда явный выбор не задан',
        }
    }
    form_excluded_columns = ['created_at', 'updated_at']
    form_include_relationships = True

    async def on_model_change(
            self,
            data: dict,
            model: Any,
            is_created: bool,
            request=None
    ) -> None:
        """Perform actions before model is created/updated."""
        await super().on_model_change(data, model, is_created, request)

    async def after_model_change(
            self,
            data: dict,
            model: Any,
            is_created: bool,
            request=None
    ) -> None:
        """Perform actions after model is created/updated."""
        await super().after_model_change(data, model, is_created, request)
