"""Scheduled event planning in the current workspace; payload never selects an actor."""

from app.core.database import async_session_maker
from app.services.planner.events import event_planning_service


async def handle_planner_events(payload):
    async with async_session_maker() as session, session.begin():
        result = await event_planning_service.run_daily(session=session)
    # The shared worker deliberately suppresses success notifications for a
    # skipped job. Disabled/empty workspaces must not receive a daily alert.
    if result["created"] == 0:
        return {"status": "skipped", "reason": result["status"], "created": 0}
    return result
