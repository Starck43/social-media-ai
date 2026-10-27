"""Selected task access shared by previews and the VK request boundary.

Only trusted handler code binds this context from a persisted/admitted plan.
The binding carries identities, never grants or a cached token. Each outbound
request reloads authorization, ownership, revocation, expiry and provider grants.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator, Mapping

from app.services.social.vk_access import VKSelection, parse_vk_selection


@dataclass(frozen=True)
class SelectedTaskAccess:
    selection: VKSelection
    tenant_id: int
    actor_id: int


_selected: ContextVar[SelectedTaskAccess | None] = ContextVar("selected_vk_task_access", default=None)


def selected_task_access() -> SelectedTaskAccess | None:
    return _selected.get()


@contextmanager
def vk_task_access_scope(selection: VKSelection, *, tenant_id: int, actor_id: int) -> Iterator[None]:
    token = _selected.set(SelectedTaskAccess(selection, tenant_id, actor_id))
    try:
        yield
    finally:
        _selected.reset(token)


def plan_required_methods(raw: Mapping[str, Any]) -> frozenset[str]:
    """Conservative preflight; exact methods are checked again per request."""
    from app.services.task_plans import parse_task_plan

    plan = parse_task_plan(raw)
    if "vk" not in plan.platforms:
        return frozenset()
    intent = plan.collection
    methods = {"wall.get", "groups.getById", "users.get"}
    if intent.queries:
        methods.add("newsfeed.search")
    if intent.includes_comments:
        methods.add("wall.getComments")
    methods.update({"likes": "likes.getList", "reposts": "wall.getReposts"}[item] for item in intent.engagement_types)
    discovery = intent.discovery
    if discovery.queries:
        if discovery.people:
            methods.add("users.search")
        if discovery.communities:
            methods.add("groups.search")
        if discovery.city is not None and discovery.city.id is None:
            methods.add("database.getCities")
    if intent.city is not None:
        methods.add("database.getCities")
    return frozenset(methods)


async def preview_vk_access(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Safe, read-only metadata. A preview is never an execution permit."""
    from app.core.permissions import get_current_user
    from app.core.tenant_context import current_tenant_id
    from app.services.social.vk_connections import check_selected_access, connection_catalog

    if "vk" not in raw.get("platforms", ["vk"]):
        return {"selected": None, "reason": "not_applicable"}
    selection_raw = raw.get("vk_access")
    if selection_raw is None:
        return {"selected": None, "reason": "legacy_source_access", "checked": False}
    selection = parse_vk_selection(selection_raw)
    tenant_id = current_tenant_id()
    actor_id = getattr(get_current_user(), "id", None)
    if type(tenant_id) is not int or type(actor_id) is not int:
        return {"selected": selection.to_dict(), "allowed": False, "reason": "workspace_actor_required"}
    try:
        catalog = await connection_catalog(tenant_id=tenant_id, actor_id=actor_id)
        decision = await check_selected_access(
            selection, tenant_id=tenant_id, actor_id=actor_id, required_methods=plan_required_methods(raw)
        )
    except PermissionError:
        return {"selected": selection.to_dict(), "allowed": False, "reason": "vk_access_unavailable"}
    return {"selected": selection.to_dict(), "decision": decision.to_dict(), **catalog}


async def selected_request_token(method: str) -> str:
    from app.services.social.vk_connections import resolve_selected_token

    bound = selected_task_access()
    if bound is None:
        raise PermissionError("vk_task_selection_required")
    return await resolve_selected_token(
        bound.selection, tenant_id=bound.tenant_id, actor_id=bound.actor_id, required_methods=frozenset({method})
    )


@asynccontextmanager
async def authorized_vk_task_scope(raw: Mapping[str, Any], *, tenant_id: int, actor_id: int) -> AsyncIterator[None]:
    """Bind fresh rights for a trusted initiator/payer, then selected access.

    Entry points derive actor_id from authentication or the stored job/payer;
    it must never be copied from a request's task plan or credential metadata.
    """
    from app.core.permissions import get_current_user, permission_scope
    from app.models import User
    from app.models.managers.tenant_manager import tenant_users
    from app.services.social.vk_connections import check_selected_access
    from app.types import ActionType

    if type(actor_id) is not int or actor_id <= 0 or type(tenant_id) is not int or tenant_id <= 0:
        raise PermissionError("vk_task_actor_required")
    selection = parse_vk_selection(raw.get("vk_access"))
    ambient = get_current_user()
    if ambient is not None and getattr(ambient, "id", None) != actor_id:
        raise PermissionError("vk_task_actor_mismatch")
    actor = await User.objects.get(id=actor_id, is_active=True)
    memberships = await tenant_users.web_memberships(actor_id)
    member = next((item for item in memberships if item.tenant_id == tenant_id), None)
    grants = await tenant_users.member_grants(member)
    if actor is None or not {("source", ActionType.ANALYZE), ("agenttask", ActionType.UPDATE)}.issubset(grants):
        raise PermissionError("vk_task_execution_permission_required")
    with permission_scope(actor, member_grants=grants):
        decision = await check_selected_access(
            selection, tenant_id=tenant_id, actor_id=actor_id, required_methods=plan_required_methods(raw)
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)
        with vk_task_access_scope(selection, tenant_id=tenant_id, actor_id=actor_id):
            yield
