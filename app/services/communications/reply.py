"""Conservative origin selection for a future reply draft (P15-T08).

This module does not authorize, persist, schedule or send replies. The caller
must obtain fresh tenant-scoped, permission-checked rows and revalidate them
under the transaction and dispatch locks. A route selected here is not proof of
consent, approval, a verified email header, or a private bot conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses
from typing import Iterable


class ReplyUnavailable(LookupError):
    """A reply origin is missing, ambiguous, or unsupported; no PII in errors."""


@dataclass(frozen=True)
class ReplyOrigin:
    thread_id: int
    inbound_message_id: int
    contact_id: int
    connection_id: int
    identity_id: int
    channel: str = "email"


def _positive(value):
    return type(value) is int and value > 0


def select_email_origin(thread, inbound, identities: Iterable) -> ReplyOrigin:
    """Bind a specifically selected inbound email to one verified contact identity.

    `thread`, `inbound`, and `identities` must be fresh server-loaded rows, never
    request-supplied objects. A caller must decide which inbound message to reply
    to, lock/revalidate the underlying rows and enforce all authority itself.
    Ambiguous addresses, deleted originals, mismatched scope and duplicate verified
    identities fail closed. No fallback to another message, identity or channel.
    """
    from app.core.pii import PIIError
    from app.services.contacts.identity import normalized_identity

    tenant = getattr(thread, "tenant_id", None)
    contact = getattr(thread, "contact_id", None)
    connection = getattr(thread, "connection_id", None)
    if (
        not all(_positive(v) for v in (tenant, thread.id, contact, connection, inbound.id))
        or thread.channel != "email"
        or inbound.tenant_id != tenant
        or inbound.thread_id != thread.id
        or inbound.contact_id != contact
        or inbound.connection_id != connection
        or inbound.channel != "email"
        or inbound.direction != "inbound"
        or inbound.is_hidden
        or inbound.unavailable_at is not None
        or not isinstance(inbound.sender_encrypted, str)
    ):
        raise ReplyUnavailable("reply_origin_unavailable")
    try:
        addresses = getaddresses([inbound.sender_encrypted])
        if len(addresses) != 1 or not addresses[0][1]:
            raise ReplyUnavailable("reply_origin_unavailable")
        sender = normalized_identity("email", addresses[0][1])
        matches = []
        for identity in identities:
            if (
                identity.tenant_id == tenant
                and identity.contact_id == contact
                and identity.kind == "email"
                and identity.verified is True
                and identity.connection_id in (None, connection)
                and _positive(identity.id)
                and normalized_identity("email", identity.value_encrypted) == sender
            ):
                matches.append(identity)
    except (ValueError, TypeError, PIIError):
        raise ReplyUnavailable("reply_origin_unavailable") from None
    if len(matches) != 1:
        raise ReplyUnavailable("reply_origin_unavailable")
    return ReplyOrigin(thread.id, inbound.id, contact, connection, matches[0].id)
