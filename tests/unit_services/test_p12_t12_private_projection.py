"""Synthetic same-read consistency only; no settings, DB, network or live rights."""

import asyncio
import copy
import json
from dataclasses import replace

import pytest

from app.integrations.file_workspace.insight_candidates import InsightCandidate, InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence
from app.integrations.file_workspace.private_payload import PrivatePayloadUnavailable, private_payload_is_canonical
from app.integrations.file_workspace.private_projection import serialize_same_read_payload
from app.services.file_workspace.insight_candidates import generate_insight_candidates
from tests.shared.file_content_read import SyntheticContentAuthority, synthetic_plain_text_read
from tests.shared.file_insight_candidates import SyntheticInsightCandidateGenerator

__source_only__ = True


def inputs():
    source = synthetic_plain_text_read(b"first\r\nsecond\nthird\n", provider_version="synthetic-exact-version")
    source = replace(source, context=replace(source.context, root_key="a" * 64))
    evidence = build_line_evidence(source, ranges=(LineRange(1, 1), LineRange(3, 3)))
    batch = InsightCandidateBatch("b" * 32, evidence, (InsightCandidate("Unverified", (1, 2)),))
    metadata = {
        "path_key": "c" * 64,
        "version_semantics": "synthetic-bytes-v1",
        "provider_object_id": None,
        "owner_epoch": "d" * 32,
        "connection_epoch": "e" * 32,
        "object_epoch": "f" * 32,
        "consent_revision": "consent-v1",
        "policy_revision": "policy-v1",
        "lifecycle": {
            "revision": "private-retention-v1",
            "captured_at": "2026-11-03T12:00:00.000000Z",
            "created_at": "2026-11-03T12:01:00.000000Z",
            "expires_at": "2026-11-04T12:00:00.000000Z",
        },
    }
    return source, batch, metadata


def reject(source, batch, metadata):
    with pytest.raises(PrivatePayloadUnavailable) as caught:
        serialize_same_read_payload(source, batch, metadata=metadata)
    assert caught.value.args == ("private_payload_unavailable",)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_explicit_projection_and_canonical_bytes():
    source, batch, metadata = inputs()
    before = copy.deepcopy((source, batch, metadata))
    payload = serialize_same_read_payload(source, batch, metadata=metadata)
    assert private_payload_is_canonical(payload)
    result = json.loads(payload)
    assert result["scope"] == {
        "tenant_id": 1,
        "owner_id": 2,
        "connection_id": 3,
        "object_id": 4,
        "root_key": "a" * 64,
        "path_key": "c" * 64,
    }
    assert result["source"]["raw_sha256"] == source.extraction.raw_sha256
    assert result["source"]["text_sha256"] == source.extraction.text_sha256
    assert result["source"]["provider_version"] == source.provider_version
    assert result["binding"]["read_attempt_id"] == source.context.request.nonce
    assert result["binding"]["generation_attempt_id"] == batch.generation_id
    assert result["excerpts"] == [{"start": 1, "end": 1, "text": "first"}, {"start": 3, "end": 3, "text": "third"}]
    assert result["candidates"] == [{"text": "Unverified", "evidence_indices": [1, 2]}]
    assert result["line_count"] == source.extraction.line_count
    assert result["review_status"] == "unverified"
    assert (source, batch, metadata) == before
    assert serialize_same_read_payload(copy.deepcopy(source), copy.deepcopy(batch), metadata=metadata) == payload
    for forbidden in (b"disk:/", b"file.txt", b"credential-key", b"processing-policy-key", b"second"):
        assert forbidden not in payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", 9),
        ("actor_id", 9),
        ("connection_id", 9),
        ("object_id", 9),
        ("nonce", "9" * 32),
        ("tenant_id", True),
    ],
)
def test_request_substitution(field, value):
    source, batch, metadata = inputs()
    context = replace(source.context, request=replace(source.context.request, **{field: value}))
    reject(replace(source, context=context), batch, metadata)


@pytest.mark.parametrize(
    "field,value",
    [
        ("path", "disk:/other.txt"),
        ("name", "other.txt"),
        ("root_key", "9" * 64),
        ("policy_key", "other"),
        ("credential_key", "other"),
        ("observation_number", 2),
        ("observation_number", True),
        ("processing_boundary", "local_only"),
        ("processing_policy_key", "other"),
    ],
)
def test_context_substitution(field, value):
    source, batch, metadata = inputs()
    reject(replace(source, context=replace(source.context, **{field: value})), batch, metadata)


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_sha256", "9" * 64),
        ("text_sha256", "9" * 64),
        ("parser_version", "other"),
        ("line_count", 2),
        ("line_count", True),
        ("text", "other\nsecond\nthird\n"),
        ("text_characters", 1),
        ("input_bytes", 0),
    ],
)
def test_extraction_substitution(field, value):
    source, batch, metadata = inputs()
    reject(replace(source, extraction=replace(source.extraction, **{field: value})), batch, metadata)


@pytest.mark.parametrize("version", [None, "other", "", True])
def test_missing_or_changed_version(version):
    source, batch, metadata = inputs()
    reject(replace(source, provider_version=version), batch, metadata)


