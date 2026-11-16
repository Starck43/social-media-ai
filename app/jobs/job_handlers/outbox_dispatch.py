"""Bounded workspace dispatch; each durable claim owns its transaction boundary."""

from app.services.communications.outbox_store import OutboxDispatcher

JOB_NAME = "comms.outbox.dispatch"


async def handle_outbox_dispatch(payload):
    # Actor/connection/recipient/approval never come from job payload.
    return await OutboxDispatcher().run(limit=payload.get("limit", 20))
