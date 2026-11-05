"""P15-T06: explicit socket/provider doubles, no models/config/DB, no external I/O."""

from __future__ import annotations

import http.client
import json
import smtplib
import socket
import ssl
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import pytest

from app.channels.senders import bots, network
from app.channels.senders.activation import admission_code
from app.channels.senders.contracts import (
    EMAIL,
    MAX,
    TELEGRAM,
    BotAccount,
    BotRecipient,
    EmailRequest,
    MailAccount,
    SendBinding,
    bot_fingerprint,
)
from app.core import outbound
from app.core.tenant_context import tenant_scope
from app.integrations.email import smtp_send

__source_only__ = True

PREFIXES = {EMAIL: "P15_EMAIL_SEND", TELEGRAM: "P15_TELEGRAM_BOT_SEND", MAX: "P15_MAX_BOT_SEND"}
BINDING = SendBinding(7, 19, 3, 3, active=True, verified=True)
MAIL = MailAccount(BINDING, "mailru_mail", "customer@example.org", "private-password", send_enabled=True)
REQUEST = EmailRequest("recipient@example.org", "Subject", "Plain text\n")
RECIPIENT = BotRecipient(7, 19, "123", started_bot=True)


def recipient(capability):
    return replace(RECIPIENT, bot_fingerprint=bot_fingerprint(capability, account(capability).token))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for prefix in PREFIXES.values():
        for suffix in ("ENABLED", "BINDINGS", "KILL"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("real network forbidden")

    class NoNetworkSocket(socket.socket):
        connect = forbidden
        connect_ex = forbidden

    # asyncio uses local socketpair() for its wakeup pipe; block connections,
    # not construction of those local descriptors.
    monkeypatch.setattr(socket, "socket", NoNetworkSocket)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    with tenant_scope(7):
        yield


def release(monkeypatch, capability):
    prefix = PREFIXES[capability]
    monkeypatch.setenv(f"{prefix}_ENABLED", "1")
    monkeypatch.setenv(f"{prefix}_BINDINGS", "7:19")


def seam_double(monkeypatch, module):
    calls = []

    def acquire(host, port, **kwargs):
        calls.append((host, port, kwargs))
        assert kwargs["policy"].allowed_hosts == (host,)
        return outbound.OutboundTarget(host, port, ("8.8.8.8",))

    monkeypatch.setattr(module, "acquire_outbound_target", acquire)
    return calls


class SMTPDouble:
    def __init__(self, failure=None, stage="send"):
        self.failure, self.stage = failure, stage
        self.calls = []
        self.closed = False
        self.refused = {}
        self.greeting = (250, b"ok")
        self.on_login = lambda: None

    def ehlo(self):
        self.calls.append("ehlo")
        return self.greeting

    def login(self, login, password):
        self.calls.append(("login", login, password))
        self.on_login()
        if self.stage == "login" and self.failure:
            raise self.failure

    def sendmail(self, sender, recipients, data):
        self.calls.append(("send", sender, recipients, data))
        if self.stage == "send" and self.failure:
            raise self.failure
        return self.refused

    def close(self):
        self.closed = True


def smtp_double(monkeypatch, client=None):
    release(monkeypatch, EMAIL)
    calls = seam_double(monkeypatch, smtp_send)
    client = client or SMTPDouble()
    monkeypatch.setattr(smtp_send, "_PinnedSMTP", lambda target, binding: client)
    return client, calls


class HTTPDouble:
    def __init__(self, capability, *, status=200, payload=None, failure=None, stage="request"):
        self.status = status
        self.failure, self.stage = failure, stage
        self.calls, self.closed = [], False
        self.payload = payload if payload is not None else acknowledgement(capability)
        self.on_connect = lambda: None

    def connect(self):
        self.on_connect()
        if self.stage == "connect" and self.failure:
            raise self.failure

    def request(self, *args):
        self.calls.append(args)
        if self.stage == "request" and self.failure:
            raise self.failure

    def getresponse(self):
        if self.stage == "read" and self.failure:
            raise self.failure
        body = self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode()
        return SimpleNamespace(status=self.status, read=lambda maximum: body[:maximum])

    def close(self):
        self.closed = True


def acknowledgement(capability):
    if capability == TELEGRAM:
        return {"ok": True, "result": {"message_id": 9, "chat": {"id": 123, "type": "private"}}}
    return {"message": {"body": {"mid": "mid.9"}, "recipient": {"user_id": 123, "chat_type": "dialog"}}}


def bot_double(monkeypatch, capability, **kwargs):
    release(monkeypatch, capability)
    calls = seam_double(monkeypatch, bots)
    client = HTTPDouble(capability, **kwargs)
    monkeypatch.setattr(bots, "PinnedHTTPSConnection", lambda target: client)
    return client, calls


def account(capability):
    return BotAccount(BINDING, "123:private-token" if capability == TELEGRAM else "private-token")


@pytest.mark.parametrize("capability", PREFIXES)
def test_default_off_even_with_verified_owned_binding(capability):
    assert admission_code(capability, BINDING) == "capability_disabled"


@pytest.mark.parametrize("capability", PREFIXES)
@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 1"])
def test_only_explicit_enable(monkeypatch, capability, value):
    release(monkeypatch, capability)
    monkeypatch.setenv(f"{PREFIXES[capability]}_ENABLED", value)
    assert admission_code(capability, BINDING) == "capability_disabled"


@pytest.mark.parametrize("capability", PREFIXES)
@pytest.mark.parametrize("entries", ["", "*", "7:*", "07:19", "7:19,", " 7:19", "7:19,2:0", "7:19,wrong"])
def test_malformed_binding_list_fails_closed(monkeypatch, capability, entries):
    release(monkeypatch, capability)
    monkeypatch.setenv(f"{PREFIXES[capability]}_BINDINGS", entries)
    assert admission_code(capability, BINDING) == "invalid_release_bindings"


@pytest.mark.parametrize("capability", PREFIXES)
def test_exact_pairs_independent_switches(monkeypatch, capability):
    release(monkeypatch, capability)
    assert admission_code(capability, BINDING) is None
    monkeypatch.setenv(f"{PREFIXES[capability]}_BINDINGS", "7:20,8:19")
    assert admission_code(capability, BINDING) == "binding_not_released"
    for other in set(PREFIXES) - {capability}:
        assert admission_code(other, BINDING) == "capability_disabled"


@pytest.mark.parametrize("kill", ["1", "true", "", "garbage"])
@pytest.mark.parametrize("capability", PREFIXES)
def test_kill_wins(monkeypatch, capability, kill):
    release(monkeypatch, capability)
    monkeypatch.setenv(f"{PREFIXES[capability]}_KILL", kill)
    assert admission_code(capability, BINDING) == "capability_killed"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"binding_id": True}, "invalid_binding"),
        ({"workspace_id": 0}, "invalid_binding"),
        ({"owner_user_id": -1}, "invalid_binding"),
        ({"actor_user_id": 0}, "invalid_binding"),
        ({"actor_user_id": 4}, "account_owner_mismatch"),
        ({"workspace_id": 8}, "workspace_mismatch"),
        ({"active": False}, "account_unavailable"),
        ({"verified": False}, "account_unavailable"),
    ],
)
def test_binding_guards(monkeypatch, change, code):
    release(monkeypatch, EMAIL)
    assert admission_code(EMAIL, replace(BINDING, **change)) == code


