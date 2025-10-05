from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_serializer


class TokenBase(BaseModel):
    """Base token schema with common fields."""
    token_type: str = "bearer"
    expires_at: datetime
    
    @field_serializer('expires_at')
    def serialize_dt(self, dt: datetime, _info) -> float:
        return dt.timestamp()
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token_type": "bearer",
                "expires_at": 1735689600
            }
        }
    )


class Token(TokenBase):
    """Schema for access token response."""
    access_token: str
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_at": 1735689600
            }
        }
    )


class TokenWithRefresh(Token):
    """Schema for token response with refresh token."""
    refresh_token: str
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_at": 1735689600
            }
        }
    )
