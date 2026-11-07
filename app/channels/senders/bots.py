"""One plain private Bot API message; no MTProto, retries, splitting or broadcasts."""

from __future__ import annotations

import asyncio
import http.client
import json
import re
import secrets
import ssl
from urllib.parse import urlencode

from app.channels.delivery_parts import REJECTED_HTTP_STATUSES, receipt_id, valid_part
from app.channels.senders.activation import admission_code
from app.channels.senders.contracts import MAX, TELEGRAM, BotAccount, BotRecipient, SendResult, bot_fingerprint
from app.channels.senders.network import MAX_RESPONSE_BYTES, PinnedHTTPSConnection
from app.core.outbound import OutboundRefusal, StaticPolicy, acquire_outbound_target

_HOSTS = {TELEGRAM: "api.telegram.org", MAX: "platform-api2.max.ru"}


def _input_code(capability: str, account: BotAccount, recipient: BotRecipient, text: str) -> str | None:
    if capability not in _HOSTS:
        return "unsupported_capability"
    code = admission_code(capability, account.binding)
    if code:
        return code
    if (
        type(recipient.workspace_id) is not int
        or type(recipient.binding_id) is not int
        or (recipient.workspace_id, recipient.binding_id) != (account.binding.workspace_id, account.binding.binding_id)
    ):
        return "recipient_binding_mismatch"
    if recipient.kind != "private" or recipient.started_bot is not True:
        return "bot_not_started"
    if (
        not isinstance(recipient.user_id, str)
        or re.fullmatch(r"[1-9][0-9]{0,15}", recipient.user_id) is None
        or int(recipient.user_id) > 2**52 - 1
    ):
        return "invalid_recipient"
    if not valid_part(recipient.user_id, text, 4096 if capability == TELEGRAM else 4000) or "\x00" in text:
        return "invalid_message"
    if not isinstance(account.token, str) or not account.token or len(account.token) > 4096:
        return "credential_unavailable"
    # Telegram embeds its credential in a path. Reject URL/header delimiters;
    # MAX sends a header, never accept CR/LF or non-ASCII there either.
    pattern = r"[0-9]+:[A-Za-z0-9_-]+" if capability == TELEGRAM else r"[A-Za-z0-9_.:-]+"
    if re.fullmatch(pattern, account.token) is None:
        return "invalid_credential"
    if (
        not isinstance(recipient.bot_fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", recipient.bot_fingerprint) is None
        or not secrets.compare_digest(recipient.bot_fingerprint, bot_fingerprint(capability, account.token))
    ):
        return "bot_identity_changed"
    return None


def _parse_result(capability: str, status: int, body: bytes, recipient_id: str) -> SendResult:
    if len(body) > MAX_RESPONSE_BYTES:
        return SendResult("unknown", "invalid_response")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return SendResult("unknown", "invalid_response")
    if not isinstance(payload, dict):
        return SendResult("unknown", "invalid_response")
    if capability == TELEGRAM:
        if status in REJECTED_HTTP_STATUSES and payload.get("ok") is False and payload.get("error_code") == status:
            return SendResult("rejected", f"http_{status}")
        result = payload.get("result")
        if status == 200 and payload.get("ok") is True and isinstance(result, dict):
            chat = result.get("chat")
            mid = result.get("message_id")
            if (
                isinstance(chat, dict)
                and chat.get("type") == "private"
                and type(chat.get("id")) is int
                and str(chat["id"]) == recipient_id
                and type(mid) is int
                and mid > 0
            ):
                return SendResult("sent", "bot_accepted", receipt_id=str(mid))
    else:
        # MAX documents {code, message} errors and Message.body.mid receipts.
        if status in REJECTED_HTTP_STATUSES and isinstance(payload.get("code"), str) and payload["code"]:
            return SendResult("rejected", f"http_{status}")
        message = payload.get("message")
        if status == 200 and isinstance(message, dict):
            content = message.get("body")
            mid = receipt_id(content.get("mid")) if isinstance(content, dict) else None
            recipient = message.get("recipient")
            if (
                mid is not None
                and len(mid) <= 200
                and re.fullmatch(r"[A-Za-z0-9_.:-]+", mid)
                and isinstance(recipient, dict)
                and recipient.get("chat_type") == "dialog"
                and type(recipient.get("user_id")) is int
                and str(recipient["user_id"]) == recipient_id
            ):
                return SendResult("sent", "bot_accepted", receipt_id=mid)
    return SendResult("unknown", "unconfirmed_response")


def _send_bot(capability: str, account: BotAccount, recipient: BotRecipient, text: str) -> SendResult:
    code = _input_code(capability, account, recipient, text)
    if code:
        return SendResult("blocked", code)
    host = _HOSTS[capability]
    connection = None
    request_started = False
    try:
        target = acquire_outbound_target(host, 443, policy=StaticPolicy((host,)))
        code = _input_code(capability, account, recipient, text)
        if code:
            return SendResult("blocked", code)
        connection = PinnedHTTPSConnection(target)
        connection.connect()
        code = _input_code(capability, account, recipient, text)
        if code:
            return SendResult("blocked", code)
        if capability == TELEGRAM:
            path = f"/bot{account.token}/sendMessage"
            payload = {"chat_id": recipient.user_id, "text": text, "disable_web_page_preview": True}
            headers = {"Content-Type": "application/json"}
        else:
            path = "/messages?" + urlencode({"user_id": recipient.user_id})
            payload = {"text": text, "notify": True}
            headers = {"Content-Type": "application/json", "Authorization": account.token}
        request_started = True
        connection.request("POST", path, json.dumps(payload).encode("utf-8"), headers)
        response = connection.getresponse()
        return _parse_result(capability, response.status, response.read(MAX_RESPONSE_BYTES + 1), recipient.user_id)
    except OutboundRefusal as exc:
        return SendResult("blocked", exc.code)
    except TimeoutError:
        return SendResult("unknown", "bot_timeout")
    except (ssl.SSLError, OSError, http.client.HTTPException):
        return SendResult("unknown" if request_started else "rejected", "bot_transport_error")
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


async def send_telegram(account: BotAccount, recipient: BotRecipient, text: str) -> SendResult:
    """Trusted caller provides persisted same-bot /start evidence and a durable claim."""
    return await asyncio.to_thread(_send_bot, TELEGRAM, account, recipient, text)


async def send_max(account: BotAccount, recipient: BotRecipient, text: str) -> SendResult:
    """Trusted caller provides persisted same-bot private-chat consent and a durable claim."""
    return await asyncio.to_thread(_send_bot, MAX, account, recipient, text)
