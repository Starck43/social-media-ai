"""Legacy ModelType imports with unchanged runtime binding semantics."""

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin
from ..core.config import settings

if TYPE_CHECKING:
    from . import Permission

# isort: split
from .identity.model_type import ModelType
