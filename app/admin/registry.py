"""Owner-only entry point into the read-only table registry."""

from urllib.parse import urlencode

from sqladmin import BaseView, expose
from starlette.requests import Request

from app.admin.table_registry import build_table_registry, filter_tables, find_table, paginate_tables
from app.models import Base

PAGE_SIZES = (25, 50, 100, 200)


class TableRegistryAdmin(BaseView):
    """Every table, no writes: the owner's map of the platform data.

    Gated on `is_accessible` — the owner alone — not on model permissions: the
    point of the surface is to see tables that no grant covers, and an admin
    carrying the `( *, * )` wildcard must still be refused. The view declares
    no `crud` and no `@action`, so SQLAdmin has no create/edit/delete/export
    route to reach.
    """

    name = "Реестр таблиц"
    name_plural = "Реестр таблиц"
    icon = "fa fa-database"
    category = "Система"
    category_icon = "fa fa-database"

    def _is_owner(self, request: Request) -> bool:
        user = request.state.admin_user if getattr(request.state, "admin_user", None) is not None else None
        return bool(user is not None and getattr(user, "is_superuser", False))

    def is_accessible(self, request: Request) -> bool:
        return self._is_owner(request)

    def is_visible(self, request: Request) -> bool:
        return self._is_owner(request)

    @property
    def _route_prefix(self) -> str:
        """Mount path of the console; templates need absolute hrefs.

        The registry lives on the console's own router, and that router is
        mounted under `base_url` — so a URL minted from the app cannot find
        `admin:view-...`, and a relative one would resolve against `/registry`.
        """
        admin = getattr(type(self), "_admin_ref", None)
        return (getattr(admin, "base_url", "") or "").rstrip("/")

    def url(self, **params) -> str:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        return f"{self._route_prefix}/registry" + (f"?{query}" if query else "")

    @expose("/registry", methods=["GET"])
    async def table_registry(self, request: Request):
        table = request.query_params.get("table")
        if table:
            item = find_table(build_table_registry(Base.metadata), table)
            if item is None:
                return await self.templates.TemplateResponse(
                    request,
                    "sqladmin/table_registry.html",
                    {
                        "page": paginate_tables([], 1, PAGE_SIZES[1]),
                        "page_rows": [],
                        "model_view": self,
                        "query": table,
                        "registry_url": self.url(),
                        "title": self.name,
                        "subtitle": "Таблица не найдена",
                    },
                    status_code=404,
                )
            return await self.templates.TemplateResponse(
                request,
                "sqladmin/table_registry_detail.html",
                {
                    "item": item,
                    "model_view": self,
                    "registry_url": self.url(),
                    "title": f"{self.name}: {item['table']}",
                    "subtitle": "Структура таблицы",
                },
            )

        query = request.query_params.get("q", "")
        try:
            size = int(request.query_params.get("size", PAGE_SIZES[1]))
            page_number = int(request.query_params.get("page", 1))
        except ValueError:
            size, page_number = PAGE_SIZES[1], 1
        if size not in PAGE_SIZES:
            size = PAGE_SIZES[1]
        tables = filter_tables(build_table_registry(Base.metadata), query)
        page = paginate_tables(tables, page_number, size)
        return await self.templates.TemplateResponse(
            request,
            "sqladmin/table_registry.html",
            {
                "page": page,
                "page_rows": page["items"],
                "model_view": self,
                "query": query,
                "registry_url": self.url(),
                "title": self.name,
                "subtitle": "Все таблицы платформы, только чтение",
            },
        )
