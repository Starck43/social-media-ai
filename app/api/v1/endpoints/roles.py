# app/api/v1/endpoints/roles.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi_pagination import Page, paginate

from app.api.deps import require_platform_role
from app.models import Role
from app.schemas.role import RoleResponse, PermissionsRequest
from app.services.user.permissions import RolePermissionService
from app.types import UserRoleType

router = APIRouter(tags=["users"])


@router.get("/", response_model=Page[RoleResponse])
async def list_roles() -> Page[RoleResponse]:
	"""Get paginated list of all roles with their permissions"""
	# `permissions` is a m2m: select_related silently returns empty collections,
	# so the roles would serialise with no permissions at all.
	roles = await Role.objects.prefetch_related("permissions").order_by(Role.id)
	return paginate(roles)


@router.get("/{role_name}", response_model=RoleResponse)
async def get_role(role_name: str) -> RoleResponse:
	"""Get a specific role with its permissions by name"""
	role = await Role.objects.get_by_name_with_permissions(role_name)
	if not role:
		raise HTTPException(404, "Role not found")
	return RoleResponse.model_validate(role)


@router.put("/{role_name}/permissions", response_model=dict)
async def update_role_permissions(
		role_name: str,
		permissions_request: PermissionsRequest,
	_current_user=Depends(require_platform_role(UserRoleType.ADMIN)),
) -> dict:
	"""
	Update permissions for a role using the specified strategy.

	Platform role ADMIN or higher: this rewrites what every account of that role
	may do, so it is deliberately above workspace owners.

	Available strategies:
	— 'replace' (default): Replace all permissions with the new list
	— 'merge': Add new permissions without removing existing ones
	— 'synchronize': Add new permissions and remove those not in the new list
	— 'update_actions': Update actions for the same tables
	"""
	try:
		# Use RolePermissionService to handle the update
		result = await RolePermissionService.update_role_permissions(
			role_codename=role_name.lower(),
			permission_codenames=permissions_request.permissions,
			strategy=permissions_request.strategy
		)

		if not any(result.values()):
			return {"message": "No changes were made to the role permissions"}

		# Get the updated role to return
		role = await Role.objects.get_by_name_with_permissions(role_name)

		return {
			"message": "Permissions updated successfully",
			"role": RoleResponse.model_validate(role),
			"changes": {
				"added": result["added"],
				"removed": result["removed"],
				"updated": result["updated"],
				"unchanged": result["unchanged"]
			}
		}

	except ValueError as e:
		raise HTTPException(400, str(e))
	except Exception as e:
		# The service owns its session, so a failure there is already rolled back.
		raise HTTPException(500, f"Internal server error: {str(e)}")
