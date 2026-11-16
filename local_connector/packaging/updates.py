"""Offline update authentication primitives; no downloader, installer or activation.

Trust anchors are supplied by the release composition root, never by a manifest.
No production key is provisioned in development bundles, so live updates stay off.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import BinaryIO

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_MANIFEST_BYTES = 16 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_LIFETIME_SECONDS = 30 * 24 * 60 * 60
PLATFORMS = frozenset({"linux-x86_64", "darwin-arm64", "darwin-x86_64", "win32-AMD64"})


class UpdateRejected(ValueError):
    """Fixed, sanitized denial; never contains attacker-provided content."""


@dataclass(frozen=True)
class Release:
    version: tuple[int, int, int]
    platform: str
    sha256: str
    size: int


def _version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})", value
    ):
        raise UpdateRejected("invalid version")
    return tuple(int(part) for part in value.split("."))


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateRejected("duplicate manifest field")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise UpdateRejected("invalid manifest constant")


def verify_manifest(
    raw: bytes,
    signature: bytes,
    *,
    trusted_key: bytes,
    platform: str,
    current_version: str,
    now: int,
) -> Release:
    """Verify exact bytes before parsing; reject replay/downgrade and wrong target.

    Caller must provide trusted time and its installed version/build platform.
    The key must come from authenticated bundled configuration, not a download.
    This pure verifier deliberately cannot write, extract, install or execute.
    """
    if not raw or len(raw) > MAX_MANIFEST_BYTES or len(signature) != 64 or len(trusted_key) != 32:
        raise UpdateRejected("invalid authentication input")
    try:
        Ed25519PublicKey.from_public_bytes(trusted_key).verify(signature, raw)
    except (ValueError, InvalidSignature):
        raise UpdateRejected("manifest authentication failed") from None
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise UpdateRejected("invalid manifest encoding") from None
    expected = {"schema", "product", "version", "platform", "sha256", "size", "issued_at", "expires_at"}
    if type(payload) is not dict or set(payload) != expected:
        raise UpdateRejected("invalid manifest schema")
    if type(payload["schema"]) is not int or payload["schema"] != 1:
        raise UpdateRejected("unsupported manifest schema")
    if payload["product"] != "ai-assistant-connector":
        raise UpdateRejected("wrong product")
    if platform not in PLATFORMS or payload["platform"] != platform:
        raise UpdateRejected("wrong platform")
    version = _version(payload["version"])
    if version <= _version(current_version):
        raise UpdateRejected("update is not newer")
    issued, expires = payload["issued_at"], payload["expires_at"]
    if (
        type(now) is not int
        or type(issued) is not int
        or type(expires) is not int
        or not 0 <= issued <= now < expires
        or not 0 < expires - issued <= MAX_LIFETIME_SECONDS
    ):
        raise UpdateRejected("invalid manifest lifetime")
    digest, size = payload["sha256"], payload["size"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateRejected("invalid artifact digest")
    if type(size) is not int or not 0 < size <= MAX_ARTIFACT_BYTES:
        raise UpdateRejected("invalid artifact size")
    return Release(version, platform, digest, size)


def verify_artifact(release: Release, artifact: bytes) -> None:
    """Authenticate bounded artifact bytes; caller must bound reads beforehand.

    This is not an installation/staging API. A future installer must verify the
    same bytes at use and provide native private-directory/no-follow guarantees.
    """
    if len(artifact) != release.size or hashlib.sha256(artifact).hexdigest() != release.sha256:
        raise UpdateRejected("artifact authentication failed")


def verify_artifact_stream(release: Release, artifact: BinaryIO) -> None:
    """Bound streamed artifact reads; never extract, install or execute bytes."""
    digest = hashlib.sha256()
    size = 0
    while chunk := artifact.read(min(1024 * 1024, release.size - size + 1)):
        size += len(chunk)
        if size > release.size:
            raise UpdateRejected("artifact authentication failed")
        digest.update(chunk)
    if size != release.size or digest.hexdigest() != release.sha256:
        raise UpdateRejected("artifact authentication failed")
