"""Legacy imports; canonical model and manager registration live in the domain facade."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.config import settings
from .base import Base, TimestampMixin

if TYPE_CHECKING:
    from . import Permission

# isort: split
from .identity.model_type import ModelType
