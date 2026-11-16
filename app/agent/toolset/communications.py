"""Thread-reply chat surface (P15-T08): preview only, never a send path.

The agent may read a thread's reply origin and draft a reply text, but it cannot
create or schedule an outbox item. A draft goes back to the person, who approves
it through the existing outbox decision surface.
"""

from typing import Any

from pydantic import ValidationError

from app.services.communications.reply import ReplyUnavailable


async def reply_preview(thread_id: int, inbound_message_id: int) -> dict[str, Any]:
    """Return the verified origin and the exact reply the person may approve.

    No outbox row is created: `create_reply_draft` is deliberately not called from
    chat, so an agent can never mint a sendable message on its own.
    """
    try:
        return await _preview(thread_id, inbound_message_id)
    except (ValidationError, ValueError):
        return {"error": "Invalid reply source", "code": "reply_invalid_source"}
    except (PermissionError, ReplyUnavailable):
        return {"error": "Reply unavailable", "code": "reply_unavailable"}


async def _preview(thread_id: int, inbound_message_id: int) -> dict[str, Any]:
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models import ContactIdentity
    from app.models.communications.message import MessageThread, UnifiedMessage
    from app.services.communications.outbox_store import current_actor
    from app.services.communications.reply import reply_authority, select_email_origin
    from app.types import ActionType

    if not isinstance(thread_id, int) or not isinstance(inbound_message_id, int):
        raise ValueError("invalid_reply_source")
    if thread_id < 1 or inbound_message_id < 1:
        raise ValueError("invalid_reply_source")
    async with async_session_maker() as session:
        tenant, actor = await current_actor(session, ActionType.VIEW)
        await reply_authority(session, tenant, actor)
        thread = await session.scalar(
            select(MessageThread).where(MessageThread.tenant_id == tenant, MessageThread.id == thread_id)
        )
        inbound = await session.scalar(
            select(UnifiedMessage).where(UnifiedMessage.tenant_id == tenant, UnifiedMessage.id == inbound_message_id)
        )
        if thread is None or inbound is None:
            raise ReplyUnavailable("reply_origin_unavailable")
        identities = list(
            (
                await session.scalars(
                    select(ContactIdentity).where(
                        ContactIdentity.tenant_id == tenant, ContactIdentity.contact_id == thread.contact_id
                    )
                )
            ).all()
        )
        origin = select_email_origin(thread, inbound, identities)
        return {
            "thread_id": thread_id,
            "inbound_message_id": inbound_message_id,
            "contact_id": origin.contact_id,
            "connection_id": origin.connection_id,
            "from_identity_id": origin.identity_id,
            "decision_surface": "/web/communications",
            "writes": False,
        }


def register_communications_tools():
    from app.agent.tools import TOOL_REGISTRY, tool
    from app.core.access_catalog import resolve_permission_key

    if resolve_permission_key("comms.outbox.view") is None:
        raise ValueError("comms_catalog_not_registered")
    if "reply_preview" not in TOOL_REGISTRY:
        tool(
            name="reply_preview",
            description=(
                "Показать проверенный источник ответа в потоке: контакт, подключение и отправителя, "
                "плюс ссылку на место, где человек утвердит ответ. Ничего не создаёт и не отправляет."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "integer", "minimum": 1},
                    "inbound_message_id": {"type": "integer", "minimum": 1},
                },
                "required": ["thread_id", "inbound_message_id"],
                "additionalProperties": False,
            },
            required_permission="comms.outbox.view",
            confirm=False,
        )(reply_preview)
