"""Explicit, optimistic source selection; no automatic grant or owner fallback."""

from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import select, text

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.tenant_context import is_bypass
from app.models import PersonalConnectorAuthorization as Authorization
from app.models import Platform, Source, TenantUser
from app.models import WorkspaceAuthorizationGrant as Grant
from app.models import WorkspaceRole
from app.models.managers.workspace_connection_manager import _authorize
from app.services.social.connector_authorizations import AuthorizationUnavailable, _account, _check_reusable, _positive
from app.services.social.telegram_grant_access import _read_source_grant, source_grant_selection
from app.types import ActionType, PlatformType


class SourceBindingUnconfirmed(RuntimeError):
    def __init__(self):
        super().__init__("source_binding_unconfirmed")


async def _rights(session, tenant_id, actor_id, *, write=False):
    if is_bypass():
        raise AuthorizationUnavailable()
    await _authorize(session, tenant_id, actor_id, require_source_view=True)
    # _authorize holds member/role SHARE locks. UPDATE, not CREATE, is required
    # to replace a source's account choice. Platform/operator roles do not help.
    role = await session.scalar(
        select(WorkspaceRole.codename)
        .join(TenantUser, TenantUser.role_id == WorkspaceRole.id)
        .where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.user_id == actor_id,
            TenantUser.channel == "web",
            TenantUser.is_active.is_(True),
        )
    )
    can_edit = ("source", ActionType.UPDATE) in workspace_grants(role) if role is not None else False
    if write and not can_edit:
        raise AuthorizationUnavailable()
    return can_edit


def _binding_token(source):
    params = source.params or {}
    if type(params) is not dict:
        raise AuthorizationUnavailable()
    # Only binding-related fields: unrelated params may be edited concurrently
    # and are preserved, not replaced by an old browser form's full JSON blob.
    raw = {
        "source_id": source.id,
        "tenant_id": source.tenant_id,
        "platform_id": source.platform_id,
        "external_id": source.external_id,
        "owner_user_id": source.owner_user_id,
        "mode": params.get("mode", "auto"),
        "token_owner": params.get("token_owner"),
        "telegram_authorization": params.get("telegram_authorization"),
    }
    try:
        return hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    except (TypeError, ValueError):
        raise AuthorizationUnavailable() from None


def _projection(source, actor_id):
    _binding_token(source)  # Reject malformed imported params before projection.
    grant_id = revision = None
    state = "unselected"
    if "telegram_authorization" in (source.params or {}):
        try:
            selected = source_grant_selection(source)
            if selected.owner_user_id == actor_id:
                grant_id, revision = selected.grant_id, selected.authorization_revision
                state = "selected"
            else:
                state = "other_owner"
        except AuthorizationUnavailable:
            state = "invalid"
    return {
        "source_id": source.id,
        "name": source.name,
        "external_id": source.external_id,
        "binding_token": _binding_token(source),
        "selection_state": state,
        "grant_id": grant_id,
        "authorization_revision": revision,
    }


def _source_query(tenant_id):
    return (
        select(Source)
        .join(Platform, Platform.id == Source.platform_id)
        .where(
            Source.tenant_id == _positive(tenant_id),
            Source.is_active.is_(True),
            Platform.is_active.is_(True),
            Platform.platform_type == PlatformType.TELEGRAM,
        )
    )


