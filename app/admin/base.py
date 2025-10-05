from datetime import datetime
from typing import Any

from sqladmin import ModelView


class BaseAdmin(ModelView):
    """Base admin view with common configurations."""

    # Common settings
    icon = "fa fa-table"
    page_size = 50
    page_size_options = [25, 50, 100, 200]
    save_as = True

    form_include_relationships = True
    form_excluded_columns = ['created_at', 'updated_at']

    column_formatters = {
        "role": lambda m, a: m.role.name.upper() if m.role else "",
        "updated_at": lambda m, a: m.updated_at.strftime("%d.%m.%Y") if m.updated_at else "",
        "created_at": lambda m, a: m.created_at.strftime("%d.%m.%Y") if m.updated_at else "",
    }

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
