"""Fail-closed, side-effect-free channel ranking for P15-T07.

The caller must derive eligibility from current tenant-scoped consent and
reachability evidence, never from user-supplied flags. Ranking is not delivery
permission, a promise of delivery, or an instruction to send anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CHANNEL_ORDER = ("email", "telegram", "max", "phone", "bitrix24")


@dataclass(frozen=True)
class ChannelSignals:
    channel: str
    consented: bool
    reachable: bool
    inbound_count: int = 0
    outbound_count: int = 0
    last_inbound_at: datetime | None = None


@dataclass(frozen=True)
class Recommendation:
    channel: str | None
    confidence: float
    reasons: tuple[str, ...]


def _recent_inbound(last: datetime | None, now: datetime) -> float:
    if last is None or last.utcoffset() is None or last > now:
        return 0.0
    age = now - last
    if age <= timedelta(days=7):
        return 1.0
    if age <= timedelta(days=30):
        return 0.75
    if age <= timedelta(days=90):
        return 0.4
    return 0.1


def rank_channels(
    candidates: list[ChannelSignals], *, preferred_channel: str | None = None, now: datetime | None = None
) -> Recommendation:
    """Rank already authorized routes; no fallback may bypass eligibility.

    Preference dominates activity; activity dominates volume. Ties are stable.
    Confidence is a bounded heuristic, not a calibrated success probability.
    """
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    seen: set[str] = set()
    eligible = []
    for candidate in candidates:
        if candidate.channel not in CHANNEL_ORDER or candidate.channel in seen:
            raise ValueError("invalid or duplicate channel")
        seen.add(candidate.channel)
        if type(candidate.inbound_count) is not int or candidate.inbound_count < 0:
            raise ValueError("invalid inbound count")
        if type(candidate.outbound_count) is not int or candidate.outbound_count < 0:
            raise ValueError("invalid outbound count")
        if candidate.consented is not True or candidate.reachable is not True:
            continue
        preferred = candidate.channel == preferred_channel
        activity = _recent_inbound(candidate.last_inbound_at, now)
        count = candidate.inbound_count + candidate.outbound_count
        volume = min(1.0, math.log1p(count) / math.log(21))
        # Tuples enforce priority even when frequency differs by orders of magnitude.
        eligible.append((preferred, activity, volume, count, candidate))
    if not eligible:
        return Recommendation(None, 0.0, ("no_consented_reachable_channel",))
    eligible.sort(key=lambda x: (-int(x[0]), -x[1], -x[3], CHANNEL_ORDER.index(x[4].channel)))
    preferred, activity, volume, count, selected = eligible[0]
    if len(eligible) == 1:
        margin = 0.0
    else:
        other = eligible[1]
        margin = 1.0 if preferred != other[0] else max(0.0, activity - other[1], (volume - other[2]) * 0.25)
    confidence = round(min(0.95, 0.2 + 0.35 * min(1.0, count / 20) + 0.3 * margin), 3)
    reasons = ["consented_and_reachable"]
    if preferred:
        reasons.append("manual_preference")
    if activity > 0:
        reasons.append("recent_inbound_activity" if activity >= 0.4 else "older_inbound_activity")
    if count:
        reasons.append("message_frequency")
    return Recommendation(selected.channel, confidence, tuple(reasons))


async def recommend_channel(session, contact_id: int, purpose: str, *, now: datetime | None = None) -> Recommendation:
    """Recommend only a currently authorized, consented, usable route.

    This read does not create an outbox, send, call a provider, or grant future
    delivery authorization. Recheck every gate when an actual delivery is made.
    """
    from types import SimpleNamespace

    from sqlalchemy import and_, or_, select

    from app.core.access_catalog import workspace_grants
    from app.core.permissions import get_current_user, has_permission
    from app.core.tenant_context import current_tenant_id, is_bypass
    from app.integrations.email.smtp_send import _valid_mailbox
    from app.models import Tenant, TenantUser, User, WorkspaceRole
    from app.models.communications.message import ContactChannelStat
    from app.models.contacts.core import PURPOSES, Contact, ContactIdentity
    from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
    from app.models.managers.contact_manager import ContactUnavailable
    from app.services.communications.outbox_store import OutboxConflict, OutboxUnavailable, resolve_route
    from app.types import ActionType

    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None or purpose not in PURPOSES or type(contact_id) is not int or contact_id <= 0:
        raise ValueError("invalid_recommendation_request")
    tenant, actor = current_tenant_id(), get_current_user()
    if is_bypass() or type(tenant) is not int or tenant <= 0 or actor is None or not actor.is_active:
        raise PermissionError("recommendation_denied")
    user_id = await session.scalar(
        select(User.id).where(User.id == actor.id, User.is_active.is_(True)).with_for_update(read=True)
    )
    workspace_id = await session.scalar(
        select(Tenant.id).where(Tenant.id == tenant, Tenant.is_active.is_(True)).with_for_update(read=True)
    )
    membership = await session.scalar(
        select(TenantUser)
        .where(TenantUser.tenant_id == tenant, TenantUser.user_id == actor.id, TenantUser.is_active.is_(True))
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    role = (
        await session.scalar(
            select(WorkspaceRole.codename).where(WorkspaceRole.id == membership.role_id).with_for_update(read=True)
        )
        if membership is not None
        else None
    )
    grants = workspace_grants(role)
    if (
        user_id is None
        or workspace_id is None
        or ("contact", ActionType.VIEW) not in grants
        or ("unifiedmessage", ActionType.VIEW) not in grants
        or not has_permission(actor, "contact", ActionType.VIEW)
        or not has_permission(actor, "comms.message", ActionType.VIEW)
    ):
        raise PermissionError("recommendation_denied")
    contact = await session.scalar(
        select(Contact)
        .where(Contact.tenant_id == tenant, Contact.id == contact_id, Contact.merged_into_id.is_(None))
        .with_for_update()
    )
    if contact is None:
        raise ContactUnavailable("contact_unavailable")

    # The only currently evidenced sender route is email (P15-T06). Identity
    # alone and recorded consent never imply a usable route. Telegram/MAX bot
    # evidence is not in the current model; do not infer it from identifiers.
    pair = (
        await session.execute(
            select(ContactIdentity.id, WorkspaceConnection.id)
            .join(
                WorkspaceConnection,
                and_(
                    WorkspaceConnection.tenant_id == tenant,
                    WorkspaceConnection.owner_user_id == actor.id,
                    or_(
                        ContactIdentity.connection_id.is_(None), ContactIdentity.connection_id == WorkspaceConnection.id
                    ),
                ),
            )
            .join(WorkspaceConnectionSecret, WorkspaceConnectionSecret.connection_id == WorkspaceConnection.id)
            .where(
                ContactIdentity.tenant_id == tenant,
                ContactIdentity.contact_id == contact_id,
                ContactIdentity.kind == "email",
                ContactIdentity.verified.is_(True),
                WorkspaceConnection.is_active.is_(True),
                WorkspaceConnection.state == "verified",
                WorkspaceConnection.provider_id.in_(("mailru_mail", "yandex_mail")),
                WorkspaceConnection.auth_method == "app_password",
                WorkspaceConnection.config["send_enabled"].as_boolean().is_(True),
                WorkspaceConnection.config["account_login"].as_string() != "",
                WorkspaceConnectionSecret.secret_encrypted != "",
            )
            .order_by(WorkspaceConnection.id, ContactIdentity.id)
            .limit(1)
        )
    ).first()
    if pair is None:
        return rank_channels([], now=now)
    message = SimpleNamespace(tenant_id=tenant, author_id=actor.id, contact_id=contact_id, purpose=purpose)
    target = SimpleNamespace(channel="email", identity_id=pair[0], connection_id=pair[1])
    try:
        # Reuse the accepted current route/consent checks; no network call.
        _, _, connection, _ = await resolve_route(session, message, target, now)
    except (PermissionError, OutboxUnavailable, OutboxConflict):
        return rank_channels([], now=now)
    config = connection.config
    if (
        not isinstance(config, dict)
        or config.get("send_enabled") is not True
        or not _valid_mailbox(config.get("account_login"))
    ):
        return rank_channels([], now=now)
    stat = await session.scalar(
        select(ContactChannelStat).where(
            ContactChannelStat.tenant_id == tenant,
            ContactChannelStat.contact_id == contact_id,
            ContactChannelStat.channel == "email",
        )
    )
    signal = ChannelSignals(
        "email",
        consented=True,
        reachable=True,
        inbound_count=int(stat.inbound_count or 0) if stat else 0,
        outbound_count=int(stat.outbound_count or 0) if stat else 0,
        last_inbound_at=stat.last_inbound_at if stat else None,
    )
    return rank_channels([signal], preferred_channel=contact.preferred_channel, now=now)
