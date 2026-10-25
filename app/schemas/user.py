from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.types import PlatformRoleType


class UserBase(BaseModel):
    """Base user schema with common fields."""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    is_active: Optional[bool] = True

    model_config = ConfigDict(from_attributes=True)


class UserLogin(BaseModel):
    email: EmailStr
    password: str

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    """Self-service registration payload.

    Only identity fields are accepted. Account state and platform rights are
    assigned by the server and are not accepted from the request body.
    """

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"username": "johndoe", "email": "johndoe@example.com", "password": "strongpassword123"}
        }
    )


class UserUpdate(BaseModel):
    """Self-service profile fields. Privileged fields use dedicated routes."""

    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None

    model_config = ConfigDict(from_attributes=True)


class UserInDBBase(UserBase):
    """Base schema for user data in the database."""

    id: int
    created_at: datetime
    updated_at: datetime

    # The stored address is shown as it is. The write paths validate the
    # address (`UserCreate`, `UserUpdate`); a *read* must not fail because a
    # row holds an address the strict input rule would have refused — one such
    # row used to take the whole user list down with a validation error.
    email: str

    model_config = ConfigDict(from_attributes=True)


class UserInDB(UserInDBBase):
    """Compatibility response name; never exposes a password hash."""


class PlatformRoleUpdate(BaseModel):
    """Platform administrators assign only a known platform role."""

    role: PlatformRoleType


class User(UserInDBBase):
    """Schema for user response (excludes sensitive data like password)."""

    pass


class UserPasswordChange(BaseModel):
    """Schema for changing a user's password."""

    current_password: str = Field(..., min_length=8, description="Current password")
    new_password: str = Field(..., min_length=8, description="New password")

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(char.isdigit() for char in v):
            raise ValueError("Password must contain at least one number")
        if not any(char.isupper() for char in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(char.islower() for char in v):
            raise ValueError("Password must contain at least one lowercase letter")
        return v

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={"example": {"current_password": "oldPassword123", "new_password": "newSecurePassword123"}},
    )
