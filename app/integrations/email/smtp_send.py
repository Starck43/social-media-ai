"""One customer-account SMTP attempt. Implicit TLS 465, no retry or notification fallback."""

from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid

from email_validator import EmailNotValidError, validate_email

from app.channels.senders.activation import admission_code
from app.channels.senders.contracts import EMAIL, EmailRequest, MailAccount, SendResult
from app.channels.senders.network import TIMEOUT_SECONDS, open_tls_socket
from app.core.outbound import OutboundRefusal, OutboundTarget, StaticPolicy, acquire_outbound_target
from app.integrations.catalog import PROVIDERS

_MAIL_PROVIDERS = frozenset({"mailru_mail", "yandex_mail"})
_MESSAGE_ID = re.compile(r"<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+>")
MAX_BODY_BYTES = 1024 * 1024


class _PinnedSMTP(smtplib.SMTP_SSL):
    def __init__(self, target: OutboundTarget):
        self._target = target
        super().__init__(target.host, target.port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        self.set_debuglevel(0)

    def _get_socket(self, host, port, timeout):
        return open_tls_socket(self._target, timeout, self.context)


def _valid_mailbox(value: str) -> bool:
    if not isinstance(value, str) or not value.isascii() or value != value.strip():
        return False
    try:
        validated = validate_email(value, check_deliverability=False, allow_smtputf8=False)
    except EmailNotValidError:
        return False
    return validated.ascii_email is not None and not any(ch in value for ch in "\r\n\x00")


def _prepare(account: MailAccount, request: EmailRequest) -> tuple[bytes, str]:
    if not _valid_mailbox(account.account_login) or not _valid_mailbox(request.recipient):
        raise ValueError("invalid_mailbox")
    if (
        not isinstance(request.subject, str)
        or len(request.subject) > 998
        or any(ch in request.subject for ch in "\r\n\x00")
        or not isinstance(request.text, str)
        or not request.text.strip()
        or "\x00" in request.text
        or len(request.text.encode("utf-8")) > MAX_BODY_BYTES
    ):
        raise ValueError("invalid_message")
    if request.in_reply_to is not None and (
        not isinstance(request.in_reply_to, str) or _MESSAGE_ID.fullmatch(request.in_reply_to) is None
    ):
        raise ValueError("invalid_reply_reference")
    message_id = make_msgid(domain=account.account_login.rsplit("@", 1)[1])
    message = EmailMessage(policy=SMTP)
    # No caller-controlled From/Return-Path/CC/BCC: envelope and header are the
    # same customer's authenticated account, never installation-domain mail.
    message["From"] = account.account_login
    message["To"] = request.recipient
    message["Subject"] = request.subject
    message["Message-ID"] = message_id
    message["Date"] = formatdate(usegmt=True)
    if request.in_reply_to is not None:
        message["In-Reply-To"] = request.in_reply_to
    # Keep a 7-bit wire body: do not assume or silently require SMTPUTF8/8BITMIME.
    message.set_content(request.text, cte="quoted-printable")
    return message.as_bytes(), message_id


def _send_email(account: MailAccount, request: EmailRequest) -> SendResult:
    code = admission_code(EMAIL, account.binding)
    if code:
        return SendResult("blocked", code)
    if account.send_enabled is not True:
        return SendResult("blocked", "account_send_disabled")
    if account.provider_id not in _MAIL_PROVIDERS:
        return SendResult("blocked", "unsupported_provider")
    if not isinstance(account.app_password, str) or not account.app_password.strip():
        return SendResult("blocked", "credential_unavailable")
    try:
        data, message_id = _prepare(account, request)
    except (ValueError, UnicodeError):
        return SendResult("blocked", "invalid_message")
    host = dict(PROVIDERS[account.provider_id].defaults)["smtp_host"]
    client = None
    data_attempted = False
    try:
        target = acquire_outbound_target(host, 465, policy=StaticPolicy((host,)))
        code = admission_code(EMAIL, account.binding)
        if code:
            return SendResult("blocked", code, message_id=message_id)
        client = _PinnedSMTP(target)
        if client.ehlo()[0] != 250:
            return SendResult("rejected", "smtp_greeting_failed", message_id=message_id)
        client.login(account.account_login, account.app_password)
        # Auth can take time: a kill/removal must stop DATA, not only connection.
        code = admission_code(EMAIL, account.binding)
        if code:
            return SendResult("blocked", code, message_id=message_id)
        data_attempted = True
        refused = client.sendmail(account.account_login, [request.recipient], data)
        if refused:
            return SendResult("rejected", "smtp_recipient_rejected", message_id=message_id)
        return SendResult("sent", "smtp_accepted", message_id=message_id)
    except OutboundRefusal as exc:
        return SendResult("blocked", exc.code, message_id=message_id)
    except TimeoutError:
        return SendResult("unknown", "smtp_timeout", message_id=message_id)
    except ssl.SSLError:
        return SendResult("unknown" if data_attempted else "rejected", "smtp_tls_failed", message_id=message_id)
    except smtplib.SMTPAuthenticationError:
        return SendResult("rejected", "smtp_auth_failed", message_id=message_id)
    except smtplib.SMTPRecipientsRefused:
        return SendResult("rejected", "smtp_recipient_rejected", message_id=message_id)
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        return SendResult("rejected", "smtp_rejected", message_id=message_id)
    except (smtplib.SMTPException, OSError):
        return SendResult("unknown" if data_attempted else "rejected", "smtp_transport_error", message_id=message_id)
    finally:
        if client is not None:
            # Do not issue QUIT: a cleanup failure cannot erase a DATA receipt.
            try:
                client.close()
            except OSError:
                pass


async def send_email(account: MailAccount, request: EmailRequest) -> SendResult:
    """Internal adapter; caller owns fresh DB authority + durable one-shot claim.

    Cancellation does not stop an already running thread/socket. The caller must
    preserve the claim and reconcile UNKNOWN, never redispatch after cancellation.
    """
    return await asyncio.to_thread(_send_email, account, request)
