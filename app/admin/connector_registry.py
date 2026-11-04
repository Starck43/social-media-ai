"""Read-only connector metadata through the existing actor-scoped authorities."""

from fastapi import HTTPException
from sqladmin import BaseView, expose
from starlette.requests import Request

from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models.managers.workspace_connection_manager import ConnectionDenied, workspace_connections
from app.services.social.connector_authorizations import connector_authorizations


class ConnectorRegistryAdmin(BaseView):
    """Show only the signed-in operator's connector metadata, never secrets."""

    name = "Личные подключения"
    name_plural = "Личные подключения"
    icon = "fa fa-link"
    category = "Пользователи и доступ"
    category_icon = "fa fa-users"

    def is_accessible(self, request: Request) -> bool:
        actor = getattr(request.state, "admin_user", None)
        return bool(actor and actor.is_active and actor.has_admin_access())

    def is_visible(self, request: Request) -> bool:
        return self.is_accessible(request)

    @expose("/personal-connectors", methods=["GET"])
    async def personal_connectors(self, request: Request):
        if not self.is_accessible(request):
            raise HTTPException(403, "Доступ к подключениям запрещён")
        actor = request.state.admin_user
        if type(actor.id) is not int or actor.id <= 0:
            raise HTTPException(403, "Доступ к подключениям запрещён")

        try:
            with tenant_scope():
                accounts = await connector_authorizations.list_personal(actor_id=actor.id)
        except ConnectionDenied:
            raise HTTPException(403, "Доступ к подключениям запрещён") from None

        # Owners normally run in global bypass; select a workspace only from the
        # authenticated session and let the manager verify fresh membership.
        tenant_id = current_tenant_id()
        if is_bypass():
            raw = request.session.get("app_tenant_id")
            try:
                tenant_id = int(raw) if raw is not None and not isinstance(raw, bool) else None
            except (TypeError, ValueError):
                tenant_id = None
        connections = []
        grants = []
        workspace_available = False
        if type(tenant_id) is int and tenant_id > 0:
            try:
                with tenant_scope(tenant_id):
                    connections, _ = await workspace_connections.list_for_actor(tenant_id=tenant_id, actor_id=actor.id)
                workspace_available = True
            except ConnectionDenied:
                # A stale selected workspace grants no information about it.
                pass
            if workspace_available:
                try:
                    with tenant_scope(tenant_id):
                        grants = await connector_authorizations.list_grants(tenant_id=tenant_id, actor_id=actor.id)
                except ConnectionDenied:
                    # Grant listing has a stricter source-view requirement.
                    grants = []

        account_states = {"active": "Активна", "reauth": "Требуется повторный вход", "revoked": "Отозвана"}
        connection_states = {
            "unverified": "Не проверено",
            "verified": "Проверено",
            "error": "Ошибка проверки",
            "reauth": "Требуется повторный вход",
            "disabled": "Отключено",
        }
        # Re-project even the service's safe metadata: never give templates
        # subject keys, account logins, connection configuration or secrets.
        account_rows = [
            {
                "id": row["authorization_id"],
                "service": "Телеграм",
                "status": account_states.get(row["state"], "Неизвестно"),
            }
            for row in accounts
        ]
        connection_rows = [
            {
                "id": row["id"],
                "label": row["label"],
                "service": row["provider_label"],
                "status": connection_states.get(row["state"], "Неизвестно"),
            }
            for row in connections
        ]
        response = await self.templates.TemplateResponse(
            request,
            "sqladmin/personal_connectors.html",
            {
                "title": self.name,
                "model_view": self,
                "accounts": account_rows,
                "connections": connection_rows,
                "grants": [
                    {"id": row["grant_id"], "authorization_id": row["authorization_id"], "active": row["is_active"]}
                    for row in grants
                ],
                "workspace_available": workspace_available,
            },
        )
        response.headers["Cache-Control"] = "no-store"
        return response
