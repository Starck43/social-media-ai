"""Scenarios SQLAdmin views."""

from __future__ import annotations

from ._shared import (
    ActionType,
    AgentScenario,
    AnalysisType,
    Any,
    BaseAdmin,
    LLMStrategyType,
    MediaType,
    RedirectResponse,
    Request,
    SelectField,
    action,
    expose,
    json,
    logger,
    logging,
    validators,
)

logger = logging.getLogger(__name__)


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
        template_dirs = [str(Path(__file__).resolve().parents[2] / "templates"), str(sqladmin_path / "templates")]
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
        """Render the confirmation step; only the CSRF-checked POST toggles."""
        from app.admin.actions import render_action_confirmation

        pks = request.query_params.get("pks", "")
        if not pks:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity))

        names = []
        for pk in pks.split(","):
            try:
                scenario = await AgentScenario.objects.get(id=int(pk))
            except Exception as e:
                logger.error(f"Error loading scenario {pk}: {e}")
                continue
            if scenario:
                names.append(scenario.name)
        if not names:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity))

        return await render_action_confirmation(
            self,
            request,
            title="Изменить активность сценариев",
            message="Будет переключён признак активности: " + ", ".join(f"«{name}»" for name in names),
            submit_label="Переключить",
            cancel_url=request.url_for("admin:list", identity=self.identity),
        )

    @expose("/action/toggle-active", methods=["POST"])
    async def toggle_active_submit(self, request: Request):
        """Execute the confirmed toggle; the only state-changing entry point."""
        from app.admin.actions import verified_action_form

        form = await verified_action_form(request)
        pks = str(form.get("pks", ""))
        if not pks:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity))

        await self._toggle_scenarios(pks)
        return RedirectResponse(
            url=request.url_for("admin:list", identity=self.identity),
            status_code=303,
        )

    async def _toggle_scenarios(self, pks: str) -> None:
        """Toggle every selected scenario; the caller has already authorised it."""
        for pk in pks.split(","):
            try:
                scenario = await AgentScenario.objects.get(id=int(pk))
                if scenario:
                    new_status = not scenario.is_active
                    await AgentScenario.objects.update_by_id(int(pk), is_active=new_status)
                    logger.info(f"Scenario {pk} status changed to {new_status}")
            except Exception as e:
                logger.error(f"Error toggling scenario {pk}: {e}")


__all__ = [name for name in globals() if not name.startswith("__")]
