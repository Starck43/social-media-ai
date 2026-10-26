"""Compatibility aliases; canonical declarations live in domain packages."""

from .analysis.agent_scenario import AgentScenario
from .managers.agent_scenario_manager import AgentScenarioManager

__all__ = ["AgentScenario", "AgentScenarioManager"]
