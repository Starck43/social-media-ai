"""Purpose-built append-only installation journal writer."""

import uuid


class TrustedOutboundHostEventManager:
    async def _record(self, *, session, action, actor_id, row):
        from app.models.integrations.trusted_outbound_host_event import TrustedOutboundHostEvent

        if action not in {"admin.trusted_host_add", "admin.trusted_host_remove"}:
            raise ValueError("invalid_trusted_host_action")
        snapshot = {
            "id": row.id,
            "host": row.host,
            "kind": row.kind,
            "authorized_addresses": list(row.authorized_addresses),
            "note": row.note,
            "created_by_actor_id": row.created_by_actor_id,
            "created_at": row.created_at.isoformat(),
        }
        event = TrustedOutboundHostEvent(
            event_id=uuid.uuid4().hex,
            action=action,
            actor_id=actor_id,
            host_entry_id=row.id,
            host=row.host,
            snapshot=snapshot,
        )
        session.add(event)
        await session.flush()
        return event

    async def list_events(self, *, actor_id, session, limit=100):
        """Bounded read, restricted to the scoped, freshly checked installation admin."""
        from sqlalchemy import select

        from app.models.integrations.trusted_outbound_host_event import TrustedOutboundHostEvent
        from app.models.managers.trusted_outbound_host_manager import TrustedOutboundHostManager

        TrustedOutboundHostManager._require_context(actor_id)
        await TrustedOutboundHostManager._require_admin(session, actor_id)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid_event_limit")
        rows = await session.execute(
            select(TrustedOutboundHostEvent).order_by(TrustedOutboundHostEvent.id.desc()).limit(limit)
        )
        return list(rows.scalars().all())

    async def update(self, *args, **kwargs):
        raise PermissionError("Trusted-host events are append-only")

    async def update_by_id(self, *args, **kwargs):
        raise PermissionError("Trusted-host events are append-only")

    async def delete(self, *args, **kwargs):
        raise PermissionError("Trusted-host events are append-only")

    async def delete_by_id(self, *args, **kwargs):
        raise PermissionError("Trusted-host events are append-only")


writer = TrustedOutboundHostEventManager()
