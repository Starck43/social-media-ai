"""Strict, bounded envelopes for new business modules only."""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

Item = TypeVar("Item")


class BusinessSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class BusinessError(BusinessSchema):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=160)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")


class BusinessPage(BusinessSchema, Generic[Item]):
    items: list[Item] = Field(max_length=100)
    limit: int = Field(default=50, ge=1, le=100, strict=True)
    next_before_id: int | None = Field(default=None, gt=0, strict=True)
