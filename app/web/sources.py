"""Sources CRUD for `/app/sources` (docs/design/ui.md §4.3).

M2: list + add + edit + toggle. Sources are scoped to the current workspace.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models.source import Source
from app.types import SourceType

from .deps import action_tenant_id, add_flash, ensure_csrf, guard_web, render, safe_next, tenant_filter_context

router = APIRouter(prefix="/sources")


async def _platforms():
    from app.models.platform import Platform

    return await Platform.objects.order_by(Platform.id)


async def _resolve_platform(form_value: str):
    from app.models.platform import Platform
    from app.types import PlatformType

    pt = PlatformType.get_by_value(form_value)
    if pt is None:
        return None
    return await Platform.objects.filter(platform_type=pt).first()


@router.get("")
@router.get("/")
async def sources_list(request: Request):
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    raw_scenario = request.query_params.get("scenario_id")
    filter_scenario_id = int(raw_scenario) if raw_scenario and raw_scenario.isdigit() else None

    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    # Scenarios for the filter dropdown — scoped to the workspace being viewed.
    if is_superuser:
        with tenant_scope(filter_tenant_id):
            scenarios = list(await AgentScenario.objects.order_by(AgentScenario.name))
    else:
        scenarios = list(
            await AgentScenario.objects.filter(tenant_id=request.state.tenant_id).order_by(AgentScenario.name)
        )

    if is_superuser:
        # Superuser sees all tenants' sources, optionally narrowed to one.
        # Bypass the tenant guard (the middleware already set a tenant scope) so
        # the manager query isn't silently scoped to the active workspace.
        with tenant_scope(bypass=True):
            query = Source.objects.select_related("platform", "tenant")
            if filter_tenant_id is not None:
                query = query.filter(tenant_id=filter_tenant_id)
            if filter_scenario_id is not None:
                query = query.filter(agent_scenario_id=filter_scenario_id)
            sources = await query.order_by(Source.created_at.desc())
    else:
        query = Source.objects.filter(tenant_id=request.state.tenant_id)
        if filter_scenario_id is not None:
            query = query.filter(agent_scenario_id=filter_scenario_id)
        sources = await query.select_related("platform").order_by(Source.created_at.desc())
    user_sources = await (
        Source.objects.filter(tenant_id=request.state.tenant_id, source_type=SourceType.USER)
        .select_related("platform")
        .order_by(Source.name, Source.id)
    )
    user_sources_data = [
        {"id": s.id, "name": s.name or s.external_id, "platform": s.platform.platform_type.db_value}
        for s in user_sources
    ]
    return render(
        request,
        "web/sources.html",
        section="sources",
        sources=sources,
        platforms=await _platforms(),
        source_types=list(SourceType),
        user_sources_json=user_sources_data,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
        scenarios=scenarios,
        filter_scenario_id=filter_scenario_id,
    )


@router.post("")
async def source_add(
    request: Request,
    name: str = Form(""),
    platform: str = Form(...),
    external_id: str = Form(...),
    source_type: str = Form(...),
    monitored_users: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
    # The onboarding wizard posts here too — land back on the page that was
    # filled in (validating, so `?next=` cannot become an open redirect).
    next: str = Form(""),
):
    back = safe_next(next) or "/app/sources"

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "create", back=back)
    if denied is not None:
        return denied

    platform_row = await _resolve_platform(platform)
    if platform_row is None:
        add_flash(request, "error", f"Платформа '{platform}' не найдена")
        return RedirectResponse(back, status_code=302)

    st = SourceType.get_by_value(source_type)
    if st is None:
        add_flash(request, "error", f"Тип источника '{source_type}' не найден")
        return RedirectResponse(back, status_code=302)

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        existing = await Source.objects.get(
            tenant_id=tenant_id,
            platform_id=platform_row.id,
            external_id=external_id,
        )
        if existing is not None:
            add_flash(request, "error", "Источник с таким ID уже существует")
            return RedirectResponse(back, status_code=302)

        clean_name = name.strip()[:100] or f"{platform}:{external_id}"

        # Parse monitored users from comma-separated string into params
        params: dict = {}
        if monitored_users.strip():
            params["monitored_users"] = [u.strip() for u in monitored_users.split(",") if u.strip()]

        await Source.objects.create(
            name=clean_name,
            platform_id=platform_row.id,
            external_id=external_id.strip()[:200],
            source_type=st,
            is_active=True,
            params=params,
        )

    add_flash(request, "success", f"Источник '{clean_name}' добавлен")
    return RedirectResponse(back, status_code=302)


@router.post("/{source_id}")
async def source_edit(
    request: Request,
    source_id: int,
    name: str = Form(""),
    platform: str = Form(...),
    external_id: str = Form(...),
    source_type: str = Form(...),
    is_active: str = Form("", alias="is_active"),
    monitored_users: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/sources", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "update", back="/app/sources")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        platform_row = await _resolve_platform(platform)
        if platform_row is None:
            add_flash(request, "error", f"Платформа '{platform}' не найдена")
            return RedirectResponse("/app/sources", status_code=302)

        st = SourceType.get_by_value(source_type)
        if st is None:
            add_flash(request, "error", f"Тип источника '{source_type}' не найден")
            return RedirectResponse("/app/sources", status_code=302)

        dup = await Source.objects.get(
            tenant_id=tenant_id,
            platform_id=platform_row.id,
            external_id=external_id,
        )
        if dup is not None and dup.id != source.id:
            add_flash(request, "error", "Источник с таким ID уже существует")
            return RedirectResponse("/app/sources", status_code=302)

        # Parse monitored users from comma-separated string into params
        params = source.params.copy() if source.params else {}
        if monitored_users.strip():
            params["monitored_users"] = [u.strip() for u in monitored_users.split(",") if u.strip()]
        else:
            params.pop("monitored_users", None)

        await Source.objects.update_by_id(
            source.id,
            name=name.strip()[:100] or source.name,
            platform_id=platform_row.id,
            external_id=external_id.strip()[:200],
            source_type=st,
            is_active=is_active == "on",
            params=params,
        )

    add_flash(request, "success", "Источник обновлён")
    return RedirectResponse("/app/sources", status_code=302)


@router.post("/{source_id}/toggle")
async def source_toggle(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/sources", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "update", back="/app/sources")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        await Source.objects.update_by_id(source.id, is_active=not source.is_active)
    action = "активирован" if not source.is_active else "деактивирован"
    add_flash(request, "success", f"Источник '{source.name}' {action}")
    return RedirectResponse("/app/sources", status_code=302)
