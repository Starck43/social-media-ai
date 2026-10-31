"""Users SQLAdmin views."""

from __future__ import annotations

from sqladmin import expose

from ._shared import (
    ActionType,
    Any,
    BaseAdmin,
    FilteredSelectMultipleField,
    HTTPException,
    Permission,
    PermissionLabelAdmin,
    PlatformRole,
    PlatformRoleGrantAdmin,
    PlatformRoleType,
    RedirectResponse,
    Request,
    RoleChoiceField,
    RolePickerConverter,
    Tenant,
    User,
    UserCredential,
    UserManager,
    action,
    get_model_form,
    get_password_hash,
    hide_form_secret,
    logger,
    logging,
    plan_badge,
    role_choices,
    role_edit,
    seal_form_secret,
    validators,
)

logger = logging.getLogger(__name__)


class UserAdmin(BaseAdmin, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa fa-user"
    category = "Пользователи и доступ"
    category_icon = "fa fa-users"

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
    category = "Пользователи и доступ"
    category_icon = "fa fa-users"
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
    category = "Пользователи и доступ"
    category_icon = "fa fa-users"
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


class TenantAdmin(BaseAdmin, model=Tenant):
    name = "Рабочее пространство"
    name_plural = "Рабочие пространства"
    icon = "fa fa-building"
    category = "Пользователи и доступ"
    category_icon = "fa fa-users"

    inline_columns = ["id", "name", "slug", "plan", "is_active"]
    column_list = [
        "id",
        "name",
        "slug",
        "plan",
        "plan_expires_at",
        "entitlement_policy",
        "timezone",
        "is_active",
        "max_sources",
        "daily_cost_limit",
        "updated_at",
    ]
    column_details_list = column_list + ["agent_style"]
    column_searchable_list = ["name", "slug"]
    column_sortable_list = ["name", "slug", "plan", "is_active"]
    column_labels = dict(
        {
            "id": "ID",
            "name": "Название",
            "slug": "Слаг",
            "plan": "Тариф",
            "plan_expires_at": "Plan expires at (UTC)",
            "entitlement_policy": "Product capabilities (static policy)",
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

    can_delete = False
    action_permissions = {"offboard": ActionType.DELETE}

    async def delete_model(self, request: Request, pk: Any) -> Any:
        """A legacy CRUD call cannot bypass the reviewed offboarding service."""
        raise HTTPException(409, "Use the confirmed workspace offboarding action")

    @action(name="offboard", label="Удалить с аудитом", add_in_list=True, add_in_detail=True)
    async def offboard_action(self, request: Request):
        from app.admin.offboarding import offboarding_page

        return await offboarding_page(self, request)

    @expose("/action/offboard", methods=["POST"])
    async def offboard_submit(self, request: Request):
        from app.admin.offboarding import offboarding_page

        return await offboarding_page(self, request, submit=True)

    @expose("/offboarding-journal", methods=["GET"])
    async def offboarding_journal(self, request: Request):
        from app.admin.offboarding import offboarding_journal

        return await offboarding_journal(self, request)

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
    category = "Пользователи и доступ"
    category_icon = "fa fa-users"

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
            "description": "Шифруется до записи в БД. Пустое поле сохраняет текущий секрет — вводите только для замены",
        },
        "expires_at": {"label": "Действует до", "description": "Оставьте пустым, если срок неизвестен"},
        **BaseAdmin.form_args,
    }

    async def on_model_change(self, data: dict, model: UserCredential, is_created: bool, request=None) -> None:
        """Seal the secret before the row is written, not after it.

        A blank field keeps the stored secret: the edit form never renders the
        ciphertext, so an operator changing only the label must not be forced to
        retype the token.
        """
        seal_form_secret(data, "secret_encrypted", getattr(model, "secret_encrypted", None))
        await super().on_model_change(data, model, is_created, request)

    async def get_form_data_for_edit(self, obj: Any) -> dict[str, Any]:
        """Never render stored ciphertext back into the edit form."""
        data = await super().get_form_data_for_edit(obj)
        hide_form_secret(data, "secret_encrypted")
        return data


__all__ = [name for name in globals() if not name.startswith("__")]