@pytest.mark.parametrize("workspace,bypass", [(None, False), (7, True), (None, True)])
def test_unscoped_and_operator_cannot_send(monkeypatch, workspace, bypass):
    release(monkeypatch, EMAIL)
    with tenant_scope(workspace, bypass=bypass):
        assert admission_code(EMAIL, BINDING) == "workspace_mismatch"


def test_unknown_capability():
    assert admission_code("arbitrary", BINDING) == "unsupported_capability"


def test_smtp_disabled_does_not_reach_seam():
    assert smtp_send._send_email(MAIL, REQUEST).code == "capability_disabled"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"send_enabled": False}, "account_send_disabled"),
        ({"provider_id": "installation_smtp"}, "unsupported_provider"),
        ({"app_password": ""}, "credential_unavailable"),
        ({"app_password": "pässword"}, "credential_unavailable"),
    ],
)
def test_smtp_account_guards(monkeypatch, change, code):
    release(monkeypatch, EMAIL)
    assert smtp_send._send_email(replace(MAIL, **change), REQUEST).code == code


@pytest.mark.parametrize("provider,host", [("mailru_mail", "smtp.mail.ru"), ("yandex_mail", "smtp.yandex.ru")])
def test_customer_envelope_tls_reply_and_receipt(monkeypatch, provider, host):
    client, calls = smtp_double(monkeypatch)
    result = smtp_send._send_email(
        replace(MAIL, provider_id=provider), replace(REQUEST, in_reply_to="<original@example.org>")
    )
    assert result.outcome == "sent" and result.code == "smtp_accepted" and result.receipt_id is None
    assert calls[0][:2] == (host, 465)
    sent = client.calls[-1]
    assert sent[:3] == ("send", MAIL.account_login, [REQUEST.recipient])
    msg = BytesParser(policy=policy.default).parsebytes(sent[3])
    assert str(msg["From"]) == MAIL.account_login
    assert str(msg["To"]) == REQUEST.recipient
    assert msg["Message-ID"] == result.message_id
    assert msg["In-Reply-To"] == "<original@example.org>"
    assert msg["Date"] and msg["Bcc"] is None and msg["Cc"] is None
    assert msg.get_content().replace("\r\n", "\n") == REQUEST.text
    assert len([x for x in client.calls if x[0] == "send"]) == 1
    assert client.closed


