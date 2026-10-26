"""Compatibility aliases; canonical declarations live in domain packages."""

from .agent.agent_feedback import AgentFeedback
from .managers.agent_feedback_manager import AgentFeedbackManager

__all__ = ["AgentFeedback", "AgentFeedbackManager"]
