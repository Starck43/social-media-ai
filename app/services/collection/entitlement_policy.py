"""Collection-owned scope resolution; generic entitlement code knows no sources."""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass


async def required_capabilities(session, payload, *, sources=None):
    """Derive collection-owned product rights from effective source scope."""
    from app.models import Source
    from app.services.collection.entitlements import intent_capabilities, source_capabilities

    tenant_id = current_tenant_id()
    if type(tenant_id) is not int or tenant_id <= 0 or is_bypass():
        raise TenantContextError("Collection entitlement scope requires a concrete workspace")
    plan = payload.get("task_plan") or {}
    if sources is None:
        query = select(Source).where(Source.tenant_id == tenant_id, Source.is_active.is_(True))
        scope = plan.get("source_scope")
        ids = plan.get("source_ids") if scope == "selected" else payload.get("source_ids")
        if ids:
            query = query.where(Source.id.in_(ids))
        elif scope in {"selected", "discovery"}:
            return intent_capabilities(payload)
        else:
            query = query.where(Source.owner_task_id.is_(None))
        sources = list((await session.execute(query.options(selectinload(Source.platform)))).scalars())
    if any(source.tenant_id != tenant_id for source in sources):
        raise TenantContextError("Entitlement source scope contains a foreign workspace source")
    if plan.get("platforms"):
        from app.jobs.source_activity import source_in_task_platform_scope

        sources = [source for source in sources if source_in_task_platform_scope(source, set(plan["platforms"]))]
    # Match the handler's legacy population rule: VK wins, otherwise the
    # first linked platform; an unlinked legacy task defaults to VK.
    platform_keys = [
        str(getattr(getattr(source.platform, "platform_type", None), "db_value", ""))
        for source in sources
    ]
    fallback = "vk" if "vk" in platform_keys else (platform_keys[0] if platform_keys else "vk")
    return intent_capabilities(payload, fallback_platform=fallback) | source_capabilities(sources)


async def preview_entitlements(payload):
    """Read-only, workspace-scoped preview; never consumes usage."""
    from app.core.database import async_session_maker
    from app.models.managers.entitlement_manager import EntitlementManager

    async with async_session_maker() as session:
        required = await required_capabilities(session, payload)
        return (await EntitlementManager().evaluate(session, required)).to_dict()
