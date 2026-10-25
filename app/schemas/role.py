from typing import Optional

from fastapi_pagination import Page
from pydantic import BaseModel, ConfigDict, Field, computed_field


class PermissionOut(BaseModel):
    id: int
    name: str
    codename: str

    model_config = ConfigDict(from_attributes=True)


class RoleResponse(BaseModel):
    """Platform role DTO. `name` remains a read-only label alias for one release."""

    id: int
    codename: str
    label: str
    description: Optional[str] = None
    permissions: list[PermissionOut] = Field(default_factory=list)

    @computed_field
    @property
    def name(self) -> str:
        return self.label

    model_config = ConfigDict(from_attributes=True)


class RoleOut(RoleResponse):
    pass


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
    codename: str
    label: str
    description: Optional[str] = None


class RoleCreate(RoleBase):
    """Kept for import compatibility; arbitrary role creation is disabled."""


class PermissionsRequest(BaseModel):
    permissions: list[str] = Field(
        ...,
        description="Exact registered permission codenames to assign to the operator role",
    )
    strategy: str = Field(
        default="replace",
        description="Update strategy: replace, merge, or synchronize",
    )


class PaginatedRoleResponse(Page[RoleResponse], Page):
    pass
