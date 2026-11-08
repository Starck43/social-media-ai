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


@dataclass(frozen=True, repr=False)
class ReplyHeaders:
    """Exact validated wire headers; contains private metadata and must not be logged."""

    in_reply_to: str
    references: tuple[str, ...]

    def approval_value(self) -> dict:
        """Embed this value in the existing outbox content digest, not alongside it."""
        return {"in_reply_to": self.in_reply_to, "references": list(self.references)}


def verified_email_headers(inbound, provider_message_id, provider_references) -> ReplyHeaders:
    """Check fetched headers against the persisted source hash and bound resource.

    Inputs must come from a fresh, authenticated read of the exact source message
    on its bound connection, not from an API caller or model. A hash match alone
    cannot prove that provenance. Callers must bind the result to approval and
    revalidate before dispatch; this function itself performs no I/O or approval.
    """
    from app.integrations.email.smtp_send import _MESSAGE_ID
    from app.services.communications.ingest import hash_message_id

    if (
        not isinstance(provider_message_id, str)
        or len(provider_message_id) > 998
        or _MESSAGE_ID.fullmatch(provider_message_id) is None
        or not isinstance(getattr(inbound, "external_message_id_hash", None), str)
        or hash_message_id(provider_message_id) != inbound.external_message_id_hash
        or not isinstance(provider_references, (list, tuple))
        or len(provider_references) > 30
    ):
        raise ReplyUnavailable("reply_headers_unavailable")
    references = []
    for ref in provider_references:
        if not isinstance(ref, str) or len(ref) > 998 or _MESSAGE_ID.fullmatch(ref) is None:
            raise ReplyUnavailable("reply_headers_unavailable")
        if ref not in references:
            references.append(ref)
    if provider_message_id not in references:
        references.append(provider_message_id)
    if len(" ".join(references)) > 998:
        raise ReplyUnavailable("reply_headers_unavailable")
    return ReplyHeaders(provider_message_id, tuple(references))


def reply_exemption_candidate(message, target, thread, inbound, identities: Iterable) -> bool:
    """Check structural D15 evidence only; never an authorization or approval.

    Rows must be freshly loaded and locked by the trusted outbox transaction.
    This fail-closed predicate is insufficient on its own: caller must verify
    current permissions and the approval binding of this exact source and route,
    both at scheduling and immediately before provider I/O. It does not permit
    a switched destination without independent matching inbound evidence.
    """
    try:
        origin = select_email_origin(thread, inbound, identities)
        return bool(
            message.purpose == "transactional"
            and message.tenant_id == thread.tenant_id == target.tenant_id
            and message.contact_id == origin.contact_id
            and getattr(message, "reply_thread_id", None) == origin.thread_id
            and getattr(message, "reply_inbound_message_id", None) == origin.inbound_message_id
            and target.message_id == message.id
            and target.channel == origin.channel
            and target.connection_id == origin.connection_id
            and target.identity_id == origin.identity_id
        )
    except (ReplyUnavailable, AttributeError):
        return False
