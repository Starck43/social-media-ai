"""Pure origin selection; no persistence, auth, approval, or provider I/O."""

from types import SimpleNamespace as NS

import pytest

from app.services.communications.reply import ReplyOrigin, ReplyUnavailable, select_email_origin


def rows():
    thread = NS(id=11, tenant_id=1, contact_id=2, connection_id=3, channel="email")
    inbound = NS(
        id=12,
        tenant_id=1,
        thread_id=11,
        contact_id=2,
        connection_id=3,
        channel="email",
        direction="inbound",
        is_hidden=False,
        unavailable_at=None,
        sender_encrypted="Example <reply@example.invalid>",
    )
    identity = NS(
        id=13,
        tenant_id=1,
        contact_id=2,
        connection_id=3,
        kind="email",
        verified=True,
        value_encrypted="reply@example.invalid",
    )
    return thread, inbound, identity


def test_origin_is_derived_from_scoped_verified_inbound():
    thread, inbound, identity = rows()
    assert select_email_origin(thread, inbound, [identity]) == ReplyOrigin(11, 12, 2, 3, 13)
    identity.connection_id = None
    assert select_email_origin(thread, inbound, [identity]).identity_id == 13


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", 9),
        ("thread_id", 9),
        ("contact_id", 9),
        ("connection_id", 9),
        ("channel", "telegram"),
        ("direction", "outbound"),
        ("is_hidden", True),
        ("unavailable_at", object()),
        ("sender_encrypted", "a@b.invalid, c@d.invalid"),
    ],
)
def test_mismatched_or_unusable_inbound_is_refused(field, value):
    thread, inbound, identity = rows()
    setattr(inbound, field, value)
    with pytest.raises(ReplyUnavailable, match="^reply_origin_unavailable$"):
        select_email_origin(thread, inbound, [identity])


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", 9),
        ("contact_id", 9),
        ("connection_id", 9),
        ("kind", "phone"),
        ("verified", False),
        ("value_encrypted", "another@example.invalid"),
    ],
)
def test_nonmatching_identity_is_refused(field, value):
    thread, inbound, identity = rows()
    setattr(identity, field, value)
    with pytest.raises(ReplyUnavailable):
        select_email_origin(thread, inbound, [identity])


def test_ambiguous_identity_is_refused_without_disclosing_sender():
    thread, inbound, identity = rows()
    other = NS(**vars(identity))
    other.id = 14
    with pytest.raises(ReplyUnavailable) as error:
        select_email_origin(thread, inbound, [identity, other])
    assert "example.invalid" not in str(error.value)


def test_unsupported_thread_and_missing_contact_refused():
    thread, inbound, identity = rows()
    for channel, contact in (("telegram", 2), ("email", None)):
        thread.channel, thread.contact_id = channel, contact
        with pytest.raises(ReplyUnavailable):
            select_email_origin(thread, inbound, [identity])


def test_verified_reply_headers_preserve_exact_wire_values_and_hide_repr():
    from app.services.communications.ingest import hash_message_id
    from app.services.communications.reply import verified_email_headers

    message_id = "<Message.123@example.invalid>"
    inbound = NS(external_message_id_hash=hash_message_id(message_id))
    headers = verified_email_headers(inbound, message_id, ["<root@example.invalid>"])
    assert headers.approval_value() == {
        "in_reply_to": message_id,
        "references": ["<root@example.invalid>", message_id],
    }
    assert message_id not in repr(headers)


@pytest.mark.parametrize(
    "message_id,refs,stored",
    [
        ("<right@example.invalid>", [], "wrong"),
        ("<right@example.invalid>\r\nBcc: x@example.invalid", [], "anything"),
        ("<right@example.invalid>", ["<root@example.invalid>\r\nBcc: x@example.invalid"], "match"),
        ("<right@example.invalid>", ["<x@example.invalid>"] * 31, "match"),
        ("<right@example.invalid>", ["<" + "x" * 990 + "@example.invalid>"], "match"),
        ("<right@example.invalid>", "<root@example.invalid>", "match"),
    ],
)
def test_reply_headers_refuse_mismatched_or_unsafe_input(message_id, refs, stored):
    from app.services.communications.ingest import hash_message_id
    from app.services.communications.reply import verified_email_headers

    source_hash = hash_message_id(message_id) if stored == "match" else stored
    with pytest.raises(ReplyUnavailable, match="^reply_headers_unavailable$"):
        verified_email_headers(NS(external_message_id_hash=source_hash), message_id, refs)


def test_reply_header_approval_value_changes_with_exact_reference():
    from app.services.communications.ingest import hash_message_id
    from app.services.communications.outbox_store import digest
    from app.services.communications.reply import verified_email_headers

    message_id = "<id@example.invalid>"
    inbound = NS(external_message_id_hash=hash_message_id(message_id))
    one = verified_email_headers(inbound, message_id, ["<one@example.invalid>"])
    two = verified_email_headers(inbound, message_id, ["<two@example.invalid>"])
    assert digest({"reply": one.approval_value()}) != digest({"reply": two.approval_value()})


def evidence_rows():
    thread, inbound, identity = rows()
    message = NS(
        id=19, tenant_id=1, contact_id=2, purpose="transactional", reply_thread_id=11, reply_inbound_message_id=12
    )
    target = NS(tenant_id=1, message_id=19, channel="email", connection_id=3, identity_id=13)
    return message, target, thread, inbound, identity


def test_d15_candidate_requires_same_inbound_route():
    from app.services.communications.reply import reply_exemption_candidate

    message, target, thread, inbound, identity = evidence_rows()
    assert reply_exemption_candidate(message, target, thread, inbound, [identity]) is True


@pytest.mark.parametrize(
    "obj,field,value",
    [
        ("message", "purpose", "marketing"),
        ("message", "purpose", "greeting"),
        ("message", "tenant_id", 9),
        ("message", "contact_id", 9),
        ("message", "reply_thread_id", 9),
        ("message", "reply_inbound_message_id", 9),
        ("target", "tenant_id", 9),
        ("target", "message_id", 9),
        ("target", "channel", "telegram"),
        ("target", "connection_id", 9),
        ("target", "identity_id", 9),
        ("inbound", "direction", "outbound"),
        ("identity", "verified", False),
    ],
)
def test_d15_candidate_rejects_changed_or_nonreply_evidence(obj, field, value):
    from app.services.communications.reply import reply_exemption_candidate

    message, target, thread, inbound, identity = evidence_rows()
    setattr({"message": message, "target": target, "inbound": inbound, "identity": identity}[obj], field, value)
    assert reply_exemption_candidate(message, target, thread, inbound, [identity]) is False


def test_d15_missing_binding_does_not_exempt_existing_outbox_message():
    from app.services.communications.reply import reply_exemption_candidate

    message, target, thread, inbound, identity = evidence_rows()
    del message.reply_thread_id
    assert reply_exemption_candidate(message, target, thread, inbound, [identity]) is False
