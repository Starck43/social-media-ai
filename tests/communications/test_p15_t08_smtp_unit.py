"""P15-T08 reply header transport, using synthetic messages and no network."""

from dataclasses import replace
from email import policy
from email.parser import BytesParser

import pytest

from app.channels.senders.contracts import EmailRequest, MailAccount, SendBinding
from app.integrations.email import smtp_send

__source_only__ = True

ACCOUNT = MailAccount(
    SendBinding(7, 19, 3, 3, active=True, verified=True), "mailru_mail", "sender@example.org", "password", True
)
REQUEST = EmailRequest("recipient@example.org", "subject", "text")
ORIGIN = "<origin@example.org>"
EARLIER = "<earlier@example.org>"


def test_reply_headers_render_and_are_private():
    request = replace(REQUEST, in_reply_to=ORIGIN, references=(EARLIER, ORIGIN))
    raw, _ = smtp_send._prepare(ACCOUNT, request)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert str(message["In-Reply-To"]) == ORIGIN
    assert str(message["References"]) == f"{EARLIER} {ORIGIN}"
    assert ORIGIN not in repr(request) and EARLIER not in repr(request)
    assert message["Bcc"] is None


def test_legacy_email_has_no_reply_headers():
    raw, _ = smtp_send._prepare(ACCOUNT, REQUEST)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert message["In-Reply-To"] is None and message["References"] is None


@pytest.mark.parametrize(
    "references,reply",
    [
        (("<ok@example.org>\r\nBcc: bad@example.org",), ORIGIN),
        (("not-an-id", ORIGIN), ORIGIN),
        (("<" + "a" * 999 + "@x>", ORIGIN), ORIGIN),
        ((ORIGIN,) * 32, ORIGIN),
        ((ORIGIN, "<" + "a" * 980 + "@x>"), ORIGIN),
        ((EARLIER,), ORIGIN),
        ((ORIGIN,), None),
        ([ORIGIN], ORIGIN),
        ((None,), ORIGIN),
    ],
)
def test_invalid_references_fail_before_transport(references, reply):
    with pytest.raises(ValueError, match="invalid_reply_reference"):
        smtp_send._prepare(ACCOUNT, replace(REQUEST, in_reply_to=reply, references=references))
