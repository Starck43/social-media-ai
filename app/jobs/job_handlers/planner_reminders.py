"""Bounded dispatch of due planner reminders and their pending channel deliveries."""

from app.services.notifications.reminders import reminder_notifications
from app.services.planner.reminders import reminder_service

JOB_NAME = "planner.reminders.dispatch"


async def handle_planner_reminders(payload):
    raw_limit = payload.get("limit", 20)
    if type(raw_limit) is not int or not 1 <= raw_limit <= 100:
        raise ValueError("invalid_reminder_dispatch_limit")
    due = await reminder_service.dispatch_due(limit=raw_limit)
    deliveries = await reminder_notifications.dispatch_pending(limit=raw_limit)
    return {"reminders": due, "deliveries": deliveries}
