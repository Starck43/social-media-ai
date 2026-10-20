from fastapi import APIRouter, Depends, HTTPException
from fastapi_pagination import Page, paginate

from app.api.deps import require_model_perm, require_platform_role
from app.core.permissions import permission_scope
from app.models import PlatformRole
from app.schemas.role import PermissionsRequest, RoleResponse
from app.services.user.permissions import RolePermissionService
from app.types import ActionType, PlatformRoleType

router = APIRouter(tags=["users"])

_LEGACY_ROLE_ALIASES = {"viewer": "user", "superuser": "admin"}


def _canonical_role_code(role_name: str) -> str:
    code = role_name.strip().lower()
    return _LEGACY_ROLE_ALIASES.get(code, code)


@router.get("/", response_model=Page[RoleResponse])
async def list_roles(
    _current_user=Depends(require_model_perm("identity.platform_role", ActionType.VIEW)),
) -> Page[RoleResponse]:
    """List the fixed platform roles for authorized operators."""
    # The roles are materialised before they are paginated: the paginator
    # walks the sequence, and the relationships it does not see are exactly
    # the ones a reader needs — the role came back with an empty grant list.
    roles = await PlatformRole.objects.prefetch_related("permissions").order_by(PlatformRole.id).all()
    return paginate(roles)


@router.get("/{role_name}", response_model=RoleResponse)
async def get_role(
    role_name: str,
    _current_user=Depends(require_model_perm("identity.platform_role", ActionType.VIEW)),
) -> RoleResponse:
    role = await PlatformRole.objects.get_by_codename(_canonical_role_code(role_name))
    if not role:
        raise HTTPException(404, "Role not found")
    role = await PlatformRole.objects.get_with_permissions(role.id)
    return RoleResponse.model_validate(role)


@router.put("/{role_name}/permissions", response_model=dict)
async def update_role_permissions(
    role_name: str,
    permissions_request: PermissionsRequest,
    current_user=Depends(require_platform_role(PlatformRoleType.ADMIN)),
) -> dict:
    """Change operator grants. Fixed roles and user grants cannot be edited."""
    role_code = _canonical_role_code(role_name)
    if role_code != PlatformRoleType.OPERATOR.value:
        raise HTTPException(400, "Only operator grants can be changed")
    # The write path checks who is acting, and that check reads the ambient
    # user. The request's identity has to become that ambient user, or the
    # administrator's own change is refused as "Platform admin required".
    with permission_scope(current_user):
        try:
            result = await RolePermissionService.update_role_permissions(
                role_codename=role_code,
                permission_codenames=permissions_request.permissions,
                strategy=permissions_request.strategy,
            )
            updated_role = await PlatformRole.objects.get_by_codename(role_code)
            if updated_role is None:
                raise HTTPException(404, "Role not found")
            role = await PlatformRole.objects.get_with_permissions(updated_role.id)
            return {
                "message": "Permissions updated successfully",
                "role": RoleResponse.model_validate(role),
                "changes": result,
            }
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