@pytest.mark.parametrize(
    "change",
    [
        {"recipient": "Other <a@example.org>"},
        {"recipient": "a@example.org\r\nBcc: b@example.org"},
        {"recipient": "a@example.org,b@example.org"},
        {"subject": "s\nInjected: x"},
        {"text": ""},
        {"text": "\x00"},
        {"text": "x" * (1024 * 1024 + 1)},
        {"text": "\ud800"},
        {"in_reply_to": "bad"},
        {"in_reply_to": "<id@x>\nBcc: a@b"},
    ],
)
def test_invalid_email_never_connects(monkeypatch, change):
    release(monkeypatch, EMAIL)
    assert smtp_send._send_email(MAIL, replace(REQUEST, **change)).outcome == "blocked"


@pytest.mark.parametrize("login", [" a@example.org", "installation", "a@example.org\n", "Имя@example.org"])
def test_invalid_from_never_connects(monkeypatch, login):
    release(monkeypatch, EMAIL)
    assert smtp_send._send_email(replace(MAIL, account_login=login), REQUEST).outcome == "blocked"


@pytest.mark.parametrize(
    "failure,stage,outcome,code",
    [
        (smtplib.SMTPAuthenticationError(535, b"private-password"), "login", "rejected", "smtp_auth_failed"),
        (ssl.SSLError("private-password"), "login", "rejected", "smtp_tls_failed"),
        (TimeoutError("private-password"), "login", "unknown", "smtp_timeout"),
        (TimeoutError("private-password"), "send", "unknown", "smtp_timeout"),
        (smtplib.SMTPServerDisconnected("private-password"), "send", "unknown", "smtp_transport_error"),
        (OSError("private-password"), "send", "unknown", "smtp_transport_error"),
        (OSError("private-password"), "login", "rejected", "smtp_transport_error"),
        (ssl.SSLError("private-password"), "send", "unknown", "smtp_tls_failed"),
        (smtplib.SMTPDataError(550, b"private-password"), "send", "rejected", "smtp_rejected"),
        (smtplib.SMTPSenderRefused(550, b"private-password", "x"), "send", "rejected", "smtp_rejected"),
        (
            smtplib.SMTPRecipientsRefused({"x": (550, b"private-password")}),
            "send",
            "rejected",
            "smtp_recipient_rejected",
        ),
    ],
)
def test_smtp_fixed_outcomes_no_retry_no_provider_text(monkeypatch, caplog, failure, stage, outcome, code):
    client, _ = smtp_double(monkeypatch, SMTPDouble(failure, stage))
    result = smtp_send._send_email(MAIL, REQUEST)
    assert (result.outcome, result.code) == (outcome, code)
    assert result.message_id
    assert len([x for x in client.calls if x[0] == "send"]) <= 1
    assert "private-password" not in repr(result) + caplog.text
    assert client.closed


