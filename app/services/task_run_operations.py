"""Bounded, read-only operations supported by the canonical worker."""

import asyncio
import hashlib
import hmac
import imaplib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from email import message_from_bytes
from email.utils import getaddresses
from types import MappingProxyType

from sqlalchemy import select

from app.core.config import settings
from app.integrations.email.imap_sync import (
    ImapSyncError,
    _close_imap_client,
    _open_pinned_imap_client,
    get_imap_folders,
    validate_folder_list,
)
from app.integrations.task_resources import WorkspaceAccountPin
from app.models import WorkspaceConnection, WorkspaceConnectionSecret
from app.utils.crypto import decrypt_secret


@dataclass(frozen=True)
class StepOutput:
    output: list
    count: int


_UID = re.compile(rb"\bUID (\d+)\b", re.I)
_HEADER = "(UID BODY.PEEK[HEADER.FIELDS (FROM)]<0.4096>)"


def _criteria(window, effective_at):
    if window is None:
        return ("ALL",)
    end = effective_at.date() if window.type == "rolling" else window.end
    start = end - timedelta(days=window.days - 1) if window.type == "rolling" else window.start

    def imap_date(day):
        return f"{day.day:02d}-{imaplib.Months[day.month]}-{day.year:04d}"

    return ("SINCE", imap_date(start), "BEFORE", imap_date(end + timedelta(days=1)))


def _read_folder(provider, login, password, folder, limit, criteria):
    """Fetch only the newest matching UIDs in the selected folder without changing flags."""
    client = _open_pinned_imap_client(provider, login, password)
    try:
        status, _ = client.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise ImapSyncError("folder_unavailable")
        validity = (client.untagged_responses.get("UIDVALIDITY") or [b""])[-1]
        if not isinstance(validity, bytes) or not validity.isdigit() or int(validity) <= 0:
            raise ImapSyncError("invalid_uidvalidity")
        status, response = client.uid("SEARCH", None, *criteria)
        if status != "OK" or not response or not isinstance(response[0], bytes):
            raise ImapSyncError("header_search_failed")
        raw_ids = response[0].split()
        if any(not item.isdigit() or len(item) > 10 for item in raw_ids):
            raise ImapSyncError("invalid_uid")
        uids = sorted({int(item) for item in raw_ids})[-limit:]
        rows = []
        for start in range(0, len(uids), 32):
            batch = uids[start : start + 32]
            status, data = client.uid("FETCH", ",".join(map(str, batch)), _HEADER)
            if status != "OK":
                raise ImapSyncError("header_fetch_failed")
            fetched = set()
            for item in data:
                if not isinstance(item, tuple):
                    continue
                descriptor, header = item
                match = _UID.search(descriptor)
                uid = int(match.group(1)) if match else None
                if uid not in batch or uid in fetched or not isinstance(header, bytes) or len(header) > 4096:
                    raise ImapSyncError("invalid_header_response")
                fetched.add(uid)
                addresses = getaddresses(message_from_bytes(header).get_all("From", []))
                address = addresses[0][1] if len(addresses) == 1 else ""
                rows.append((int(validity), uid, address))
            if fetched != set(batch):
                raise ImapSyncError("incomplete_header_read")
        return rows
    finally:
        _close_imap_client(client)


async def read_headers(session, context, step, inputs, resources):
    binding = next(binding for binding in context.plan.bindings if binding.name == step.bindings[0])
    selection = binding.selection()
    if not any(
        r.tenant_id == context.tenant_id
        and r.actor_id == context.principal_id
        and r.selection == selection
        and r.capability_id == binding.capability
        for r in resources
    ):
        raise PermissionError("resource_unavailable")
    connection = await session.scalar(
        select(WorkspaceConnection)
        .where(
            WorkspaceConnection.id == selection.resource_id,
            WorkspaceConnection.tenant_id == context.tenant_id,
            WorkspaceConnection.owner_user_id == context.principal_id,
            WorkspaceConnection.is_active.is_(True),
            WorkspaceConnection.state == "verified",
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        connection is None
        or selection.workspace_account != WorkspaceAccountPin.from_connection(connection)
        or selection.path not in get_imap_folders(connection)
    ):
        raise PermissionError("resource_unavailable")
    (folder,) = validate_folder_list([selection.path])
    ciphertext = await session.scalar(
        select(WorkspaceConnectionSecret.secret_encrypted)
        .where(WorkspaceConnectionSecret.connection_id == connection.id)
        .with_for_update(read=True)
    )
    if not ciphertext:
        raise PermissionError("reauth_required")
    password = decrypt_secret(ciphertext)
    if not password:
        raise PermissionError("reauth_required")
    login = str((connection.config or {}).get("account_login") or connection.account_key)
    rows = await asyncio.to_thread(
        _read_folder,
        connection.provider_id,
        login,
        password,
        folder,
        step.max_items,
        _criteria(step.window, context.effective_at),
    )
    output = []
    for validity, uid, address in rows:
        # A sender key is meaningful only inside its original account. An
        # anonymous or ambiguous sender stays separate for each message.
        key = [context.tenant_id, connection.id, address or [folder, validity, uid]]
        digest = hmac.new(settings.SECRET_KEY.encode(), json.dumps(key).encode(), hashlib.sha256).hexdigest()
        output.append([connection.id, folder, validity, uid, digest])
    return StepOutput(output, len(output))


async def group_by_sender(session, context, step, inputs, resources):
    groups = {}
    for source in inputs["mail"]:
        for connection, folder, validity, uid, sender in source:
            groups.setdefault(sender, set()).add((connection, folder, validity, uid))
    output = [[sender, sorted(messages)] for sender, messages in sorted(groups.items())]
    return StepOutput(output, len(output))


OPERATIONS = MappingProxyType({"mail.read_headers": read_headers, "mail.group_by_sender": group_by_sender})
