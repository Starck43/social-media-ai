"""Reply origin header read: strictly mocked IMAP, no DB or network."""

import imaplib
from types import SimpleNamespace

import pytest

from app.services.communications.ingest import hash_message_id
from app.services.communications.reply import ReplyUnavailable, fetch_email_reply_headers

__source_only__ = True

MESSAGE_ID = "<id@example.invalid>"
ROOT = "<root@example.invalid>"
RAW = f"Message-ID: {MESSAGE_ID}\r\nReferences: {ROOT}\r\n\r\n".encode()


class Client:
    def __init__(self):
        self.untagged_responses = {"UIDVALIDITY": [b"123"]}
        self.calls = []
        self.status = "OK"
        self.payload = [(b"42 (UID 42 BODY[HEADER.FIELDS] {1}", RAW), b")"]

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return self.status, [b"1"]

    def uid(self, command, uid, spec):
        self.calls.append(("uid", command, uid, spec))
        return self.status, self.payload


def inbound():
    return SimpleNamespace(original_id="INBOX:123:42", external_message_id_hash=hash_message_id(MESSAGE_ID))


def test_pinned_source_reads_only_bounded_headers_without_changing_flags():
    client = Client()
    headers = fetch_email_reply_headers(client, inbound(), ("INBOX",))
    assert headers.in_reply_to == MESSAGE_ID and headers.references == (ROOT, MESSAGE_ID)
    assert client.calls == [
        ("select", "INBOX", True),
        ("uid", "FETCH", "42", "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES)]<0.16385>)"),
    ]


@pytest.mark.parametrize(
    "original",
    [
        "bad",
        "INBOX:0:42",
        "INBOX:123:0",
        "INBOX:123:4294967296",
        "INBOX:١٢٣:42",
        "../bad:123:42",
        "INBOX\n:123:42",
        "Sent:123:42",
    ],
)
def test_untrusted_or_unavailable_source_is_rejected(original):
    source = inbound()
    source.original_id = original
    client = Client()
    with pytest.raises(ReplyUnavailable, match="^reply_headers_unavailable$"):
        fetch_email_reply_headers(client, source, ("INBOX",))
    assert not client.calls


@pytest.mark.parametrize("epoch", [None, [], [b"124"], [b"garbage"], ["123"]])
def test_missing_or_changed_epoch_is_rejected_before_fetch(epoch):
    client = Client()
    client.untagged_responses = {"UIDVALIDITY": epoch}
    with pytest.raises(ReplyUnavailable):
        fetch_email_reply_headers(client, inbound(), ("INBOX",))
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"Message-ID: <id@example.invalid>\r\nMessage-ID: <other@example.invalid>\r\n\r\n",
        b"Message-ID: <id@example.invalid>\r\nReferences: <r@x>\r\nReferences: <s@x>\r\n\r\n",
        b"Message-ID: <id@example.invalid>\r\nBcc: bad@x\r\n\r\n",
        b"Message-ID: <id@example.invalid>\r\nReferences: <x@a> garbage\r\n\r\n",
        b"Message-ID: <other@example.invalid>\r\n\r\n",
        b"Message-ID: <id@example.invalid>\r\nReferences: <x@a>\x00\r\n\r\n",
        b"Message-ID: <id@example.invalid>\r\n",
        b"x" * 16385,
    ],
)
def test_rejects_malformed_mismatched_or_truncated_header(raw):
    client = Client()
    client.payload = [(b"42 (UID 42 BODY[HEADER.FIELDS]", raw), b")"]
    with pytest.raises(ReplyUnavailable):
        fetch_email_reply_headers(client, inbound(), ("INBOX",))


@pytest.mark.parametrize("metadata", [b"43 (UID 43 BODY[]", b"42 (BODY[]", b"42 (UID 42 UID 43 BODY[]"])
def test_rejects_wrong_or_ambiguous_fetched_uid(metadata):
    client = Client()
    client.payload = [(metadata, RAW), b")"]
    with pytest.raises(ReplyUnavailable):
        fetch_email_reply_headers(client, inbound(), ("INBOX",))


def test_provider_errors_are_redacted():
    client = Client()

    def broken(*args, **kwargs):
        raise imaplib.IMAP4.error("private server detail")

    client.select = broken
    with pytest.raises(ReplyUnavailable, match="^reply_headers_unavailable$") as exc:
        fetch_email_reply_headers(client, inbound(), ("INBOX",))
    assert exc.value.__cause__ is None
