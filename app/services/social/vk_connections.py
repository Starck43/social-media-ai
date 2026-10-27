"""Authorized resolution and safe metadata for explicitly selected VK access.

Task selections are identifiers, never credentials or authority. Every call
reloads the interactive actor, active workspace and membership before reading
the exact actor-owned vault row or resolving the deployment's service token.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.services.social.vk_access import (
    METHOD_MODES,
    VKAccessDecision,
    VKAccessSnapshot,
    VKSelection,
    VKSelectionError,
    decide_vk_access,
    parse_vk_selection,
)

_KNOWN_SCOPES = frozenset(
    {
        "vkid.personal_info",
        "email",
        "phone",
        "offline",
        "wall",
        "groups",
        "users",
        "photos",
    }
)


class VKConnectionDenied(PermissionError):
    """Fixed safe error code for an unavailable or unauthorized selection."""


def _deny(code: str) -> VKConnectionDenied:
    return VKConnectionDenied(code)


async def _fresh_context(*, tenant_id: int, actor_id: int) -> tuple[Any, Any, Any]:
    """Require the current interactive identity and reload active rights."""
    from app.core.permissions import get_current_user
    from app.core.tenant_context import current_tenant_id, is_bypass
    from app.models import Tenant, User
    from app.models.managers.tenant_manager import tenant_users
    from app.types import ActionType

    actor = get_current_user()
    if (
        is_bypass()
        or type(tenant_id) is not int
        or tenant_id <= 0
        or type(actor_id) is not int
        or actor_id <= 0
        or current_tenant_id() != tenant_id
        or type(getattr(actor, "id", None)) is not int
        or actor.id != actor_id
    ):
        raise _deny("vk_access_actor_or_workspace_required")

    user = await User.objects.get(id=actor_id, is_active=True)
    tenant = await Tenant.objects.get(id=tenant_id, is_active=True)
    if user is None or tenant is None:
        raise _deny("vk_access_actor_or_workspace_inactive")
    memberships = await tenant_users.web_memberships(actor_id)
    membership = next((row for row in memberships if row.tenant_id == tenant_id and row.is_active), None)
    if membership is None:
        raise _deny("vk_access_membership_required")
    grants = await tenant_users.member_grants(membership)
    if ("source", ActionType.ANALYZE) not in grants:
        raise _deny("vk_access_analysis_permission_required")
    if ("agenttask", ActionType.UPDATE) not in grants:
        raise _deny("vk_access_task_permission_required")
    return user, tenant, membership


def _safe_scopes(raw: object) -> frozenset[str]:
    """Parse only known grant names from persisted VK OAuth scope metadata."""
    if not isinstance(raw, str):
        return frozenset()
    values = raw.replace(",", " ").split()
    return frozenset(value for value in values if value in _KNOWN_SCOPES)


def _method_grants(raw: object) -> frozenset[str] | None:
    """Return explicitly configured known methods; unknown config fails closed."""
    if not isinstance(raw, (list, tuple)) or any(type(value) is not str for value in raw):
        return None
    if any(value not in METHOD_MODES for value in raw):
        return None
    return frozenset(raw)


def _method_scope_requirements(raw: object) -> dict[str, frozenset[str]]:
    """Validate a trusted method-to-scope declaration; malformed means unknown."""
    if not isinstance(raw, Mapping):
        return {}
    requirements: dict[str, frozenset[str]] = {}
    for method, scopes in raw.items():
        if (
            type(method) is not str
            or method not in METHOD_MODES
            or not isinstance(scopes, list)
            or any(type(scope) is not str or scope not in _KNOWN_SCOPES for scope in scopes)
        ):
            return {}
        requirements[method] = frozenset(scopes)
    return requirements


async def _snapshot(
    selection: VKSelection,
    *,
    tenant_id: int,
    actor_id: int,
    include_credential: bool = False,
) -> tuple[VKAccessSnapshot | None, Any | None]:
    """Load exact connection metadata; second result is the exact vault row."""
    from app.core.config import settings

    if selection.mode == "api":
        configured = bool(settings.VK_SERVICE_KEY)
        if not configured:
            return None, None
        snapshot = VKAccessSnapshot(
            tenant_id=tenant_id,
            mode="api",
            active=True,
            granted_scopes=_safe_scopes(" ".join(settings.VK_SERVICE_GRANTED_SCOPES or [])),
            allowed_methods=_method_grants(settings.VK_SERVICE_METHOD_GRANTS),
            method_scopes=_method_scope_requirements(settings.VK_SERVICE_METHOD_SCOPES),
        )
        return snapshot, None

    from app.models.identity.user_credential import UserCredential
    from app.models.managers.user_credential_manager import user_credentials

    query = user_credentials.filter(
        id=selection.credential_id,
        user_id=actor_id,
        platform="vk",
        kind="user_token",
    )
    if include_credential:
        row = await query.first()
    else:
        row = await query.values(
            UserCredential.id,
            UserCredential.user_id,
            UserCredential.is_active,
            UserCredential.expires_at,
            UserCredential.meta,
        ).first_row()
    if row is None:
        return None, None
    meta = row.meta if isinstance(row.meta, Mapping) else {}
    grants = _method_grants(meta.get("vk_method_grants")) if "vk_method_grants" in meta else None
    method_scopes = _method_scope_requirements(meta.get("vk_method_scopes"))
    return (
        VKAccessSnapshot(
            tenant_id=tenant_id,
            mode="user",
            credential_id=row.id,
            owner_user_id=row.user_id,
            active=row.is_active is True,
            expires_at=row.expires_at,
            granted_scopes=_safe_scopes(meta.get("scope")),
            allowed_methods=grants,
            method_scopes=method_scopes,
        ),
        row,
    )


async def _authorized_snapshot(
    selection: VKSelection,
    *,
    tenant_id: int,
    actor_id: int,
    include_credential: bool = False,
) -> tuple[VKAccessSnapshot | None, Any | None, frozenset[int]]:
    try:
        await _fresh_context(tenant_id=tenant_id, actor_id=actor_id)
    except VKConnectionDenied:
        raise
    except Exception:
        raise _deny("vk_access_authority_unavailable") from None
    active_member_ids = frozenset({actor_id})
    try:
        snapshot, row = await _snapshot(
            selection,
            tenant_id=tenant_id,
            actor_id=actor_id,
            include_credential=include_credential,
        )
    except Exception:
        raise _deny("vk_access_connection_unavailable") from None
    return snapshot, row, active_member_ids


async def check_selected_access(
    selection: VKSelection | Mapping[str, Any],
    *,
    tenant_id: int,
    actor_id: int,
    required_methods: frozenset[str],
    required_scopes: frozenset[str] = frozenset(),
) -> VKAccessDecision:
    """Authorize an explicit mode against fresh actor/workspace/credential facts."""
    try:
        parsed = selection if isinstance(selection, VKSelection) else parse_vk_selection(selection)
    except (VKSelectionError, TypeError, AttributeError) as exc:
        raise _deny("vk_access_selection_invalid") from None
    snapshot, _, members = await _authorized_snapshot(parsed, tenant_id=tenant_id, actor_id=actor_id)
    return decide_vk_access(
        parsed,
        snapshot,
        tenant_id=tenant_id,
        active_member_ids=members,
        required_methods=required_methods,
        required_scopes=required_scopes,
    )


async def resolve_selected_token(
    selection: VKSelection | Mapping[str, Any],
    *,
    tenant_id: int,
    actor_id: int,
    required_methods: frozenset[str],
    required_scopes: frozenset[str] = frozenset(),
) -> str:
    """Reveal only the pinned credential after a fresh successful capability check."""
    try:
        parsed = selection if isinstance(selection, VKSelection) else parse_vk_selection(selection)
    except (VKSelectionError, TypeError, AttributeError):
        raise _deny("vk_access_selection_invalid") from None
    snapshot, row, members = await _authorized_snapshot(
        parsed, tenant_id=tenant_id, actor_id=actor_id, include_credential=True
    )
    decision = decide_vk_access(
        parsed,
        snapshot,
        tenant_id=tenant_id,
        active_member_ids=members,
        required_methods=required_methods,
        required_scopes=required_scopes,
    )
    if not decision.allowed:
        raise _deny(decision.reason)
    if parsed.mode == "api":
        from app.services.social.credentials import resolve_token

        try:
            token = await resolve_token("vk", kinds=("service_token",))
        except Exception:
            raise _deny("vk_access_credential_unavailable") from None
        if not token:
            raise _deny("vk_access_connection_missing")
        return token
    if row is None or row.is_active is not True:
        raise _deny("vk_access_connection_revoked")
    try:
        token = row.reveal()
    except Exception:
        raise _deny("vk_access_credential_unavailable") from None
    if not isinstance(token, str) or not token:
        raise _deny("vk_access_credential_unavailable")
    return token


def _account_id(meta: Mapping[str, Any]) -> int | None:
    value = meta.get("user_id")
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and len(value) <= 20 and value.isascii() and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _expires_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _catalog_methods(raw: object) -> list[str] | None:
    methods = _method_grants(raw)
    return sorted(methods) if methods is not None else None


def _catalog_method_scopes(raw: object) -> dict[str, list[str]] | None:
    requirements = _method_scope_requirements(raw)
    if not requirements:
        return None
    return {method: sorted(scopes) for method, scopes in sorted(requirements.items())}


def _vault_state(row: Any) -> tuple[str, bool]:
    if row.is_active is not True:
        return "revoked", True
    expiry = row.expires_at
    if expiry is None:
        return "connected", False
    moment = expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)
    if moment > datetime.now(timezone.utc):
        return "connected", False
    meta = row.meta if isinstance(row.meta, Mapping) else {}
    if isinstance(meta.get("refresh_token"), str) and meta["refresh_token"]:
        return "renewal_available", False
    return "reauthorization_required", True


def _state_label(state: str) -> str:
    return {
        "connected": "Подключено",
        "revoked": "Отключено",
        "renewal_available": "Требуется продление",
        "reauthorization_required": "Требуется повторная авторизация",
    }.get(state, "Статус недоступен")


async def connection_catalog(*, tenant_id: int, actor_id: int) -> dict[str, Any]:
    """Return bounded caller-owned and deployment VK connection metadata only."""
    try:
        await _fresh_context(tenant_id=tenant_id, actor_id=actor_id)
    except VKConnectionDenied:
        raise
    except Exception:
        raise _deny("vk_access_authority_unavailable") from None
    from app.core.config import settings
    from app.models.identity.user_credential import UserCredential
    from app.models.managers.user_credential_manager import user_credentials

    try:
        rows = await (
            user_credentials.filter(user_id=actor_id, platform="vk", kind="user_token")
            .order_by(UserCredential.id.desc())
            .limit(50)
            .values(
                UserCredential.id,
                UserCredential.is_active,
                UserCredential.expires_at,
                UserCredential.meta,
            )
            .rows()
        )
    except Exception:
        raise _deny("vk_access_connection_unavailable") from None
    connections: list[dict[str, Any]] = []
    for row in rows:
        meta = row.meta if isinstance(row.meta, Mapping) else {}
        state, reauth = _vault_state(row)
        account_id = _account_id(meta)
        label = f"Личный аккаунт VK ID {account_id}"
        if account_id is None:
            label = f"Личное подключение #{row.id}"
        connections.append(
            {
                "selection": {"mode": "user", "credential_id": row.id},
                "label": label,
                "mode": "user",
                "credential_id": row.id,
                "state": state,
                "state_label": _state_label(state),
                "scopes": sorted(_safe_scopes(meta.get("scope"))),
                "allowed_methods": _catalog_methods(meta.get("vk_method_grants")),
                "method_scopes": _catalog_method_scopes(meta.get("vk_method_scopes")),
                "expires_at": _expires_iso(row.expires_at),
                "account_id": account_id,
                "reauthorization_required": reauth,
            }
        )
    service_configured = bool(settings.VK_SERVICE_KEY)
    if service_configured:
        methods = _method_grants(settings.VK_SERVICE_METHOD_GRANTS)
        connections.insert(
            0,
            {
                "selection": {"mode": "api"},
                "label": "Сервисный доступ VK",
                "mode": "api",
                "credential_id": None,
                "state": "connected",
                "state_label": _state_label("connected"),
                "scopes": sorted(_safe_scopes(" ".join(settings.VK_SERVICE_GRANTED_SCOPES or []))),
                "allowed_methods": sorted(methods) if methods is not None else None,
                "method_scopes": _catalog_method_scopes(settings.VK_SERVICE_METHOD_SCOPES),
                "expires_at": None,
                "account_id": None,
                "reauthorization_required": False,
            },
        )
    return {
        "connections": connections,
        "method_modes": {method: sorted(modes) for method, modes in METHOD_MODES.items()},
    }
