"""Deprecated compatibility path for ``BotAction``.

Canonical import: ``from app.models import BotAction`` (or the domain package
``app.models.scheduling``). The class is declared once in
``app.models.scheduling.bot_action``; managers are bound only by
``app.models.bootstrap``. This module re-exports the same objects and declares
nothing; removal of the root compatibility modules is tracked by P10-T28.
"""

from __future__ import annotations

from ..types import AgentActionType, BotActionStatus
from .managers.bot_action_manager import BotActionManager
from .scheduling.bot_action import BotAction

# The enum re-exports are pinned legacy symbols (tests/models/test_scheduling_model_layout.py).
__all__ = ["BotAction", "BotActionManager", "AgentActionType", "BotActionStatus"]
