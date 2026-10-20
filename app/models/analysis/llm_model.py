"""Legacy alias inside the analysis package; the class lives in `app.models.llm`."""

from ..base import Base, TimestampMixin
from ..llm.llm_model import LLMModel, _model_type_to_capabilities

__all__ = ["LLMModel", "_model_type_to_capabilities", "Base", "TimestampMixin"]
