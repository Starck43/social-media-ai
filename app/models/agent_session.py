"""Deprecated compatibility path for ``AgentSession``.

Canonical import: ``from app.models import AgentSession`` (or the domain package
``app.models.agent``). The class is declared once in
``app.models.agent.agent_session``; managers are bound only by
``app.models.bootstrap``. This module re-exports the same objects and declares
nothing; removal of the root compatibility modules is tracked by P10-T28.
"""

from __future__ import annotations

from .agent.agent_session import AgentSession
from .managers.agent_session_manager import AgentSessionManager

__all__ = ["AgentSession", "AgentSessionManager"]
