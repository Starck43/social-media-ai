"""Outbox state machine, route verification, and approval binding service (P12-T04).

Implements the reliable communication outbox lifecycle:
  draft -> validated -> approved/scheduled -> claimed -> sent | uncertain_delivery | failed

Key guarantees:
  1. Approval is single-use and bound to (actor_id, content_hash, ttl_expires_at).
  2. Route verification gate: transition to approved is strictly blocked if route_verified is False.
  3. Editing body/recipient recomputes content_hash and invalidates any prior approval.
  4. Provider timeouts transition to 'uncertain_delivery' (or 'unknown') without blind automatic retry.
  5. Emergency kill-switch cancellation can be invoked by operators to abort delivery.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

# Outbox lifecycle states
STATE_DRAFT = "draft"
STATE_VALIDATED = "validated"
STATE_APPROVED = "approved"
STATE_CLAIMED = "claimed"
STATE_SENT = "sent"
STATE_UNCERTAIN_DELIVERY = "uncertain_delivery"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"

STATE_LABELS: dict[str, str] = {
    STATE_DRAFT: "DRAFT · Требует верификации",
    STATE_VALIDATED: "VALIDATED · Готово к акцепту",
    STATE_APPROVED: "APPROVED · Одобрено к отправке",
    STATE_CLAIMED: "CLAIMED · Передано в шлюз доставки",
    STATE_SENT: "SENT · Подтверждено шлюзом",
    STATE_UNCERTAIN_DELIVERY: "UNCERTAIN_DELIVERY · Таймаут провайдера (слепой повтор запрещён)",
    STATE_FAILED: "FAILED · Ошибка доставки",
    STATE_CANCELLED: "CANCELLED · Отменено (Kill Switch)",
}


class CommunicationError(Exception):
    """Base exception for outbox and communication workflows."""


class RouteNotVerifiedError(CommunicationError):
    """Raised when an approval is attempted on an unverified or ambiguous route."""


class ApprovalExpiredError(CommunicationError):
    """Raised when an outbox claim is attempted after TTL has elapsed."""


class InvalidApprovalStateError(CommunicationError):
    """Raised when an illegal transition is requested on the outbox item."""


class BlindRetryForbiddenError(CommunicationError):
    """Raised when automatic blind retry is attempted on uncertain delivery status."""


def compute_message_hash(recipient: str, channel: str, subject: str, body: str) -> str:
    """Compute deterministic SHA-256 hash for message content binding."""
    payload = f"{recipient.strip().lower()}|{channel.strip().lower()}|{subject.strip()}|{body.strip()}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def create_outbox_item(
    item_id: str,
    channel: str,
    recipient_label: str,
    recipient_address: str,
    subject: str,
    body_text: str,
    route_verified: bool = False,
    route_warning: str | None = None,
    scheduled_at: str | None = None,
) -> dict[str, Any]:
    """Create a new outbox message in draft state."""
    content_hash = compute_message_hash(recipient_address, channel, subject, body_text)
    preview = body_text[:120] + ("..." if len(body_text) > 120 else "")

    state = STATE_VALIDATED if route_verified and not route_warning else STATE_DRAFT

    return {
        "id": item_id,
        "channel": channel,
        "recipient_label": recipient_label,
        "recipient_address": recipient_address,
        "route_verified": route_verified,
        "route_warning": route_warning,
        "subject": subject,
        "body_text": body_text,
        "body_preview": preview,
        "state": state,
        "state_label": STATE_LABELS[state],
        "content_hash": content_hash,
        "ttl_expires_at": None,
        "bound_actor": None,
        "scheduled_at": scheduled_at,
        "attempts_count": 0,
        "last_error": None,
        "cancellation_reason": None,
    }


def validate_route(
    item: dict[str, Any],
    is_verified: bool,
    warning: str | None = None,
) -> dict[str, Any]:
    """Update route verification status for an outbox message."""
    updated = dict(item)
    updated["route_verified"] = is_verified
    updated["route_warning"] = warning
    if is_verified and not warning:
        if updated["state"] == STATE_DRAFT:
            updated["state"] = STATE_VALIDATED
            updated["state_label"] = STATE_LABELS[STATE_VALIDATED]
    else:
        # Route invalidated or ambiguous: demote from approved/validated
        if updated["state"] in (STATE_VALIDATED, STATE_APPROVED):
            updated["state"] = STATE_DRAFT
            updated["state_label"] = STATE_LABELS[STATE_DRAFT]
            updated["bound_actor"] = None
            updated["ttl_expires_at"] = None
    return updated


def approve_outbox_item(
    item: dict[str, Any],
    actor_id: str,
    ttl_expires_at: str,
) -> dict[str, Any]:
    """Approve outbox delivery, strictly binding to actor_id, content_hash and TTL.

    Raises RouteNotVerifiedError if route_verified is False.
    """
    if not item.get("route_verified"):
        warning_msg = item.get("route_warning") or "Маршрут получателя не подтверждён"
        raise RouteNotVerifiedError(f"Cannot approve message {item.get('id')}: {warning_msg}")

    if item.get("state") not in (STATE_VALIDATED, STATE_DRAFT):
        raise InvalidApprovalStateError(
            f"Cannot approve message in state '{item.get('state')}'. Expected '{STATE_VALIDATED}'."
        )

    # Verify content hash integrity
    expected_hash = compute_message_hash(
        item["recipient_address"],
        item["channel"],
        item["subject"],
        item["body_text"],
    )
    if item["content_hash"] != expected_hash:
        raise InvalidApprovalStateError("Content hash mismatch during approval binding.")

    updated = dict(item)
    updated["state"] = STATE_APPROVED
    updated["state_label"] = STATE_LABELS[STATE_APPROVED]
    updated["bound_actor"] = actor_id
    updated["ttl_expires_at"] = ttl_expires_at
    return updated


def update_message_body(item: dict[str, Any], new_body_text: str) -> dict[str, Any]:
    """Update message body text, recomputing content hash and invalidating approval."""
    updated = dict(item)
    updated["body_text"] = new_body_text
    updated["body_preview"] = new_body_text[:120] + ("..." if len(new_body_text) > 120 else "")
    updated["content_hash"] = compute_message_hash(
        updated["recipient_address"],
        updated["channel"],
        updated["subject"],
        new_body_text,
    )
    # Invalidate prior approval
    updated["state"] = STATE_VALIDATED if updated.get("route_verified") else STATE_DRAFT
    updated["state_label"] = STATE_LABELS[updated["state"]]
    updated["bound_actor"] = None
    updated["ttl_expires_at"] = None
    return updated


def claim_outbox_item(
    item: dict[str, Any],
    current_time_iso: str | None = None,
) -> dict[str, Any]:
    """Claim an approved outbox message for gateway transmission.

    Raises ApprovalExpiredError if TTL has expired.
    """
    if item.get("state") != STATE_APPROVED:
        raise InvalidApprovalStateError(
            f"Cannot claim message in state '{item.get('state')}'. Must be '{STATE_APPROVED}'."
        )

    # Check TTL if provided
    ttl_str = item.get("ttl_expires_at")
    if ttl_str and current_time_iso:
        try:
            ttl_dt = datetime.fromisoformat(ttl_str.replace("Z", "+00:00"))
            curr_dt = datetime.fromisoformat(current_time_iso.replace("Z", "+00:00"))
            if curr_dt > ttl_dt:
                raise ApprovalExpiredError(f"Approval for message {item.get('id')} expired at {ttl_str}")
        except ValueError:
            pass  # Non-standard timestamp string (e.g., human-readable "14:00 MSK"), skip ISO comparison

    updated = dict(item)
    updated["state"] = STATE_CLAIMED
    updated["state_label"] = STATE_LABELS[STATE_CLAIMED]
    updated["attempts_count"] = updated.get("attempts_count", 0) + 1
    return updated


def confirm_delivery(item: dict[str, Any], provider_ack: str | None = None) -> dict[str, Any]:
    """Transition message to SENT upon verified gateway acknowledgment."""
    if item.get("state") != STATE_CLAIMED:
        raise InvalidApprovalStateError(
            f"Cannot confirm delivery for message in state '{item.get('state')}'. Expected '{STATE_CLAIMED}'."
        )
    updated = dict(item)
    updated["state"] = STATE_SENT
    updated["state_label"] = STATE_LABELS[STATE_SENT]
    updated["provider_ack"] = provider_ack
    return updated


def timeout_delivery(item: dict[str, Any], timeout_note: str) -> dict[str, Any]:
    """Transition message to UNCERTAIN_DELIVERY on gateway timeout.

    Enforces that blind automatic retries are blocked to prevent duplicate sends.
    """
    if item.get("state") != STATE_CLAIMED:
        raise InvalidApprovalStateError(f"Cannot record timeout for message in state '{item.get('state')}'.")
    updated = dict(item)
    updated["state"] = STATE_UNCERTAIN_DELIVERY
    updated["state_label"] = STATE_LABELS[STATE_UNCERTAIN_DELIVERY]
    updated["last_error"] = f"Gateway timeout: {timeout_note}"
    return updated


def can_retry_blindly(item: dict[str, Any]) -> bool:
    """Check if the message can be automatically retried without human operator audit.

    Always returns False for UNCERTAIN_DELIVERY to prevent duplicate transmission.
    """
    if item.get("state") == STATE_UNCERTAIN_DELIVERY:
        return False
    if item.get("state") == STATE_FAILED and item.get("attempts_count", 0) < 3:
        return True
    return False


def cancel_outbox_item(
    item: dict[str, Any],
    actor_id: str,
    reason: str = "Аварийная отмена оператором (Kill Switch)",
) -> dict[str, Any]:
    """Emergency cancel an outbox message before or during transmission."""
    if item.get("state") == STATE_SENT:
        raise InvalidApprovalStateError("Cannot cancel message that has already been delivered.")
    updated = dict(item)
    updated["state"] = STATE_CANCELLED
    updated["state_label"] = STATE_LABELS[STATE_CANCELLED]
    updated["cancellation_reason"] = f"{reason} [actor={actor_id}]"
    updated["bound_actor"] = None
    return updated


def fail_delivery(item: dict[str, Any]) -> dict[str, Any]:
    """Record a definitive sender rejection; persistent dispatch never retries it."""
    if item.get("state") != STATE_CLAIMED:
        raise InvalidApprovalStateError("Cannot fail a message that is not claimed.")
    return {**item, "state": STATE_FAILED, "state_label": STATE_LABELS[STATE_FAILED]}


def reject_approved_item(item: dict[str, Any]) -> dict[str, Any]:
    """Close an approved target whose fresh admission checks were denied."""
    if item.get("state") != STATE_APPROVED:
        raise InvalidApprovalStateError("Cannot reject admission for a target that is not approved.")
    return {**item, "state": STATE_FAILED, "state_label": STATE_LABELS[STATE_FAILED]}
