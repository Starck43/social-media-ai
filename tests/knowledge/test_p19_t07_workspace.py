"""DB-exempt unit tests for ``evidence_dict`` in the knowledge workspace service (P19-T07).

``evidence_dict`` is a pure projection helper: it only reads attributes from a
record (and optionally a pinned revision) and composes the chat handoff row.
The tests therefore use ``SimpleNamespace`` doubles and touch no database,
network or paid provider.
"""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.knowledge import workspace as mod

__source_only__ = True


def make_record(**overrides):
    base = dict(
        id=11,
        context_type="knowledge",
        title="Регламент",
        source_ref="drive/regs/reg.pdf",
        citation_anchor="drive/regs/reg.pdf · p.2",
        data_boundary="company",
        acl_scope="public",
        revision_hash="recmut",
        excerpt="Актуальный отрывок.",
        extraction_limit_note="OCR page 2",
        counterparty="ООО Ромашка",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_revision(**overrides):
    base = dict(
        id=77,
        content_hash="revhash",
        body_text="Текст утверждённой редакции.",
        version=3,
        status="approved",
        approved_by="owner",
        unsupported_terms_note="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def chat_params(row):
    parts = urlsplit(row["chat_url"])
    assert parts.path == "/app/chat"
    return {k: v[0] for k, v in parse_qs(parts.query).items()}


# --------------------------------------------------------------------------- #
# Without a pinned revision
# --------------------------------------------------------------------------- #


def test_evidence_dict_without_revision_uses_record_fields():
    record = make_record()
    row = mod.evidence_dict(record)

    assert row["id"] == 11
    assert row["context_type"] == "knowledge"
    assert row["title"] == "Регламент"
    assert row["source_ref"] == "drive/regs/reg.pdf"
    assert row["citation_anchor"] == "drive/regs/reg.pdf · p.2"
    assert row["data_boundary"] == "company"
    assert row["acl_scope"] == "public"
    assert row["revision_hash"] == "recmut"
    assert row["excerpt"] == "Актуальный отрывок."
    assert row["extraction_limit_note"] == "OCR page 2"


def test_evidence_dict_without_revision_has_no_revision_only_keys():
    row = mod.evidence_dict(make_record())
    for key in ("version", "status", "approved_by", "counterparty", "unsupported_terms_note"):
        assert key not in row


def test_evidence_dict_context_id_matches_record_id():
    row = mod.evidence_dict(make_record(id=11))
    assert row["context_id"] == "11"


def test_evidence_dict_chat_url_encodes_context_and_boundary():
    row = mod.evidence_dict(make_record(id=11, data_boundary="company", context_type="knowledge"))
    assert chat_params(row) == {"context_type": "knowledge", "context_id": "11", "boundary": "company"}


@pytest.mark.parametrize(
    ("context_type", "boundary"),
    [
        ("knowledge", "local_only"),
        ("followup", "cloud"),
        ("case", "company"),
        ("contact", "local_only"),
    ],
)
def test_evidence_dict_chat_url_reflects_stored_context_and_boundary(context_type, boundary):
    row = mod.evidence_dict(make_record(context_type=context_type, data_boundary=boundary))
    params = chat_params(row)
    assert params["context_type"] == context_type
    assert params["boundary"] == boundary


# --------------------------------------------------------------------------- #
# With a pinned revision
# --------------------------------------------------------------------------- #


def test_evidence_dict_with_revision_overrides_hash_and_excerpt():
    record = make_record()
    revision = make_revision()
    row = mod.evidence_dict(record, context_type="document", revision=revision)

    assert row["context_type"] == "document"
    assert row["revision_hash"] == "revhash"
    assert row["excerpt"] == "Текст утверждённой редакции."
    assert row["context_id"] == "77"
    assert row["extraction_limit_note"] == ""


def test_evidence_dict_with_revision_adds_version_status_and_notes():
    row = mod.evidence_dict(make_record(), context_type="document", revision=make_revision())
    assert row["version"] == 3
    assert row["status"] == "Согласовано"
    assert row["approved_by"] == "owner"
    assert row["counterparty"] == "ООО Ромашка"
    assert row["unsupported_terms_note"] == ""


def test_evidence_dict_pins_revision_not_latest_pointer():
    """Handoff must point at the actual revision, never the mutable record id."""
    row = mod.evidence_dict(make_record(id=11), context_type="document", revision=make_revision(id=77))
    assert row["context_id"] == "77"
    params = chat_params(row)
    assert params["context_id"] == "77"
    assert params["context_type"] == "document"


def test_evidence_dict_with_revision_keeps_record_source_and_boundary():
    row = mod.evidence_dict(
        make_record(source_ref="drive/x.pdf", data_boundary="local_only"),
        context_type="document",
        revision=make_revision(),
    )
    assert row["source_ref"] == "drive/x.pdf"
    assert row["data_boundary"] == "local_only"
    params = chat_params(row)
    assert params["boundary"] == "local_only"


def test_evidence_dict_with_revision_retains_unsupported_terms_note():
    row = mod.evidence_dict(
        make_record(),
        context_type="document",
        revision=make_revision(unsupported_terms_note="Термин X не поддерживается"),
    )
    assert row["unsupported_terms_note"] == "Термин X не поддерживается"


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("draft", "Черновик"),
        ("in_review", "На согласовании"),
        ("approved", "Согласовано"),
        ("rejected", "Отклонено"),
        ("revision_requested", "Требуется доработка"),
    ],
)
def test_evidence_dict_maps_every_revision_status_to_label(status, label):
    row = mod.evidence_dict(make_record(), context_type="document", revision=make_revision(status=status))
    assert row["status"] == label


def test_evidence_dict_unknown_revision_status_raises():
    with pytest.raises(KeyError):
        mod.evidence_dict(make_record(), context_type="document", revision=make_revision(status="bogus"))


# --------------------------------------------------------------------------- #
# context_type override
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("context_type", sorted(mod.CONTEXT_TYPES))
def test_evidence_dict_accepts_every_declared_context_type(context_type):
    record = make_record(context_type="knowledge")
    row = mod.evidence_dict(record, context_type=context_type)
    assert row["context_type"] == context_type
    assert chat_params(row)["context_type"] == context_type


@pytest.mark.parametrize("override", [None, ""])
def test_evidence_dict_falsy_override_falls_back_to_record_context_type(override):
    record = make_record(context_type="followup")
    row = mod.evidence_dict(record, context_type=override)
    assert row["context_type"] == "followup"


def test_evidence_dict_override_does_not_mutate_record():
    record = make_record(context_type="knowledge")
    mod.evidence_dict(record, context_type="document", revision=make_revision())
    assert record.context_type == "knowledge"
    assert record.revision_hash == "recmut"


def test_evidence_dict_returns_fresh_dict_each_call():
    record = make_record()
    first = mod.evidence_dict(record)
    second = mod.evidence_dict(record)
    assert first is not second
    assert first == second
