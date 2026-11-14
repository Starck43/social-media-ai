"""Superuser-only dormant grant editor; all writes use the catalog service."""

from fastapi import HTTPException
from sqladmin.authorization import Action
from starlette.requests import Request
from wtforms import IntegerField, SelectField, validators

from app.admin.authorization import bound_console_request
from app.admin.base import BaseAdmin
from app.admin.csrf import verify_request_token
from app.core.permissions import permission_scope
from app.models import PlanModelGrant, Tenant
from app.models.llm.plan_model_grant import GRANT_CAPABILITIES
from app.services.llm_catalog.catalog_admin import platform_model_choices, save_grant


class PlanModelGrantAdmin(BaseAdmin, model=PlanModelGrant):
    name = "Модель тарифа"
    name_plural = "Модели тарифов (не активированы)"
    category = "ИИ и аналитика"
    icon = "fa fa-list-check"
    can_delete = False
    can_export = False
    inline_exclude = True
    column_list = ["id", "plan", "capability", "llm_model_id", "priority", "is_active", "is_default"]
    column_details_list = column_list
    column_sortable_list = ["plan", "capability", "priority", "llm_model_id"]
    column_default_sort = [("plan", False), ("capability", False), ("priority", False), ("llm_model_id", False)]
    form_columns = ["plan", "capability", "llm_model_id", "priority", "is_active", "is_default"]
    form_include_pk = True
    form_overrides = {
        **BaseAdmin.form_overrides,
        "plan": SelectField,
        "capability": SelectField,
        "llm_model_id": SelectField,
        "priority": IntegerField,
    }
    form_args = {
        **BaseAdmin.form_args,
        "plan": {"choices": [(code, code) for code in Tenant.PLANS]},
        "capability": {"choices": [(code, code) for code in GRANT_CAPABILITIES]},
        "llm_model_id": {"choices": [], "coerce": int},
        "priority": {"default": 100, "validators": [validators.NumberRange(min=0, max=2**31 - 1)]},
    }

    def is_accessible(self, request: Request) -> bool:
        actor = self.get_admin_user(request)
        return bool(actor is not None and actor.is_active and actor.is_superuser)

    def is_visible(self, request: Request) -> bool:
        return self.is_accessible(request)

    def has_permission(self, request: Request, action: str, obj=None) -> bool:
        return (
            self.is_accessible(request)
            and action not in {Action.DELETE, Action.EXPORT}
            and super().has_permission(request, action, obj)
        )

    async def scaffold_form(self, rules=None):
        request = bound_console_request()
        actor = self.get_admin_user(request) if request is not None else None
        if actor is None or not self.is_accessible(request):
            raise HTTPException(status_code=403, detail="Нужны права собственника платформы")
        with permission_scope(actor):
            async with PlanModelGrant.objects._get_owned_session() as session:
                choices = await platform_model_choices(session, actor_user_id=actor.id, include_inactive=True)
        # Build a fresh class: never freeze the first request's choices in the
        # shared SQLAdmin instance, and never load the secret-bearing model relation.
        from sqladmin.forms import get_model_form

        return await get_model_form(
            model=self.model,
            session_maker=self.session_maker,
            only=self._form_prop_names,
            column_labels=self._column_labels,
            form_args={
                **self.form_args,
                "llm_model_id": {
                    "choices": [(row["id"], f'{row["provider_name"]}/{row["model_id"]}') for row in choices],
                    "coerce": int,
                },
            },
            form_overrides=self._form_overrides,
            form_include_pk=True,
            form_converter=self.form_converter,
        )

    async def _save(self, request: Request, data: dict, grant_id=None):
        actor = self.get_admin_user(request)
        if not self.is_accessible(request):
            raise HTTPException(status_code=403, detail="Нужны права собственника платформы")
        form = await request.form()
        token = form.get("csrf_token")
        if not isinstance(token, str) or not verify_request_token(request, token):
            raise HTTPException(status_code=403, detail="Недействительный CSRF токен")
        if set(data) != set(self.form_columns):
            raise HTTPException(status_code=422, detail="Разрешены только поля назначения модели тарифа")
        from sqlalchemy.exc import IntegrityError

        try:
            with permission_scope(actor):
                async with PlanModelGrant.objects._get_owned_session() as session:
                    return await save_grant(
                        session,
                        actor_user_id=actor.id,
                        grant_id=grant_id,
                        model_id=data["llm_model_id"],
                        **{key: data[key] for key in self.form_columns if key != "llm_model_id"},
                    )
        except PermissionError:
            raise HTTPException(status_code=403, detail="Нужны права собственника платформы") from None
        except (ValueError, IntegrityError):
            raise HTTPException(
                status_code=422, detail="Недопустимое или конфликтующее назначение модели тарифа"
            ) from None

    async def insert_model(self, request: Request, data: dict):
        return await self._save(request, data)

    async def update_model(self, request: Request, pk: str, data: dict):
        return await self._save(request, data, grant_id=int(pk))

    async def delete_model(self, request: Request, pk: str):
        raise HTTPException(status_code=403, detail="Отключите назначение вместо удаления")
