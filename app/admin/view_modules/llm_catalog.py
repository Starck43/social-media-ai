"""Llm Catalog SQLAdmin views."""

from __future__ import annotations

from ._shared import (
    ActionType,
    Any,
    APIFormatType,
    BaseAdmin,
    CommaSeparatedSelectMultipleField,
    ExactModelPriceField,
    FloatField,
    Form,
    HTTPException,
    LLMClientFactory,
    LLMModel,
    LLMModelActions,
    LLMProvider,
    RedirectResponse,
    Request,
    Select,
    SelectField,
    Union,
    action,
    expose,
    get_model_form,
    hide_form_secret,
    logger,
    logging,
    model_price,
    seal_form_secret,
    slugify_uid,
    validators,
    widgets,
)

logger = logging.getLogger(__name__)


class LLMProviderAdmin(BaseAdmin, model=LLMProvider):
    name = "Провайдер ИИ"
    name_plural = "Провайдеры ИИ"
    icon = "fa fa-server"
    category = "ИИ и аналитика"
    category_icon = "fa fa-robot"
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
            "description": "Шифруется до записи в БД. Пустое поле сохраняет текущий ключ — вводите только для замены",
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

        # The key is sealed here, before SQLAdmin writes the row. A blank field
        # keeps the stored key, so an edit that only touches the name does not
        # need the secret retyped.
        seal_form_secret(data, "encrypted_api_key", getattr(model, "encrypted_api_key", None))

        with self._catalog_scope(request):
            await super().on_model_change(data, model, is_created, request)

    async def get_form_data_for_edit(self, obj: Any) -> dict[str, Any]:
        """Never render stored ciphertext back into the edit form."""
        data = await super().get_form_data_for_edit(obj)
        hide_form_secret(data, "encrypted_api_key")
        return data

    @action(name="test-connection", label="🔌 Проверить подключение", add_in_list=True, add_in_detail=True)
    async def test_connection_action(self, request: Request):
        """Render the confirmation step; only the CSRF-checked POST calls the provider."""
        from app.admin.actions import render_action_confirmation

        pks = request.query_params.get("pks", "")
        if not pks:
            request.session["admin_message"] = {"type": "error", "message": "Не выбран провайдер"}
            return RedirectResponse(request.url_for("admin:list", identity=self.identity))

        with self._catalog_scope(request):
            provider = await LLMProvider.objects.get(id=int(pks.split(",")[0]))
        if provider is None:
            request.session["admin_message"] = {"type": "error", "message": "Не выбран провайдер"}
            return RedirectResponse(request.url_for("admin:list", identity=self.identity))

        return await render_action_confirmation(
            self,
            request,
            title="Проверить подключение провайдера",
            message=(f"Будет отправлен короткий запрос через «{provider.name}». " "Это списывает токены провайдера."),
            submit_label="Проверить",
            cancel_url=request.url_for("admin:list", identity=self.identity),
        )

    @expose("/action/test-connection", methods=["POST"])
    async def test_connection_submit(self, request: Request):
        """Execute the confirmed provider probe; the only paid entry point."""
        from app.admin.actions import verified_action_form

        form = await verified_action_form(request)
        pks = str(form.get("pks", ""))
        if not pks:
            request.session["admin_message"] = {"type": "error", "message": "Не выбран провайдер"}
            return RedirectResponse(request.url_for("admin:list", identity=self.identity))
        with self._catalog_scope(request):
            return await self._test_connection(request, int(pks.split(",")[0]))

    async def _test_connection(self, request: Request, provider_id: int):
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
        # The API key is already sealed in `on_model_change`; nothing to rewrite
        # after the commit.
        with self._catalog_scope(request):
            await super().after_model_change(data, model, is_created, request)


def _catalog_rate(value, token_unit: int) -> str:
    unit_label = "1K" if token_unit == 1000 else "1M"
    return f"{model_price(value)} / {unit_label}"


class LLMModelAdmin(BaseAdmin, model=LLMModel):
    name = "Модель ИИ"
    name_plural = "Модели ИИ"
    icon = "fa fa-microchip"
    category = "ИИ и аналитика"
    category_icon = "fa fa-robot"
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


__all__ = [name for name in globals() if not name.startswith("__")]
