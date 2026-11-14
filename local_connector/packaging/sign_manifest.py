"""Offline release-signing tool; never generates keys or publishes artifacts."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import time
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key

from updates import MAX_ARTIFACT_BYTES, PLATFORMS, _version, verify_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--key", type=Path, required=True, help="Private Ed25519 PEM kept outside the repository")
    parser.add_argument("--encrypted-key", action="store_true", help="Prompt for key password (never a CLI argument)")
    parser.add_argument("--output", type=Path, required=True, help="New manifest file (never overwritten)")
    args = parser.parse_args()
    _version(args.version)
    password = getpass.getpass("Signing key password: ").encode() if args.encrypted_key else None
    key = load_pem_private_key(args.key.read_bytes(), password=password)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing requires an Ed25519 key")
    digest = hashlib.sha256()
    size = 0
    with args.artifact.open("rb") as artifact:
        while chunk := artifact.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact exceeds update limit")
            digest.update(chunk)
    now = int(time.time())
    raw = json.dumps(
        {
            "schema": 1,
            "product": "ai-assistant-connector",
            "version": args.version,
            "platform": args.platform,
            "sha256": digest.hexdigest(),
            "size": size,
            "issued_at": now,
            "expires_at": now + 7 * 24 * 60 * 60,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = key.sign(raw)
    verify_manifest(
        raw,
        signature,
        trusted_key=key.public_key().public_bytes_raw(),
        platform=args.platform,
        current_version="0.0.0",
        now=now,
    )
    with args.output.open("xb") as manifest:
        manifest.write(raw)
    with args.output.with_suffix(args.output.suffix + ".sig").open("xb") as detached:
        detached.write(signature)


if __name__ == "__main__":
    main()
