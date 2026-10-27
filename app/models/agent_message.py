"""Compatibility aliases; canonical declarations live in domain packages."""

from .agent.agent_message import AgentMessage
from .managers.agent_message_manager import AgentMessageManager

__all__ = ["AgentMessage", "AgentMessageManager"]
