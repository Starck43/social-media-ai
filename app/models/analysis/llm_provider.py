"""Legacy alias inside the analysis package; the class lives in `app.models.llm`."""

from ..base import Base, TimestampMixin
from ..llm.llm_provider import LLMProvider

__all__ = ["LLMProvider", "Base", "TimestampMixin"]
