"""Agent toolset. Importing this package registers all tools."""

from __future__ import annotations

from . import (  # noqa: F401
    actions,
    collect,
    communications,
    documents,
    llm,
    onec,
    planner,
    reports,
    scenarios,
    social_brief,
    sources,
    system,
    tasks,
    web_research,
)

planner.register_planner_tools()
communications.register_communications_tools()
