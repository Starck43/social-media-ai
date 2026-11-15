"""Standalone, in-memory cryptographic checks: no app fixtures, DB or network."""

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SPEC = importlib.util.spec_from_file_location("p18_packaging_updates", Path(__file__).parents[1] / "updates.py")
updates = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = updates
SPEC.loader.exec_module(updates)


@pytest.fixture
def sample():
    key = Ed25519PrivateKey.generate()
    artifact = b"standalone fake executable; never executed"
    payload = {
        "schema": 1,
        "product": "ai-assistant-connector",
        "version": "0.3.0",
        "platform": "linux-x86_64",
        "sha256": hashlib.sha256(artifact).hexdigest(),
        "size": len(artifact),
        "issued_at": 100,
        "expires_at": 200,
    }
    return key, artifact, payload


def verify(sample, *, raw=None, signature=None, **kwargs):
    key, _, payload = sample
    raw = json.dumps(payload).encode() if raw is None else raw
    return updates.verify_manifest(
        raw,
        key.sign(raw) if signature is None else signature,
        trusted_key=kwargs.pop("trusted_key", key.public_key().public_bytes_raw()),
        platform="linux-x86_64",
        current_version="0.2.0",
        now=150,
        **kwargs,
    )


def test_authenticated_artifact(sample):
    release = verify(sample)
    updates.verify_artifact(release, sample[1])


def test_tampered_manifest_and_whitespace(sample):
    raw = json.dumps(sample[2]).encode()
    signature = sample[0].sign(raw)
    for changed in (raw.replace(b"0.3.0", b"0.4.0"), raw + b" "):
        with pytest.raises(updates.UpdateRejected):
            verify(sample, raw=changed, signature=signature)


def test_untrusted_key(sample):
    with pytest.raises(updates.UpdateRejected):
        verify(sample, trusted_key=Ed25519PrivateKey.generate().public_key().public_bytes_raw())


@pytest.mark.parametrize("trusted_key", [b"", b"short", bytes(33)])
def test_missing_or_malformed_trust(sample, trusted_key):
    with pytest.raises(updates.UpdateRejected):
        verify(sample, trusted_key=trusted_key)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("product", "other"),
        ("platform", "../linux-x86_64"),
        ("version", "0.1.0"),
        ("version", "0.2.0"),
        ("version", "01.3.0"),
        ("version", "0.3.0/../evil"),
        ("sha256", "A" * 64),
        ("sha256", "a" * 63),
        ("size", True),
        ("size", 0),
        ("size", updates.MAX_ARTIFACT_BYTES + 1),
        ("issued_at", 151),
        ("issued_at", True),
        ("expires_at", 150),
        ("expires_at", 100 + updates.MAX_LIFETIME_SECONDS + 1),
        ("untrusted_key", "ignored keys must not be accepted"),
    ],
)
def test_invalid_fields(sample, field, value):
    sample[2][field] = value
    with pytest.raises(updates.UpdateRejected):
        verify(sample)


@pytest.mark.parametrize("raw", [b"{}", b"[]", b'{"size":1,"size":2}', b'{"size":NaN}', b"\xff", b"[" * 2000])
def test_signed_invalid_json(sample, raw):
    with pytest.raises(updates.UpdateRejected):
        verify(sample, raw=raw)


def test_oversized_manifest(sample):
    with pytest.raises(updates.UpdateRejected):
        verify(sample, raw=b" " * (updates.MAX_MANIFEST_BYTES + 1))


def test_tampered_artifact(sample):
    release = verify(sample)
    for changed in (sample[1] + b"x", b"x" * len(sample[1])):
        with pytest.raises(updates.UpdateRejected):
            updates.verify_artifact(release, changed)


def test_streamed_artifact_and_bound(sample):
    release = verify(sample)
    updates.verify_artifact_stream(release, io.BytesIO(sample[1]))
    with pytest.raises(updates.UpdateRejected):
        updates.verify_artifact_stream(release, io.BytesIO(sample[1] + b"x" * 1000))
