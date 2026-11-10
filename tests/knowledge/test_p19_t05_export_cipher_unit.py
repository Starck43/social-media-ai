"""Pure cryptographic-envelope checks; no database, authority or delivery."""

import pytest
from cryptography.fernet import Fernet

from app.services.knowledge.export_cipher import ExportArtifactUnavailable, ExportCipher


@pytest.fixture
def cipher():
    return ExportCipher(Fernet.generate_key().decode("ascii"), max_bytes=64)


def test_roundtrip_binary_and_hide_token_in_repr(cipher):
    data = b"\x00\xff\nexport"
    artifact = cipher.seal(data)
    assert cipher.open(artifact.token, expected_hash=artifact.payload_sha256, expected_size=artifact.size_bytes) == data
    assert artifact.token not in repr(artifact)


@pytest.mark.parametrize("payload", [b"", b"x" * 65, "not bytes", bytearray(b"x")])
def test_seal_rejects_invalid_or_oversized_content(cipher, payload):
    with pytest.raises(ExportArtifactUnavailable):
        cipher.seal(payload)


@pytest.mark.parametrize("size", [0, 65, True])
def test_read_rejects_invalid_size_before_decryption(cipher, size):
    artifact = cipher.seal(b"test")
    with pytest.raises(ExportArtifactUnavailable):
        cipher.open(artifact.token, expected_hash=artifact.payload_sha256, expected_size=size)


def test_rejects_wrong_hash_size_token_and_key(cipher):
    artifact = cipher.seal(b"test")
    cases = [
        (artifact.token, "sha256:" + "0" * 64, 4),
        (artifact.token, artifact.payload_sha256, 3),
        (artifact.token[:-2] + "AA", artifact.payload_sha256, 4),
        ("a" * 1000, artifact.payload_sha256, 4),
    ]
    for token, digest, size in cases:
        with pytest.raises(ExportArtifactUnavailable, match="export_artifact_unavailable"):
            cipher.open(token, expected_hash=digest, expected_size=size)
    with pytest.raises(ExportArtifactUnavailable):
        ExportCipher(Fernet.generate_key().decode("ascii"), max_bytes=64).open(
            artifact.token, expected_hash=artifact.payload_sha256, expected_size=4
        )


def test_ciphertext_is_purpose_separated_from_original_key(cipher):
    key = Fernet.generate_key()
    artifact = ExportCipher(key.decode(), max_bytes=64).seal(b"test")
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        Fernet(key).decrypt(artifact.token.encode())


def test_exact_limit_and_invalid_keys():
    key = Fernet.generate_key().decode("ascii")
    cipher = ExportCipher(key, max_bytes=16)
    item = cipher.seal(b"x" * 16)
    assert cipher.open(item.token, expected_hash=item.payload_sha256, expected_size=16) == b"x" * 16
    for invalid in ("", "bad", None):
        with pytest.raises(ExportArtifactUnavailable):
            ExportCipher(invalid, max_bytes=16)
    with pytest.raises(ExportArtifactUnavailable):
        ExportCipher(key, max_bytes=True)
