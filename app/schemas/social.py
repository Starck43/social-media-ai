from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SocialAccountBase(BaseModel):
    """Base schema for social media accounts."""
    platform: str = Field(..., min_length=2, max_length=50)
    platform_user_id: str = Field(..., min_length=2, max_length=100)
    is_active: bool = True
    
    model_config = ConfigDict(from_attributes=True)


class SocialAccountAuth(BaseModel):
    """Schema for social account authentication data."""
    access_token: str = Field(..., min_length=10)
    refresh_token: Optional[str] = None
    token_expires: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class SocialAccountCreate(SocialAccountBase):
    """Schema for creating a new social media account."""
    pass
    
    model_config = ConfigDict(from_attributes=True)


class SocialAccountUpdateAuth(SocialAccountAuth):
    """Schema for updating social account authentication data."""
    pass
    
    model_config = ConfigDict(from_attributes=True)




class SocialAccountUpdate(BaseModel):
    """Schema for updating a social media account."""
    is_active: Optional[bool] = None


class SocialAccountInDB(SocialAccountBase):
    """Schema for social account data in database."""
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime


class SocialGroupBase(BaseModel):
    """Base schema for social media groups."""
    name: str = Field(..., min_length=2, max_length=100)
    platform_id: str = Field(..., min_length=2, max_length=100)
    platform_type: str = Field(..., min_length=2, max_length=50)
    avatar_url: Optional[HttpUrl] = None
    is_active: bool = True


class SocialGroupCreate(SocialGroupBase):
    """Schema for creating a new social media group."""
    social_account_id: int


class SocialGroupUpdate(BaseModel):
    """Schema for updating a social media group."""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    avatar_url: Optional[HttpUrl] = None
    is_active: Optional[bool] = None


class SocialGroupInDB(SocialGroupBase):
    """Schema for social group data in database."""
    id: int
    social_account_id: int
    created_at: datetime
    updated_at: datetime


class SocialAccountWithGroups(SocialAccountInDB):
    """Schema for social account with its groups."""
    groups: list[SocialGroupInDB] = []
    model_config = ConfigDict(from_attributes=True)


class SocialGroupWithAccount(SocialGroupInDB):
    """Schema for a social group with its account information."""
    social_account: SocialAccountInDB