def test_smtp_greeting_and_recipient_refusal(monkeypatch):
    client, _ = smtp_double(monkeypatch)
    client.greeting = (500, b"private-password")
    assert smtp_send._send_email(MAIL, REQUEST).code == "smtp_greeting_failed"
    assert client.calls == ["ehlo"]
    client.greeting = (250, b"ok")
    client.refused = {"x": (550, b"private-password")}
    assert smtp_send._send_email(MAIL, REQUEST).code == "smtp_recipient_rejected"


def test_smtp_switch_rechecked_after_auth(monkeypatch):
    client, _ = smtp_double(monkeypatch)
    client.on_login = lambda: monkeypatch.setenv("P15_EMAIL_SEND_KILL", "1")
    assert smtp_send._send_email(MAIL, REQUEST).code == "capability_killed"
    assert not any(x[0] == "send" for x in client.calls)


@pytest.mark.parametrize("kill_during", [None, "mail", "rcpt"])
def test_smtp_rechecks_kill_after_envelope_before_data(monkeypatch, kill_during):
    release(monkeypatch, EMAIL)
    seam_double(monkeypatch, smtp_send)

    class SMTPDataGate(smtp_send._PinnedSMTP):
        def __init__(self, target, binding):
            self._target = target
            self._binding = binding
            self.does_esmtp = True
            self.esmtp_features = {}
            self.helo_resp = 250
            self.calls = []
            self.data_replies = 0
            self.closed = False

        def ehlo(self):
            self.calls.append(("ehlo",))
            return 250, b"ok"

        def login(self, login, password):
            self.calls.append(("login", login, password))

        def mail(self, sender, options=()):
            self.calls.append(("mail", sender, options))
            if kill_during == "mail":
                monkeypatch.setenv("P15_EMAIL_SEND_KILL", "1")
            return 250, b"ok"

        def rcpt(self, recipient, options=()):
            self.calls.append(("rcpt", recipient, options))
            if kill_during == "rcpt":
                monkeypatch.setenv("P15_EMAIL_SEND_KILL", "1")
            return 250, b"ok"

        def putcmd(self, command, args=""):
            self.calls.append(("command", command, args))

        def getreply(self):
            self.data_replies += 1
            if self.data_replies == 1:
                return 354, b"continue"
            return 250, b"accepted"

        def send(self, message):
            self.calls.append(("wire", message))

        def close(self):
            self.closed = True

    client = SMTPDataGate(None, BINDING)
    monkeypatch.setattr(smtp_send, "_PinnedSMTP", lambda target, binding: client)

    result = smtp_send._send_email(MAIL, REQUEST)

    killed = kill_during is not None
    assert (result.outcome, result.code) == (("blocked", "capability_killed") if killed else ("sent", "smtp_accepted"))
    assert [call[0] for call in client.calls if call[0] in {"mail", "rcpt"}] == ["mail", "rcpt"]
    commands = [call[1] for call in client.calls if call[0] == "command"]
    wire = [call[1] for call in client.calls if call[0] == "wire"]
    if killed:
        assert commands == [] and wire == []
    else:
        assert commands == ["data"] and len(wire) == 1 and wire[0].endswith(b".\r\n")
    assert client.closed


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
def test_bots_disabled_even_with_credentials(capability):
    assert bots._send_bot(capability, account(capability), recipient(capability), "hello").code == "capability_disabled"


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize(
    "change,code",
    [
        ({"started_bot": False}, "bot_not_started"),
        ({"kind": "channel"}, "bot_not_started"),
        ({"binding_id": 20}, "recipient_binding_mismatch"),
        ({"workspace_id": 8}, "recipient_binding_mismatch"),
        ({"user_id": "@username"}, "invalid_recipient"),
        ({"user_id": "-123"}, "invalid_recipient"),
        ({"user_id": "01"}, "invalid_recipient"),
        ({"user_id": "123&chat_id=1"}, "invalid_recipient"),
        ({"user_id": "9999999999999999"}, "invalid_recipient"),
    ],
)
def test_private_started_same_bot_recipient_required(monkeypatch, capability, change, code):
    release(monkeypatch, capability)
    result = bots._send_bot(capability, account(capability), replace(recipient(capability), **change), "hello")
    assert result.code == code and result.outcome == "blocked"


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize("text", ["", " ", "a\x00b", "x" * 4097, "😀" * 2049, "\ud800"])
def test_bot_invalid_text_no_split_or_truncate(monkeypatch, capability, text):
    release(monkeypatch, capability)
    assert bots._send_bot(capability, account(capability), recipient(capability), text).code == "invalid_message"


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize("token", ["", "x\nAuthorization: y", "x/y", "x?y", "😀"])
def test_bot_invalid_credentials_no_network(monkeypatch, capability, token):
    release(monkeypatch, capability)
    assert bots._send_bot(capability, BotAccount(BINDING, token), RECIPIENT, "hi").outcome == "blocked"


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
def test_bot_one_pinned_plain_request(monkeypatch, capability):
    client, calls = bot_double(monkeypatch, capability)
    result = bots._send_bot(capability, account(capability), recipient(capability), "<b>exact</b>\n")
    assert result.outcome == "sent" and result.receipt_id
    assert len(calls) == len(client.calls) == 1 and calls[0][1] == 443
    method, path, body, headers = client.calls[0]
    payload = json.loads(body)
    assert method == "POST" and payload["text"] == "<b>exact</b>\n"
    assert "format" not in payload and "parse_mode" not in payload
    if capability == TELEGRAM:
        assert calls[0][0] == "api.telegram.org"
        assert path == "/bot123:private-token/sendMessage" and payload["chat_id"] == "123"
    else:
        assert calls[0][0] == "platform-api2.max.ru"
        assert path == "/messages?user_id=123" and headers["Authorization"] == "private-token"
    assert client.closed


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 429])
def test_explicit_provider_rejection_never_retries(monkeypatch, capability, status):
    payload = (
        {"ok": False, "error_code": status, "description": "private-token"}
        if capability == TELEGRAM
        else {"code": "private-token", "message": "secret"}
    )
    client, _ = bot_double(monkeypatch, capability, status=status, payload=payload)
    result = bots._send_bot(capability, account(capability), recipient(capability), "hello")
    assert (result.outcome, result.code) == ("rejected", f"http_{status}")
    assert len(client.calls) == 1 and "private-token" not in repr(result)


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize("status", [301, 302, 307, 408, 409, 500, 502, 503])
def test_ambiguous_http_unknown_no_redirect_retry(monkeypatch, capability, status):
    client, _ = bot_double(
        monkeypatch, capability, status=status, payload={"code": "secret", "ok": False, "error_code": status}
    )
    assert bots._send_bot(capability, account(capability), recipient(capability), "hello").outcome == "unknown"
    assert len(client.calls) == 1


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize("payload", [b"bad-json", b"\xff", [], {}, {"ok": True}, b"x" * 65537])
def test_malformed_response_unknown(monkeypatch, capability, payload):
    client, _ = bot_double(monkeypatch, capability, payload=payload)
    assert bots._send_bot(capability, account(capability), recipient(capability), "hello").outcome == "unknown"
    assert len(client.calls) == 1 and client.closed


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize(
    "failure,stage,outcome",
    [
        (TimeoutError("private-token"), "connect", "unknown"),
        (TimeoutError("private-token"), "read", "unknown"),
        (ssl.SSLError("private-token"), "connect", "rejected"),
        (OSError("private-token"), "request", "unknown"),
        (http.client.RemoteDisconnected("private-token"), "read", "unknown"),
    ],
)
def test_bot_transport_ambiguity_redacted(monkeypatch, caplog, capability, failure, stage, outcome):
    client, _ = bot_double(monkeypatch, capability, failure=failure, stage=stage)
    result = bots._send_bot(capability, account(capability), recipient(capability), "hi")
    assert result.outcome == outcome and client.closed
    assert len(client.calls) <= 1 and "private-token" not in caplog.text + repr(result)


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
def test_final_bot_kill_after_tls(monkeypatch, capability):
    client, _ = bot_double(monkeypatch, capability)
    client.on_connect = lambda: monkeypatch.setenv(f"{PREFIXES[capability]}_KILL", "1")
    assert bots._send_bot(capability, account(capability), recipient(capability), "hi").code == "capability_killed"
    assert client.calls == [] and client.closed


