"""Read-only incremental IMAP header synchronization (`P15-T01`).

Key invariants:
- Metadata-only per D3 and D11: fetches only RFC 5322 headers (`From`, `To`, `Cc`,
  `Subject`, `Date`, `Message-ID`, `In-Reply-To`, `References`). Never fetches or
  persists message bodies or attachments.
- Never modifies server message flags: selects mailboxes with `readonly=True` and
  uses `BODY.PEEK[HEADER.FIELDS ...]` exclusively (`STORE` and non-PEEK `BODY[...]`
  are forbidden).
- Tracks `(uidvalidity, last_uid)` per folder in `WorkspaceConnection.config["imap_sync_state"]`.
  On `UIDVALIDITY` change, resets `last_uid` and re-scans with
  `dedupe_by_external_message_id_hash=True` so existing messages rebind their UID
  without duplicating rows or stats.
- Reuses existing `WorkspaceConnection` + `WorkspaceConnectionSecret` credentials
  per D12.
- Isolates outbound host resolution in `_resolve_imap_target` per D13 so P14-T09's
  `app.core.outbound.acquire_outbound_target` seam is wired at a single call site.
"""

from __future__ import annotations

import asyncio
import email
import hashlib
import imaplib
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Callable

from sqlalchemy import select

from app.core.outbound import OutboundRefusal, StaticPolicy, acquire_outbound_target
from app.core.tenant_context import TenantContextError, current_tenant_id
from app.integrations.catalog import CATALOG_VERSION, PROVIDERS, WORKSPACE_PROVIDER_IDS
from app.integrations.mail_check import _PinnedIMAP
from app.models.communications.message import UnifiedMessage
from app.models.integrations.connection import WorkspaceConnection, WorkspaceConnectionSecret
from app.services.communications.ingest import (
    IngestMessageInput,
    extract_reference_ids,
    ingest,
    mark_message_unavailable,
)
from app.utils.crypto import decrypt_secret

DEFAULT_IMAP_FOLDERS: tuple[str, ...] = ("INBOX", "Sent")
MAX_FOLDERS_PER_CONNECTION = 16
DEFAULT_BATCH_SIZE = 50
_FOLDER_NAME_RE = re.compile(r"^[\w./ -]{1,120}$")
_UID_RE = re.compile(rb"UID\s+(\d+)", re.IGNORECASE)
_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)", re.IGNORECASE)
_UIDVALIDITY_RE = re.compile(r"UIDVALIDITY\s+(\d+)", re.IGNORECASE)

FETCH_SPEC = (
    "(UID FLAGS BODY.PEEK[HEADER.FIELDS "
    "(FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)])"
)


