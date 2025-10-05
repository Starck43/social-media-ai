from datetime import datetime
from typing import Optional, ClassVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class UserBase(BaseModel):
    """Base user schema with common fields."""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    is_active: Optional[bool] = True
    is_superuser: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    
    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    """Schema for creating a new user."""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    is_active: bool = True
    is_superuser: bool = False

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "johndoe",
                "email": "johndoe@example.com",
                "password": "strongpassword123",
                "is_active": True,
                "is_superuser": False
            }
        }
    )


class UserUpdate(BaseModel):
    """Schema for updating user data."""
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8)
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    
    model_config = ConfigDict(from_attributes=True)


class UserInDBBase(UserBase):
    """Base schema for user data in the database."""
    id: int
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class UserInDB(UserInDBBase):
    """Schema for user data in the database including hashed password."""
    hashed_password: str
    
    model_config = ConfigDict(from_attributes=True)


class User(UserInDBBase):
    """Schema for user response (excludes sensitive data like password)."""
    pass


class UserPasswordChange(BaseModel):
    """Schema for changing a user's password."""
    current_password: str = Field(..., min_length=8, description="Current password")
    new_password: str = Field(..., min_length=8, description="New password")
    
    @field_validator('new_password')
    @classmethod
    def password_strength(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        if not any(char.isdigit() for char in v):
            raise ValueError('Password must contain at least one number')
        if not any(char.isupper() for char in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(char.islower() for char in v):
            raise ValueError('Password must contain at least one lowercase letter')
        return v
    
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "current_password": "oldPassword123",
                "new_password": "newSecurePassword123"
            }
        }
    )