async def _source(session, tenant_id, source_id, *, lock=False):
    statement = _source_query(tenant_id).where(Source.id == _positive(source_id))
    if lock:
        statement = statement.with_for_update(of=Source)
    row = (await session.execute(statement.execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        raise AuthorizationUnavailable()
    return row


async def _choices(session, tenant_id, actor_id):
    # Explicit metadata-only projection, never load an envelope for a dropdown.
    rows = (
        (
            await session.execute(
                select(
                    Grant.id.label("grant_id"),
                    Grant.authorization_id,
                    Grant.authorization_revision,
                    Authorization.subject_key,
                )
                .join(Authorization, Authorization.id == Grant.authorization_id)
                .where(
                    Grant.tenant_id == tenant_id,
                    Grant.owner_user_id == actor_id,
                    Grant.is_active.is_(True),
                    Authorization.owner_user_id == actor_id,
                    Authorization.state == "active",
                    Authorization.provider_id == "telegram",
                    Authorization.auth_method == "mtproto",
                    Authorization.revision == Grant.authorization_revision,
                    Authorization.secret_encrypted != "",
                )
                .order_by(Grant.id)
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


class TelegramSourceBindings:
    async def set(self, **selection):
        try:
            return await self._set(**selection)
        except AuthorizationUnavailable:
            raise
        except PermissionError:
            raise
        except Exception:
            # Includes SQL errors whose bound JSON might contain unrelated
            # legacy source params. Unknown commits must not be auto-replayed.
            raise SourceBindingUnconfirmed() from None

    async def list_sources(self, *, tenant_id, actor_id, after_id=0):
        if type(after_id) is not int or after_id < 0:
            raise AuthorizationUnavailable()
        async with async_session_maker() as session, session.begin():
            can_edit = await _rights(session, tenant_id, actor_id)
            rows = (
                (
                    await session.execute(
                        _source_query(tenant_id).where(Source.id > after_id).order_by(Source.id).limit(101)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "sources": [_projection(row, actor_id) for row in rows[:100]],
                "can_edit": can_edit,
                "next_after": rows[99].id if len(rows) > 100 else None,
            }

    async def get(self, *, tenant_id, actor_id, source_id):
        async with async_session_maker() as session, session.begin():
            can_edit = await _rights(session, tenant_id, actor_id)
            source = await _source(session, tenant_id, source_id)
            return {
                "source": _projection(source, actor_id),
                "choices": await _choices(session, tenant_id, actor_id),
                "can_edit": can_edit,
            }

    async def _set(
        self,
        *,
        tenant_id,
        actor_id,
        source_id,
        expected_binding,
        grant_id=None,
        authorization_id=None,
        authorization_revision=None,
    ):
        if not isinstance(expected_binding, str) or not re.fullmatch("[0-9a-f]{64}", expected_binding):
            raise AuthorizationUnavailable()
        if len({value is None for value in (grant_id, authorization_id, authorization_revision)}) != 1:
            raise AuthorizationUnavailable()
        async with async_session_maker() as session, session.begin():
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            # Match the runtime gate: account -> grant -> source -> rights.
            # No mutation or result is exposed until fresh actor rights pass.
            account = grant = None
            if grant_id is not None:
                _positive(authorization_revision)
                # Exact user-selected account, then grant -> source. Never
                # follow a grant rebound after the picker was displayed.
                _positive(grant_id)
                account = await _account(session, actor_id, _positive(authorization_id))
                grant = (
                    await session.execute(
                        select(Grant)
                        .where(
                            Grant.id == grant_id,
                            Grant.tenant_id == tenant_id,
                            Grant.owner_user_id == actor_id,
                            Grant.authorization_id == account.id,
                            Grant.authorization_revision == authorization_revision,
                            Grant.is_active.is_(True),
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if grant is None or account.revision != authorization_revision:
                    raise AuthorizationUnavailable()
                _check_reusable(account)
            source = await _source(session, tenant_id, source_id, lock=True)
            await _rights(session, tenant_id, actor_id, write=True)
            if _binding_token(source) != expected_binding:
                raise AuthorizationUnavailable()
            params = dict(source.params or {})
            if grant is None:
                # Clearing is NOT an instruction to switch owner/account/mode.
                # User/auto collection now fails closed until explicitly rebound.
                params.pop("telegram_authorization", None)
            else:
                params["mode"] = "user"
                params["token_owner"] = actor_id
                params["telegram_authorization"] = {
                    "grant_id": grant.id,
                    "authorization_revision": authorization_revision,
                }
            source.params = params
            await session.flush()
            if grant is not None:
                snapshot, _ = await _read_source_grant(session, source_grant_selection(source), lock_owner_rights=True)
                if snapshot.authorization_id != account.id:
                    raise AuthorizationUnavailable()
            await _rights(session, tenant_id, actor_id, write=True)
            return _projection(source, actor_id)


telegram_source_bindings = TelegramSourceBindings()
