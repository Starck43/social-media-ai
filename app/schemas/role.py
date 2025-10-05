from typing import Optional

from fastapi_pagination import Page
from pydantic import BaseModel, Field, ConfigDict


class PermissionOut(BaseModel):
	id: int
	name: str
	codename: str

	model_config = ConfigDict(from_attributes=True)


class RoleOut(BaseModel):
	id: int
	name: str
	codename: str
	description: Optional[str] = None
	permissions: list[PermissionOut]

	model_config = ConfigDict(from_attributes=True)


class UpdateRolePermissions(BaseModel):
	permission_ids: list[int]


class PermissionBase(BaseModel):
	codename: str
	name: Optional[str] = None


class PermissionCreate(PermissionBase):
	pass


class PermissionResponse(PermissionBase):
	id: int

	model_config = ConfigDict(from_attributes=True)


class RoleBase(BaseModel):
	name: str
	codename: str
	description: Optional[str] = None


class RoleCreate(RoleBase):
	pass


class RoleResponse(RoleBase):
	id: int
	permissions: list[PermissionResponse] = []

	model_config = {
		"from_attributes": True,
		"json_schema_extra": {
			"example": {
				"id": 1,
				"name": "viewer",
				"codename": "VIEWER",
				"permissions": [
					{"id": 1, "name": "View users", "codename": "account.users.view"},
					{"id": 2, "name": "Edit users", "codename": "account.users.edit"}
				]
			}
		}
	}


class PermissionsRequest(BaseModel):
	permissions: list[str] = Field(
		...,
		description="List of permission codenames to assign to the role"
	)
	strategy: str = Field(
		default='replace',
		description="Update strategy: 'replace' (default), 'merge', 'synchronize', or 'update_actions'"
	)


class PaginatedRoleResponse(Page[RoleResponse], Page):
	pass
