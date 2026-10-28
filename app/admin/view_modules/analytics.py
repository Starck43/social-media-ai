"""Analytics SQLAdmin views."""

from __future__ import annotations

from ._shared import (
    logging,
    AIAnalytics,
    ActionType,
    BaseAdmin,
    PeriodType,
    RedirectResponse,
    Request,
    SelectField,
    action,
    format_usd,
    logger,
    validators,
)


logger = logging.getLogger(__name__)


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

__all__ = [name for name in globals() if not name.startswith("__")]