@pytest.mark.parametrize("capability", [EMAIL, TELEGRAM, MAX])
@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.1.2", "127.0.0.1", "169.254.1.2", "::1", "fd00::1"])
def test_every_sender_uses_real_seam_private_dns_block(monkeypatch, capability, address):
    release(monkeypatch, capability)
    calls = []

    def real_seam(host, port, **kwargs):
        calls.append(host)

        def resolve(*args):
            return [(0, 0, 0, "", (address, port))]

        return outbound.acquire_outbound_target(
            host, port, resolver=resolve, registry=outbound.empty_registry, **kwargs
        )

    module = smtp_send if capability == EMAIL else bots
    monkeypatch.setattr(module, "acquire_outbound_target", real_seam)
    result = (
        smtp_send._send_email(MAIL, REQUEST)
        if capability == EMAIL
        else bots._send_bot(capability, account(capability), recipient(capability), "hi")
    )
    assert result.outcome == "blocked" and result.code == outbound.PRIVATE_ADDRESS_FORBIDDEN
    assert len(calls) == 1


@pytest.mark.parametrize("capability", [EMAIL, TELEGRAM, MAX])
def test_revalidate_each_attempt_and_switch_after_resolution(monkeypatch, capability):
    release(monkeypatch, capability)
    module = smtp_send if capability == EMAIL else bots
    calls = []

    def acquire(host, port, **kwargs):
        calls.append(host)
        monkeypatch.setenv(f"{PREFIXES[capability]}_KILL", "1")
        return outbound.OutboundTarget(host, port, ("8.8.8.8",))

    monkeypatch.setattr(module, "acquire_outbound_target", acquire)

    def send():
        return (
            smtp_send._send_email(MAIL, REQUEST)
            if capability == EMAIL
            else bots._send_bot(capability, account(capability), recipient(capability), "hi")
        )

    assert send().code == "capability_killed"
    monkeypatch.setenv(f"{PREFIXES[capability]}_KILL", "0")
    assert send().code == "capability_killed" and len(calls) == 2


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
def test_wrong_recipient_ack_is_unknown(monkeypatch, capability):
    payload = acknowledgement(capability)
    if capability == TELEGRAM:
        payload["result"]["chat"]["id"] = 999
    else:
        payload["message"]["recipient"]["user_id"] = 999
    bot_double(monkeypatch, capability, payload=payload)
    assert bots._send_bot(capability, account(capability), recipient(capability), "hi").outcome == "unknown"


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize("mid", [True, 0, -1, "", " ", {}, [], None])
def test_invalid_receipt_unknown(monkeypatch, capability, mid):
    payload = acknowledgement(capability)
    if capability == TELEGRAM:
        payload["result"]["message_id"] = mid
    else:
        payload["message"]["body"]["mid"] = mid
    bot_double(monkeypatch, capability, payload=payload)
    assert bots._send_bot(capability, account(capability), recipient(capability), "hi").outcome == "unknown"


