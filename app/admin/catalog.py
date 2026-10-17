"""Narrow catalog editors: immutable identities, platform-admin-only writes."""

from typing import Any

from fastapi import HTTPException
from sqladmin.authorization import Action
from starlette.requests import Request

from app.admin.base import BaseAdmin
from app.admin.csrf import get_csrf_manager
from app.core.permissions import permission_scope
from app.models import PlatformRole, User


class CatalogEditAdmin(BaseAdmin):
    can_create = False
    can_delete = False
    can_edit = True
    can_export = False

    def has_permission(self, request: Request, action: str, obj: Any | None = None) -> bool:
        if action == Action.EDIT:
            actor = self.get_admin_user(request)
            if actor is None or not actor.is_platform_admin:
                return False
        return super().has_permission(request, action, obj)

    async def require_catalog_edit(self, request: Request) -> User:
        """Recheck the actor and CSRF even when called outside the CRUD route."""
        admin = self._admin_ref
        actor = await admin.authorization_backend.resolve_user(request)
        fresh = (
            await User.objects.prefetch_related("role").get(id=actor.id, is_active=True) if actor is not None else None
        )
        if fresh is None or not fresh.is_platform_admin:
            raise HTTPException(status_code=403, detail="Нужны права администратора платформы")
        manager = getattr(admin.authentication_backend, "csrf_manager", None) or get_csrf_manager(request)
        form = await request.form()
        token = form.get("csrf_token")
        if not isinstance(token, str) or not manager.verify_token(token):
            raise HTTPException(status_code=403, detail="Недействительный CSRF токен")
        return fresh


class OperatorGrantAdmin(CatalogEditAdmin):
    catalog_edit_notice = (
        "Изменяются только разрешения оператора. Коды системных ролей, назначения пользователей "
        "и полный доступ администратора платформы защищены от изменения."
    )

    def has_permission(self, request: Request, action: str, obj: Any | None = None) -> bool:
        if action == Action.EDIT and obj is not None and obj.codename != "operator":
            return False
        return super().has_permission(request, action, obj)

    async def update_model(self, request: Request, pk: str, data: dict) -> Any:
        actor = await self.require_catalog_edit(request)
        if set(data) != {"permissions"}:
            raise HTTPException(status_code=422, detail="Разрешено менять только права оператора")
        role = await PlatformRole.objects.get(id=int(pk))
        if role is None or role.codename != "operator":
            raise HTTPException(status_code=403, detail="Изменяются только разрешения оператора")
        # Use the canonical association writer, with an explicit actor context.
        # Never let SQLAdmin replace role/users relationships through generic ORM CRUD.
        with permission_scope(actor):
            await PlatformRole.objects.set_permissions(role.id, [int(value) for value in data["permissions"]])
        return await PlatformRole.objects.get_with_permissions(role.id)


class PermissionLabelAdmin(CatalogEditAdmin):
    catalog_edit_notice = (
        "Изменяется только отображаемое название разрешения. Ресурс, действие и код права "
        "задаются системным каталогом. Выдавайте права через роль «Оператор»."
    )

    async def update_model(self, request: Request, pk: str, data: dict) -> Any:
        await self.require_catalog_edit(request)
        if set(data) != {"name"} or not isinstance(data["name"], str) or not data["name"].strip():
            raise HTTPException(status_code=422, detail="Укажите название разрешения")
        return await super().update_model(request, pk, {"name": data["name"].strip()})
