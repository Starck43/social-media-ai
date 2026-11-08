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
