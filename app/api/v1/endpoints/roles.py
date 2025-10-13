# app/api/v1/endpoints/roles.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi_pagination import Page, paginate
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Role
from app.schemas.role import RoleResponse, PermissionsRequest
from app.services.user.permissions import RolePermissionService

router = APIRouter(tags=["users"])


@router.get("/", response_model=Page[RoleResponse])
async def list_roles() -> Page[RoleResponse]:
	"""Get paginated list of all roles with their permissions"""
	roles = await Role.objects.select_related("permissions").order_by(Role.id)
	return paginate(roles)


@router.get("/{role_name}", response_model=RoleResponse)
def get_role(
		role_name: str,
		db: Session = Depends(get_db)
) -> RoleResponse:
	"""Get a specific role with its permissions by name"""
	role = Role.objects.get_with_permissions(role_name, db=db)
	if not role:
		raise HTTPException(404, "Role not found")
	return RoleResponse.model_validate(role)


@router.put("/{role_name}/permissions", response_model=dict)
async def update_role_permissions(
		role_name: str,
		permissions_request: PermissionsRequest,
		db: Session = Depends(get_db)
) -> dict:
	"""
	Update permissions for a role using the specified strategy.

	Available strategies:
	— 'replace' (default): Replace all permissions with the new list
	— 'merge': Add new permissions without removing existing ones
	— 'synchronize': Add new permissions and remove those not in the new list
	— 'update_actions': Update actions for the same tables
	"""
	try:
		# Use RolePermissionService to handle the update
		result = RolePermissionService.update_role_permissions(
			role_codename=role_name.lower(),
			permission_codenames=permissions_request.permissions,
			strategy=permissions_request.strategy
		)

		if not any(result.values()):
			return {"message": "No changes were made to the role permissions"}

		# Get the updated role to return
		role = Role.objects.get_with_permissions(role_name.lower(), db=db)

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
		db.rollback()
		raise HTTPException(500, f"Internal server error: {str(e)}")