class ImapSyncError(RuntimeError):
    """Safe, value-free operational error raised on IMAP sync failures."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ImapEnvelope:
    uid: int
    folder: str
    subject: str | None
    sender: str | None
    recipient: str | None
    participants: tuple[str, ...]
    sent_at: datetime
    message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    is_seen: bool
    header_hash: str


def _resolve_imap_target(provider_id: str) -> tuple[str, str]:
    """Single isolated egress resolution seam for IMAP connections (Q-19 D13 / P14-T09).

    Delegates destination authorization and DNS validation to `app.core.outbound.acquire_outbound_target`
    using `StaticPolicy((host,))`, identical to `app.integrations.mail_check`.
    """
    if provider_id not in WORKSPACE_PROVIDER_IDS:
        raise ImapSyncError("unsupported_provider")
    host = str(dict(PROVIDERS[provider_id].defaults)["imap_host"])
    try:
        target = acquire_outbound_target(host, 993, policy=StaticPolicy((host,)))
    except OutboundRefusal as refusal:
        if isinstance(refusal.__cause__, OSError):
            raise ImapSyncError("connection_failed") from refusal
        raise ImapSyncError("unsafe_address") from refusal
    return target.host, str(target.addresses[0])


def _open_pinned_imap_client(provider_id: str, login: str, password: str) -> _PinnedIMAP:
    """Open an authenticated, IP-pinned TLS 993 IMAP client using P12-T08 rules."""
    host, address = _resolve_imap_target(provider_id)
    client: _PinnedIMAP | None = None
    try:
        client = _PinnedIMAP(host, address)
        auth_login = (
            login.split("@", 1)[0]
            if provider_id == "yandex_mail" and login.lower().endswith("@yandex.ru")
            else login
        )
        client.login(auth_login, password)
        return client
    except ssl.SSLError as exc:
        if client is not None:
            _close_imap_client(client)
        raise ImapSyncError("tls_failed") from exc
    except imaplib.IMAP4.abort as exc:
        if client is not None:
            _close_imap_client(client)
        raise ImapSyncError("service_unavailable") from exc
    except imaplib.IMAP4.error as exc:
        if client is not None:
            _close_imap_client(client)
        raise ImapSyncError("authentication_failed") from exc
    except (OSError, ValueError, UnicodeError) as exc:
        if client is not None:
            _close_imap_client(client)
        raise ImapSyncError("connection_failed") from exc


def _close_imap_client(client: Any) -> None:
    try:
        client.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _decode_mime_header(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    try:
        decoded = str(make_header(decode_header(raw_value))).strip()
        return decoded or None
    except Exception:  # noqa: BLE001
        return raw_value.strip() or None


def _format_address_list(raw_values: list[str]) -> tuple[str | None, tuple[str, ...]]:
    parsed = getaddresses(raw_values)
    formatted: list[str] = []
    addresses_only: list[str] = []
    for real_name, addr in parsed:
        clean_addr = (addr or "").strip()
        clean_name = _decode_mime_header(real_name) if real_name else None
        if clean_addr:
            addresses_only.append(clean_addr)
            if clean_name and clean_name != clean_addr:
                formatted.append(f"{clean_name} <{clean_addr}>")
            else:
                formatted.append(clean_addr)
    primary = formatted[0] if formatted else None
    return primary, tuple(dict.fromkeys(addresses_only))


def parse_raw_header_block(
    *,
    uid: int,
    folder: str,
    header_bytes: bytes,
    flags_bytes: bytes = b"",
    fallback_now: datetime | None = None,
) -> ImapEnvelope:
    """Parse raw RFC 5322 header bytes fetched via `BODY.PEEK[HEADER.FIELDS ...]`."""
    msg = email.message_from_bytes(header_bytes)
    subject = _decode_mime_header(msg.get("Subject"))
    sender_str, sender_addrs = _format_address_list(msg.get_all("From", []))
    to_str, to_addrs = _format_address_list(msg.get_all("To", []))
    _, cc_addrs = _format_address_list(msg.get_all("Cc", []))

    participants = tuple(dict.fromkeys([*sender_addrs, *to_addrs, *cc_addrs]))
    raw_date = msg.get("Date")
    sent_at: datetime | None = None
    if raw_date:
        try:
            parsed_dt = parsedate_to_datetime(raw_date)
            if parsed_dt is not None:
                sent_at = parsed_dt if parsed_dt.tzinfo is not None else parsed_dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, IndexError):
            sent_at = None
    if sent_at is None:
        sent_at = fallback_now or datetime.now(timezone.utc)

    message_id = _decode_mime_header(msg.get("Message-ID"))
    in_reply_to = _decode_mime_header(msg.get("In-Reply-To"))
    raw_refs = msg.get_all("References", [])
    references = extract_reference_ids(" ".join(raw_refs)) if raw_refs else ()
    is_seen = b"\\Seen" in flags_bytes or b"\\seen" in flags_bytes.lower()
    header_hash = f"sha256:{hashlib.sha256(header_bytes).hexdigest()}"

    return ImapEnvelope(
        uid=uid,
        folder=folder,
        subject=subject,
        sender=sender_str,
        recipient=to_str,
        participants=participants,
        sent_at=sent_at,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        is_seen=is_seen,
        header_hash=header_hash,
    )


def validate_folder_list(folders: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not folders:
        return DEFAULT_IMAP_FOLDERS
    cleaned: list[str] = []
    for folder in folders:
        if not isinstance(folder, str):
            raise ValueError("Invalid IMAP folder name")
        name = folder.strip()
        if not name or not _FOLDER_NAME_RE.fullmatch(name):
            raise ValueError("Invalid IMAP folder name")
        if name not in cleaned:
            cleaned.append(name)
    if not cleaned or len(cleaned) > MAX_FOLDERS_PER_CONNECTION:
        raise ValueError("Invalid IMAP folder selection count")
    return tuple(cleaned)


def get_imap_folders(connection: WorkspaceConnection) -> tuple[str, ...]:
    config = connection.config if isinstance(connection.config, dict) else {}
    raw = config.get("imap_folders")
    if isinstance(raw, (list, tuple)) and raw:
        try:
            return validate_folder_list(raw)
        except ValueError:
            return DEFAULT_IMAP_FOLDERS
    return DEFAULT_IMAP_FOLDERS


async def configure_imap_folders(
    session,
    *,
    connection_id: int,
    folders: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    """Persist selected IMAP folders in non-secret `WorkspaceConnection.config`."""
    tenant_id = current_tenant_id()
    if type(tenant_id) is not int or tenant_id <= 0:
        raise TenantContextError("IMAP folder configuration requires a selected workspace")
    validated = validate_folder_list(folders)
    conn = (
        await session.execute(
            select(WorkspaceConnection).where(
                WorkspaceConnection.tenant_id == tenant_id,
                WorkspaceConnection.id == connection_id,
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise ImapSyncError("connection_unavailable")
    updated_config = dict(conn.config) if isinstance(conn.config, dict) else {}
    updated_config["imap_folders"] = list(validated)
    conn.config = updated_config
    await session.flush()
    return validated


def _extract_uidvalidity(client: Any, select_data: list[Any] | None) -> int:
    resp = getattr(client, "untagged_responses", {}) or {}
    uv_items = resp.get("UIDVALIDITY") or resp.get(b"UIDVALIDITY")
    if uv_items:
        last_item = uv_items[-1]
        text_val = last_item.decode("ascii", errors="ignore") if isinstance(last_item, bytes) else str(last_item)
        if text_val.strip().isdigit():
            return int(text_val.strip())
    for item in select_data or []:
        text_val = item.decode("ascii", errors="ignore") if isinstance(item, bytes) else str(item)
        match = _UIDVALIDITY_RE.search(text_val)
        if match:
            return int(match.group(1))
    return 1


def _fetch_folder_batches_sync(
    client: Any,
    *,
    folder: str,
    stored_uidvalidity: int | None,
    last_uid: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    reset_pending: bool = False,
) -> tuple[int, bool, list[tuple[list[ImapEnvelope], int]], set[int], bool]:
    """Read-only folder UID scan using `BODY.PEEK` only.

    Returns `(current_uidvalidity, uidvalidity_reset, successful_batches, present_uids, all_batches_ok)`
    where each entry in `successful_batches` is `(batch_envelopes, max_candidate_uid)`.
    """
    if "BODY.PEEK[" not in FETCH_SPEC:
        raise ImapSyncError("non_peek_fetch_forbidden")

    status, select_data = client.select(folder, readonly=True)
    if status != "OK":
        raise ImapSyncError("folder_unavailable")

    current_uidvalidity = _extract_uidvalidity(client, select_data)
    uidvalidity_reset = bool(
        reset_pending or (stored_uidvalidity is not None and stored_uidvalidity != current_uidvalidity)
    )
    effective_last_uid = 0 if uidvalidity_reset else max(0, int(last_uid))

    status, search_data = client.uid("SEARCH", None, "UID 1:*")
    if status != "OK":
        return current_uidvalidity, uidvalidity_reset, [], set(), True
    # An OK answer with no data is not evidence that the folder is empty (B-14): the scan is
    # incomplete, so the expunge check must not treat the stored backlog as deleted.
    if not search_data or not search_data[0] or not isinstance(search_data[0], (bytes, str)):
        return current_uidvalidity, uidvalidity_reset, [], set(), False

    raw_uids = search_data[0].split()
    present_uids: set[int] = {
        int(u.decode("ascii") if isinstance(u, bytes) else str(u))
        for u in raw_uids
        if (u.decode("ascii") if isinstance(u, bytes) else str(u)).isdigit()
    }
    candidate_uids = sorted(u for u in present_uids if u > effective_last_uid)
    if not candidate_uids:
        return current_uidvalidity, uidvalidity_reset, [], present_uids, True

    successful_batches: list[tuple[list[ImapEnvelope], int]] = []
    all_batches_ok = True

    for idx in range(0, len(candidate_uids), batch_size):
        batch = candidate_uids[idx : idx + batch_size]
        uid_set = ",".join(str(u) for u in batch)
        fetch_status, fetch_data = client.uid("FETCH", uid_set, FETCH_SPEC)
        if fetch_status != "OK" or not fetch_data:
            if idx == 0:
                raise ImapSyncError("fetch_failed")
            all_batches_ok = False
            break

        batch_envelopes: list[ImapEnvelope] = []
        batch_ok = True
        for item in fetch_data:
            if item is None:
                continue
            if isinstance(item, (bytes, str)):
                stripped = item.strip()
                if stripped in (b")", b"", ")", ""):
                    continue
                batch_ok = False
                break
            if not isinstance(item, tuple) or len(item) < 2:
                batch_ok = False
                break
            meta_bytes = item[0] if isinstance(item[0], bytes) else str(item[0]).encode("utf-8")
            header_bytes = item[1] if isinstance(item[1], bytes) else str(item[1]).encode("utf-8")
            uid_match = _UID_RE.search(meta_bytes)
            if not uid_match:
                batch_ok = False
                break
            msg_uid = int(uid_match.group(1))
            if msg_uid <= effective_last_uid:
                continue
            flags_match = _FLAGS_RE.search(meta_bytes)
            flags_bytes = flags_match.group(1) if flags_match else b""
            batch_envelopes.append(
                parse_raw_header_block(
                    uid=msg_uid,
                    folder=folder,
                    header_bytes=header_bytes,
                    flags_bytes=flags_bytes,
                )
            )

        if not batch_ok:
            all_batches_ok = False
            break

        batch_envelopes.sort(key=lambda e: e.uid)
        successful_batches.append((batch_envelopes, max(batch)))

    return current_uidvalidity, uidvalidity_reset, successful_batches, present_uids, all_batches_ok


def _fetch_folder_envelopes_sync(
    client: Any,
    *,
    folder: str,
    stored_uidvalidity: int | None,
    last_uid: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, bool, list[ImapEnvelope]]:
    """Read-only folder UID scan using `BODY.PEEK` only. Never alters server flags."""
    current_uv, uv_reset, batches, _, _ = _fetch_folder_batches_sync(
        client,
        folder=folder,
        stored_uidvalidity=stored_uidvalidity,
        last_uid=last_uid,
        batch_size=batch_size,
    )
    envelopes = [env for batch_envs, _ in batches for env in batch_envs]
    envelopes.sort(key=lambda e: e.uid)
    return current_uv, uv_reset, envelopes


def _infer_direction_and_contact_email(
    *,
    folder: str,
    account_email: str,
    envelope: ImapEnvelope,
) -> tuple[str, str | None, str | None]:
    norm_account = account_email.strip().lower()
    folder_lower = folder.strip().lower()
    is_sent_folder = folder_lower in ("sent", "отправленные", "sent items", "sent messages")

    sender_addrs = [addr.lower() for _, addr in getaddresses([envelope.sender or ""]) if addr]
    if is_sent_folder or (norm_account and norm_account in sender_addrs):
        direction = "outbound"
        parsed_to = getaddresses([envelope.recipient or ""])
        if parsed_to and parsed_to[0][1]:
            disp, addr = parsed_to[0]
            return direction, addr.strip(), (disp.strip() or None)
        return direction, None, None

    direction = "inbound"
    parsed_from = getaddresses([envelope.sender or ""])
    if parsed_from and parsed_from[0][1]:
        disp, addr = parsed_from[0]
        return direction, addr.strip(), (disp.strip() or None)
    return direction, None, None


class ImapSyncService:
    """Incremental read-only IMAP folder synchronizer feeding P15-T02 `ingest()`."""

    def __init__(
        self,
        *,
        client_factory: Callable[[str, str, str], Any] = _open_pinned_imap_client,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        self._client_factory = client_factory
        self._batch_size = batch_size

    async def sync_connection(
        self,
        session,
        *,
        connection_id: int,
        folders: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise TenantContextError("IMAP sync requires an explicit workspace scope")

        conn = (
            await session.execute(
                select(WorkspaceConnection).where(
                    WorkspaceConnection.tenant_id == tenant_id,
                    WorkspaceConnection.id == connection_id,
                )
            )
        ).scalar_one_or_none()
        if conn is None or not conn.is_active or conn.state == "disabled":
            raise ImapSyncError("connection_unavailable")
        if (
            conn.provider_id not in WORKSPACE_PROVIDER_IDS
            or conn.schema_version != CATALOG_VERSION
            or conn.auth_method != "app_password"
        ):
            raise ImapSyncError("unsupported_provider")

        secret_row = (
            await session.execute(
                select(WorkspaceConnectionSecret).where(
                    WorkspaceConnectionSecret.connection_id == conn.id,
                )
            )
        ).scalar_one_or_none()
        try:
            password = decrypt_secret(secret_row.secret_encrypted) if secret_row else ""
        except (ValueError, RuntimeError):
            password = ""
        if not password:
            conn.state = "reauth"
            conn.check_code = "secret_unavailable"
            await session.flush()
            raise ImapSyncError("secret_unavailable")

        config = dict(conn.config) if isinstance(conn.config, dict) else {}
        account_login = str(config.get("account_login") or conn.account_key)
        selected_folders = validate_folder_list(folders) if folders is not None else get_imap_folders(conn)
        sync_state: dict[str, Any] = dict(config.get("imap_sync_state") or {})

        def _poll_all_folders() -> dict[
            str, tuple[int, bool, list[tuple[list[ImapEnvelope], int]], set[int], bool]
        ]:
            client = self._client_factory(conn.provider_id, account_login, password)
            try:
                out: dict[str, tuple[int, bool, list[tuple[list[ImapEnvelope], int]], set[int], bool]] = {}
                for folder in selected_folders:
                    folder_state = sync_state.get(folder) if isinstance(sync_state.get(folder), dict) else {}
                    stored_uv = folder_state.get("uidvalidity")
                    stored_last_uid = int(folder_state.get("last_uid") or 0)
                    reset_pending = bool(folder_state.get("uidvalidity_reset_pending", False))
                    out[folder] = _fetch_folder_batches_sync(
                        client,
                        folder=folder,
                        stored_uidvalidity=int(stored_uv) if isinstance(stored_uv, int) else None,
                        last_uid=stored_last_uid,
                        batch_size=self._batch_size,
                        reset_pending=reset_pending,
                    )
                return out
            finally:
                _close_imap_client(client)

        folder_batches = await asyncio.to_thread(_poll_all_folders)

        folder_summaries: list[dict[str, Any]] = []
        total_created = 0
        total_rebound = 0
        total_unavailable = 0

        for folder in selected_folders:
            current_uv, uv_reset, successful_batches, present_uids, all_batches_ok = folder_batches[folder]
            folder_state = dict(sync_state.get(folder) or {}) if isinstance(sync_state.get(folder), dict) else {}
            highest_uid = 0 if uv_reset else int(folder_state.get("last_uid") or 0)
            created_count = 0
            rebound_count = 0
            fetched_count = 0
            unavailable_count = 0

            for batch_envelopes, batch_max_uid in successful_batches:
                for env in batch_envelopes:
                    fetched_count += 1
                    direction, contact_email, contact_name = _infer_direction_and_contact_email(
                        folder=folder,
                        account_email=account_login,
                        envelope=env,
                    )
                    result = await ingest(
                        session,
                        IngestMessageInput(
                            connection_id=conn.id,
                            channel="email",
                            original_id=f"{folder}:{current_uv}:{env.uid}",
                            direction=direction,
                            sent_at=env.sent_at,
                            received_at=datetime.now(timezone.utc),
                            subject=env.subject,
                            sender=envelope_addr_or_default(
                                env.sender, account_login if direction == "outbound" else None
                            ),
                            recipient=envelope_addr_or_default(
                                env.recipient, account_login if direction == "inbound" else None
                            ),
                            participants=env.participants,
                            content_hash=env.header_hash,
                            external_message_id=env.message_id,
                            in_reply_to=env.in_reply_to,
                            references=env.references,
                            contact_identity_kind="email" if contact_email else None,
                            contact_identity_value=contact_email,
                            contact_display_name=contact_name,
                            is_read=env.is_seen or direction == "outbound",
                            dedupe_by_external_message_id_hash=uv_reset,
                        ),
                    )
                    if result.created:
                        created_count += 1
                        total_created += 1
                    if result.uid_rebound:
                        rebound_count += 1
                        total_rebound += 1

                if batch_max_uid > highest_uid:
                    highest_uid = batch_max_uid

                # Persist per-batch cursor progress (N-1)
                sync_state[folder] = {
                    "uidvalidity": current_uv,
                    "last_uid": highest_uid,
                    "last_synced_at": datetime.now(timezone.utc).isoformat(),
                    **(
                        {"uidvalidity_reset_pending": True}
                        if (uv_reset and not all_batches_ok)
                        else {}
                    ),
                }
                config["imap_sync_state"] = dict(sync_state)
                conn.config = dict(config)
                await session.flush()

            rescan_needed = uv_reset and not all_batches_ok
            sync_state[folder] = {
                "uidvalidity": current_uv,
                # A partial scan cannot move the cursor past messages it never read (N-1).
                "last_uid": 0 if rescan_needed else highest_uid,
                "last_synced_at": datetime.now(timezone.utc).isoformat(),
                **({"uidvalidity_reset_pending": True} if rescan_needed else {}),
            }

            # Detect messages expunged from this folder on the IMAP server (B-6)
            stored_messages = (
                await session.execute(
                    select(UnifiedMessage).where(
                        UnifiedMessage.tenant_id == tenant_id,
                        UnifiedMessage.connection_id == conn.id,
                        UnifiedMessage.channel == "email",
                    )
                )
            ).scalars().all()
            folder_prefix = f"{folder}:"
            for stored_msg in stored_messages:
                if stored_msg.unavailable_at is not None:
                    continue
                orig = str(stored_msg.original_id or "")
                if not orig.startswith(folder_prefix):
                    continue
                parts = orig.split(":", 2)
                if len(parts) != 3 or not parts[2].isdigit():
                    continue
                msg_uv_str, msg_uid = parts[1], int(parts[2])
                # Only one exact, complete scan can prove a message is gone (B-14).
                is_expunged_in_current_uv = (
                    all_batches_ok
                    and present_uids
                    and msg_uv_str == str(current_uv)
                    and msg_uid <= highest_uid
                    and msg_uid not in present_uids
                )
                is_missing_after_reset = (
                    uv_reset and all_batches_ok and msg_uv_str != str(current_uv)
                )
                if is_expunged_in_current_uv or is_missing_after_reset:
                    marked = await mark_message_unavailable(
                        session,
                        connection_id=conn.id,
                        channel="email",
                        original_id=orig,
                    )
                    if marked:
                        unavailable_count += 1
                        total_unavailable += 1

            folder_summaries.append(
                {
                    "folder": folder,
                    "uidvalidity": current_uv,
                    "uidvalidity_reset": uv_reset,
                    "last_uid": highest_uid,
                    "fetched": fetched_count,
                    "created": created_count,
                    "uid_rebound": rebound_count,
                    "marked_unavailable": unavailable_count,
                    "batch_incomplete": not all_batches_ok,
                }
            )

        config["imap_folders"] = list(selected_folders)
        config["imap_sync_state"] = sync_state
        conn.config = config
        conn.state = "verified"
        conn.check_code = "imap_authenticated"
        conn.checked_at = datetime.now(timezone.utc)
        await session.flush()

        return {
            "connection_id": conn.id,
            "provider_id": conn.provider_id,
            "folders": folder_summaries,
            "total_created": total_created,
            "total_uid_rebound": total_rebound,
            "total_marked_unavailable": total_unavailable,
        }


def envelope_addr_or_default(value: str | None, fallback: str | None) -> str | None:
    if value and value.strip():
        return value.strip()
    return fallback


imap_sync_service = ImapSyncService()