@pytest.mark.parametrize("address,family", [("8.8.8.8", socket.AF_INET), ("2001:4860:4860::8888", socket.AF_INET6)])
def test_tls_connects_only_validated_ip_with_original_sni(monkeypatch, address, family):
    events = []
    sock = SimpleNamespace(
        settimeout=lambda n: events.append(("timeout", n)),
        connect=lambda x: events.append(("connect", x)),
        close=lambda: events.append("close"),
    )
    monkeypatch.setattr(socket, "socket", lambda f, t: events.append(("socket", f, t)) or sock)
    context = SimpleNamespace(wrap_socket=lambda s, server_hostname: events.append(("sni", server_hostname)) or "tls")
    target = outbound.OutboundTarget("smtp.mail.ru", 465, (address,))
    assert network.open_tls_socket(target, 10, context) == "tls"
    assert events == [
        ("socket", family, socket.SOCK_STREAM),
        ("timeout", 10),
        ("connect", (address, 465)),
        ("sni", "smtp.mail.ru"),
    ]


@pytest.mark.parametrize("stage", ["connect", "tls"])
def test_socket_closed_on_tls_failure(monkeypatch, stage):
    closed = []

    def fail(*args, **kwargs):
        raise ssl.SSLError("secret")

    sock = SimpleNamespace(
        settimeout=lambda n: None,
        connect=fail if stage == "connect" else lambda x: None,
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(socket, "socket", lambda *args: sock)
    with pytest.raises(ssl.SSLError):
        network.open_tls_socket(
            outbound.OutboundTarget("api.telegram.org", 443, ("8.8.8.8",)), 10, SimpleNamespace(wrap_socket=fail)
        )
    assert closed == [True]


def test_pinned_protocols_do_not_resolve_hostname_again(monkeypatch):
    target = outbound.OutboundTarget("smtp.mail.ru", 465, ("8.8.8.8",))
    seen = []
    monkeypatch.setattr(network, "open_tls_socket", lambda *args: seen.append(args) or "https-socket")
    connection = network.PinnedHTTPSConnection(replace(target, host="api.telegram.org", port=443))
    connection.connect()
    assert connection.sock == "https-socket" and seen[-1][0].host == "api.telegram.org"
    assert connection._context.verify_mode == ssl.CERT_REQUIRED and connection._context.check_hostname
    monkeypatch.setattr(smtplib.SMTP, "__init__", lambda self, *args: setattr(self, "debuglevel", 0))
    monkeypatch.setattr(smtp_send, "open_tls_socket", lambda *args: seen.append(args) or "smtp-socket")
    client = smtp_send._PinnedSMTP(target, BINDING)
    assert client._get_socket("ignored", 25, 10) == "smtp-socket"
    assert seen[-1][0] == target and client.debuglevel == 0
    assert client.context.verify_mode == ssl.CERT_REQUIRED and client.context.check_hostname


async def test_async_public_adapters(monkeypatch):
    smtp_double(monkeypatch)
    assert (await smtp_send.send_email(MAIL, REQUEST)).outcome == "sent"
    for capability, send in ((TELEGRAM, bots.send_telegram), (MAX, bots.send_max)):
        bot_double(monkeypatch, capability)
        assert (await send(account(capability), recipient(capability), "hello")).outcome == "sent"


def test_sensitive_inputs_not_in_repr():
    assert "private-password" not in repr(MAIL)
    assert "private-token" not in repr(account(TELEGRAM))
    assert REQUEST.recipient not in repr(REQUEST) and REQUEST.text not in repr(REQUEST)


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
@pytest.mark.parametrize("fingerprint", ["", "0" * 64, "private-token", "f" * 63])
def test_consent_bound_to_this_bot_credential(monkeypatch, capability, fingerprint):
    release(monkeypatch, capability)
    result = bots._send_bot(
        capability, account(capability), replace(recipient(capability), bot_fingerprint=fingerprint), "hi"
    )
    assert result.code == "bot_identity_changed"


@pytest.mark.parametrize("capability", [TELEGRAM, MAX])
def test_token_rotation_cannot_reuse_start_evidence(monkeypatch, capability):
    release(monkeypatch, capability)
    rotated = replace(account(capability), token="456:other-token" if capability == TELEGRAM else "other-token")
    assert bots._send_bot(capability, rotated, recipient(capability), "hi").code == "bot_identity_changed"


def test_boolean_ambient_scope_not_a_workspace(monkeypatch):
    release(monkeypatch, EMAIL)
    with tenant_scope(True):
        assert admission_code(EMAIL, replace(BINDING, workspace_id=1)) == "workspace_mismatch"


def test_unicode_mail_uses_7bit_wire_without_8bitmime(monkeypatch):
    client, _ = smtp_double(monkeypatch)
    request = replace(REQUEST, subject="Тема письма", text="Привет, клиент! 👋\n")
    assert smtp_send._send_email(MAIL, request).outcome == "sent"
    data = client.calls[-1][3]
    assert data.isascii()
    message = BytesParser(policy=policy.default).parsebytes(data)
    assert message.get_content().replace("\r\n", "\n") == request.text
    assert str(message["Subject"]) == request.subject


def test_bot_adapter_rejects_non_bot_capability(monkeypatch):
    release(monkeypatch, EMAIL)
    assert bots._send_bot(EMAIL, account(MAX), recipient(MAX), "hi").code == "unsupported_capability"
