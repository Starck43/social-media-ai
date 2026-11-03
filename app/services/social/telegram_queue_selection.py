"""Metadata-only Telegram account pins created at trusted collect enqueue.

Never accept a supplied snapshot as authority. A queued job must not silently
follow a source/grant rebound to another account after enqueue.
"""

from dataclasses import asdict

from sqlalchemy import select

from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Platform, Source
from app.models import WorkspaceAuthorizationGrant as Grant
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import SourceGrantSelection, _positive, source_grant_selection
from app.types import PlatformType

_QUEUE_BINDINGS = "_telegram_source_grants"


async def _snapshot_queue_bindings(session, payload, tenant_id):
    payload = dict(payload or {})
    # Caller payloads/replayed snapshots never supply their own authority.
    payload.pop(_QUEUE_BINDINGS, None)
    source_ids = payload.get("source_ids")
    if not source_ids:
        # Legacy/taskless implicit-all jobs must be re-admitted explicitly to
        # use the new runtime. Do not widen them by guessing a source set.
        return payload
    if (
        type(source_ids) is not list
        or len(source_ids) > 10000
        or any(type(value) is not int or value <= 0 for value in source_ids)
        or len(source_ids) != len(set(source_ids))
    ):
        raise AuthorizationUnavailable()
    sources = (
        (
            await session.execute(
                select(Source)
                .join(Platform, Platform.id == Source.platform_id)
                .where(
                    Source.id.in_(source_ids),
                    Source.tenant_id == _positive(tenant_id),
                    Source.is_active.is_(True),
                    Platform.platform_type == PlatformType.TELEGRAM,
                    Platform.is_active.is_(True),
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    pins = {}
    for source in sources:
        params = source.params or {}
        if params.get("mode", "auto") == "api" or "telegram_authorization" not in params:
            continue
        selected = source_grant_selection(source)
        authorization_id = await session.scalar(
            select(Grant.authorization_id)
            .join(Authorization, Authorization.id == Grant.authorization_id)
            .where(
                Grant.id == selected.grant_id,
                Grant.tenant_id == tenant_id,
                Grant.owner_user_id == selected.owner_user_id,
                Grant.authorization_revision == selected.authorization_revision,
                Grant.is_active.is_(True),
                Authorization.owner_user_id == selected.owner_user_id,
                Authorization.revision == selected.authorization_revision,
                Authorization.provider_id == "telegram",
                Authorization.auth_method == "mtproto",
                Authorization.state == "active",
            )
        )
        pins[str(source.id)] = {**asdict(selected), "authorization_id": _positive(authorization_id)}
    payload[_QUEUE_BINDINGS] = pins
    return payload


def _queued_authorization_id(payload, selection):
    pins = payload.get(_QUEUE_BINDINGS)
    raw = pins.get(str(selection.source_id)) if type(pins) is dict else None
    fields = set(SourceGrantSelection.__dataclass_fields__)
    if type(raw) is not dict or set(raw) != fields | {"authorization_id"}:
        raise AuthorizationUnavailable()
    expected = SourceGrantSelection(**{key: raw[key] for key in fields})
    if expected != selection:
        raise AuthorizationUnavailable()
    return _positive(raw["authorization_id"])
