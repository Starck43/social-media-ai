"""Owner-only restrictive policy edits, serialized with readers and scan admission."""

from __future__ import annotations

import re
import secrets

from app.core.database import async_session_maker
from app.integrations.file_workspace.policy import FileOperation, _components
from app.models import ActionAuditEvent
from app.models.managers.file_catalog_manager import _cloud_policy, _policy_key
from app.models.managers.workspace_connection_manager import _authorize, _connection


class FolderPolicyInputError(ValueError):
    pass


class FolderPolicyConflict(ValueError):
    pass


def _path(value):
    try:
        return "disk:/" + "/".join(_components(value))
    except (ValueError, TypeError):
        raise FolderPolicyInputError("invalid_folder") from None


def policy_view(connection, path=None):
    policy, root = _cloud_policy(connection)
    path = _path(root if path is None else path)
    target, selected = _components(path), _components(root)
    if target[: len(selected)] != selected:
        raise FolderPolicyInputError("outside_selected_root")
    rules = connection.config["file_policy"]["restrictions"]
    denied_by = {"list": [], "read": []}
    for rule in rules:
        folder = _components(rule["path"])
        if target[: len(folder)] == folder:
            for operation in rule["deny"]:
                denied_by[operation].append(_path(rule["path"]))
    return {
        "connection_id": connection.id,
        "label": connection.label,
        "root": _path(root),
        "path": path,
        "revision": _policy_key(connection.config),
        "rules": [{"path": _path(r["path"]), "deny": r["deny"]} for r in rules],
        "list_allowed": policy.permits(path, FileOperation.LIST),
        "read_allowed": policy.permits(path, FileOperation.READ),
        "denied_by": denied_by,
    }


async def get_folder_permissions(*, tenant_id, actor_id, connection_id, path=None):
    async with async_session_maker() as session, session.begin():
        await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
        connection = await _connection(session, tenant_id, actor_id, connection_id)
        return policy_view(connection, path)


async def set_folder_permissions(*, tenant_id, actor_id, connection_id, path, access, expected_revision):
    """Change one local restriction; read_only removes only that folder's denial.

    Parent denials remain authoritative. The selected root is immutable here.
    No provider ACL, sharing, credentials or write capability is changed. Audit
    and config commit together; failures roll back both. Existing catalog/receipt
    fingerprints invalidate queued work and cached content after any change.
    """
    if access not in {"none", "read_only"}:
        raise FolderPolicyInputError("invalid_access")
    if type(expected_revision) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_revision) is None:
        raise FolderPolicyInputError("invalid_revision")
    async with async_session_maker() as session, session.begin():
        await _authorize(session, tenant_id, actor_id, write=True, require_source_view=True)
        connection = await _connection(session, tenant_id, actor_id, connection_id)
        view = policy_view(connection, path)
        if view["revision"] != expected_revision:
            raise FolderPolicyConflict("policy_changed")
        rules = [rule for rule in view["rules"] if rule["path"] != view["path"]]
        if access == "none":
            rules.append({"path": view["path"], "deny": ["list", "read"]})
        if len(rules) > 100:
            raise FolderPolicyInputError("too_many_restrictions")
        config = {"file_policy": {"version": 1, "root": view["root"], "restrictions": rules}}
        old_policy = connection.config["file_policy"]
        previous = {k: v for k, v in old_policy.items() if k != "epoch"}
        if config["file_policy"] != previous:
            config["file_policy"]["epoch"] = secrets.token_hex(16)
            connection.config = config
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="user",
                actor_id=actor_id,
                action="files.policy_update",
                resource_kind="workspace_connection",
                resource_id=connection_id,
            )
            await session.flush()
        return policy_view(connection, view["path"])
