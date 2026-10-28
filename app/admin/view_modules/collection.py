"""Collection SQLAdmin views."""

from __future__ import annotations

from ._shared import (
    BaseAdmin,
    CollectedItem,
    format_date,
    logger,
    logging,
)

logger = logging.getLogger(__name__)


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


__all__ = [name for name in globals() if not name.startswith("__")]
