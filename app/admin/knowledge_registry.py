"""Permission-filtered, read-only indexes of evidence and document revisions."""

from urllib.parse import urlencode

from fastapi import HTTPException
from sqladmin import BaseView, expose
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from app.admin.file_catalog import _number
from app.admin.scope import AdminScopeMiddleware
from app.core.permissions import has_permission, permission_scope
from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models import DocumentRevision, KnowledgeDocument, KnowledgeSnippet
from app.services.knowledge.documents import STATUS_LABELS
from app.types import ActionType


class KnowledgeRegistryAdmin(BaseView):
    name = "Материалы и версии"
    name_plural = "Материалы и версии"
    icon = "fa fa-book"
    category = "Источники и сбор"
    category_icon = "fa fa-rss"

    def is_accessible(self, request: Request) -> bool:
        actor = getattr(request.state, "admin_user", None)
        return bool(
            actor and actor.is_active and actor.has_admin_access() and actor.has_perm_for("source", ActionType.VIEW)
        )

    def is_visible(self, request: Request) -> bool:
        return self.is_accessible(request)

    @expose("/knowledge-registry", methods=["GET"])
    async def knowledge_registry(self, request: Request):
        if not self.is_accessible(request):
            raise HTTPException(403, "Доступ к материалам запрещён")
        actor = request.state.admin_user
        params = request.query_params
        kind = params.get("kind", "snippets")
        boundary = params.get("boundary", "cloud")
        after = _number(params.get("after"), default=0, minimum=0)
        if kind not in {"snippets", "documents", "revisions"} or boundary not in {"cloud", "company", "local_only"}:
            raise HTTPException(400, "Некорректный запрос материалов")
        tenant_id = current_tenant_id()
        owner_grants = None
        if is_bypass():
            raw = request.session.get("app_tenant_id")
            try:
                tenant_id = int(raw) if raw is not None and not isinstance(raw, bool) else None
            except (TypeError, ValueError):
                tenant_id = None
            if type(tenant_id) is int and tenant_id > 0:
                memberships = await AdminScopeMiddleware._memberships(actor, wanted_id=tenant_id, only_active=True)
                owner_grants = next((grants for member_id, grants in memberships if member_id == tenant_id), None)
            if owner_grants is None:
                raise HTTPException(403, "Выберите доступное рабочее пространство")
        if type(tenant_id) is not int or tenant_id <= 0:
            raise HTTPException(403, "Выберите доступное рабочее пространство")
        model = {"snippets": KnowledgeSnippet, "documents": KnowledgeDocument, "revisions": DocumentRevision}[kind]
        try:
            if owner_grants is not None:
                with tenant_scope(tenant_id), permission_scope(actor, member_grants=owner_grants):
                    records = await self._read(model, boundary, after, actor)
            else:
                records = await self._read(model, boundary, after, actor)
        except DBAPIError:
            raise HTTPException(503, "Материалы временно недоступны") from None
        next_url = None
        if len(records) > 50:
            next_url = "?" + urlencode({"kind": kind, "boundary": boundary, "after": records[49].id})
        rows = []
        context_labels = {
            "knowledge": "База знаний",
            "communication": "Коммуникация",
            "followup": "Поручение",
            "case": "Дело",
        }
        boundary_labels = {"cloud": "Облако", "company": "Компания", "local_only": "Только локально"}
        for item in records[:50]:
            if kind == "snippets":
                rows.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "type": context_labels.get(item.context_type, "Неизвестно"),
                        "boundary": boundary_labels.get(item.data_boundary, "Неизвестно"),
                    }
                )
            elif kind == "documents":
                rows.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "counterparty": item.counterparty,
                        "boundary": boundary_labels.get(item.data_boundary, "Неизвестно"),
                    }
                )
            else:
                rows.append(
                    {
                        "id": item.id,
                        "document_id": item.document_id,
                        "version": item.version,
                        "status": STATUS_LABELS.get(item.status, "Неизвестно"),
                    }
                )
        response = await self.templates.TemplateResponse(
            request,
            "sqladmin/knowledge_registry.html",
            {
                "title": self.name,
                "model_view": self,
                "kind": kind,
                "boundary": boundary,
                "rows": rows,
                "next_url": next_url,
            },
        )
        response.headers.update(
            {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}
        )
        return response

    @staticmethod
    async def _read(model, boundary, after, actor):
        if not has_permission(actor, "source", ActionType.VIEW):
            raise HTTPException(403, "Доступ к материалам запрещён")
        return await model.objects.visible(boundary).filter(model.id > after).order_by(model.id).limit(51)