def test_consistently_absent_version_is_not_replaced_with_hash():
    source, batch, metadata = inputs()
    source = replace(source, provider_version=None)
    batch = replace(batch, evidence=replace(batch.evidence, provider_version=None))
    reject(source, batch, metadata)


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_sha256", "9" * 64),
        ("text_sha256", "9" * 64),
        ("parser_version", "other"),
        ("provider_version", "other"),
        ("line_count", 9),
        ("excerpts", ()),
    ],
)
def test_evidence_substitution(field, value):
    source, batch, metadata = inputs()
    reject(source, replace(batch, evidence=replace(batch.evidence, **{field: value})), metadata)


@pytest.mark.parametrize("field,value", [("text", "forged"), ("start", 2), ("end", 2), ("start", True)])
def test_forged_excerpt(field, value):
    source, batch, metadata = inputs()
    excerpts = (replace(batch.evidence.excerpts[0], **{field: value}), batch.evidence.excerpts[1])
    reject(source, replace(batch, evidence=replace(batch.evidence, excerpts=excerpts)), metadata)


@pytest.mark.parametrize("indices", [(), (0,), (3,), (True,), (2, 1), (1, 1), (1.0,), [1], tuple(range(100))])
def test_invalid_candidate_references(indices):
    source, batch, metadata = inputs()
    reject(source, replace(batch, candidates=(InsightCandidate("candidate", indices),)), metadata)


@pytest.mark.parametrize("text", ["", " ", "x" * 601, "secret\rtext", "\ud800", 1, True])
def test_invalid_candidate_text(text):
    source, batch, metadata = inputs()
    reject(source, replace(batch, candidates=(InsightCandidate(text, (1,)),)), metadata)


@pytest.mark.parametrize("generation_id", ["", "g" * 32, "a" * 31, "a" * 33, None, 1])
def test_invalid_generation_id(generation_id):
    source, batch, metadata = inputs()
    reject(source, replace(batch, generation_id=generation_id), metadata)


@pytest.mark.parametrize("field", sorted(inputs()[2]))
def test_required_metadata(field):
    source, batch, metadata = inputs()
    del metadata[field]
    reject(source, batch, metadata)


@pytest.mark.parametrize(
    "field", ["scope", "source", "binding", "excerpts", "candidates", "review_status", "read_attempt_id"]
)
def test_metadata_cannot_override_derived_fields(field):
    source, batch, metadata = inputs()
    metadata[field] = "forged"
    reject(source, batch, metadata)


@pytest.mark.parametrize(
    "field,value",
    [
        ("path_key", "not-a-key"),
        ("owner_epoch", "bad"),
        ("version_semantics", "bad label"),
        ("provider_object_id", "secret\rvalue"),
        ("lifecycle", {}),
        ("consent_revision", None),
    ],
)
def test_metadata_schema_is_not_bypassed(field, value):
    source, batch, metadata = inputs()
    metadata[field] = value
    reject(source, batch, metadata)


def test_abstention_is_valid_not_knowledge():
    source, batch, metadata = inputs()
    payload = serialize_same_read_payload(source, replace(batch, candidates=()), metadata=metadata)
    assert json.loads(payload)["candidates"] == []
    assert json.loads(payload)["review_status"] == "unverified"


def test_bounds_and_exact_containers():
    source, batch, metadata = inputs()
    reject(source, replace(batch, candidates=list(batch.candidates)), metadata)
    reject(source, replace(batch, candidates=batch.candidates * 6), metadata)
    reject(source, replace(batch, candidates=(object(),)), metadata)
    reject(source, replace(batch, candidates=tuple(InsightCandidate("x" * 600, (1,)) for _ in range(4))), metadata)
    reject(source, object(), metadata)
    reject(object(), batch, metadata)
    reject(source, batch, None)

    class Metadata(dict):
        pass

    reject(source, batch, Metadata(metadata))

    class Batch(InsightCandidateBatch):
        pass

    reject(source, Batch(batch.generation_id, batch.evidence, batch.candidates), metadata)


def test_promoted_batch_is_rejected():
    source, batch, metadata = inputs()
    object.__setattr__(batch, "review_status", "verified")
    reject(source, batch, metadata)


def test_injected_synthetic_generator_to_projection():
    source, batch, metadata = inputs()
    authority = SyntheticContentAuthority()
    authority.initial_updates = {"root_key": source.context.root_key}
    generator = SyntheticInsightCandidateGenerator()
    generated = asyncio.run(
        generate_insight_candidates(source, batch.evidence, authority=authority, generator=generator)
    )
    payload = serialize_same_read_payload(source, generated, metadata=metadata)
    assert private_payload_is_canonical(payload)
    assert json.loads(payload)["binding"]["generation_attempt_id"] == generator.inputs[0].generation_id
    assert len(authority.requests) == 2


def test_consistent_forgery_is_not_claimed_as_authenticity():
    source, batch, metadata = inputs()
    # Application code can forge the entire graph; this helper is not a seal.
    source = replace(source, extraction=replace(source.extraction, raw_sha256="9" * 64))
    batch = replace(batch, evidence=replace(batch.evidence, raw_sha256="9" * 64))
    assert private_payload_is_canonical(serialize_same_read_payload(source, batch, metadata=metadata))
