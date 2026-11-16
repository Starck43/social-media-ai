"""Synthetic offline frozen-bundle probe; never launches the local runtime.

Prepare a throwaway public-key fixture, build with it, then check the bundle.
The synthetic key is not a release identity; never publish the probe bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def prepare(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    key = Ed25519PrivateKey.generate()
    artifact = b"synthetic update bytes, not an executable"
    now = int(time.time())
    raw = json.dumps(
        {
            "schema": 1,
            "product": "ai-assistant-connector",
            "version": "999.0.0",
            "platform": f"{sys.platform}-{platform.machine()}",
            "sha256": hashlib.sha256(artifact).hexdigest(),
            "size": len(artifact),
            "issued_at": now - 1,
            "expires_at": now + 3600,
        }
    ).encode()
    (directory / "public-key").write_bytes(key.public_key().public_bytes_raw())
    (directory / "manifest").write_bytes(raw)
    (directory / "signature").write_bytes(key.sign(raw))
    (directory / "artifact").write_bytes(artifact)
    # The private key is never persisted and this synthetic identity is disposable.


def check(directory: Path, binary: Path) -> None:
    def run(paths: list[Path], expected: int) -> None:
        result = subprocess.run(
            [str(binary), "--verify-update", *(str(path) for path in paths)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != expected:
            raise RuntimeError(f"frozen update probe failed: expected {expected}, got {result.returncode}")

    paths = [directory / "manifest", directory / "signature", directory / "artifact"]
    run(paths, 0)
    tampered = directory / "tampered-manifest"
    tampered.write_bytes(paths[0].read_bytes() + b" ")
    run([tampered, paths[1], paths[2]], 2)
    tampered = directory / "tampered-artifact"
    tampered.write_bytes(b"modified artifact")
    run([paths[0], paths[1], tampered], 2)
    tampered = directory / "wrong-signature"
    tampered.write_bytes(Ed25519PrivateKey.generate().sign(paths[0].read_bytes()))
    run([paths[0], tampered, paths[2]], 2)
    print("Frozen synthetic-key probe: 4 passed, 0 failed; no runtime or activation")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "check"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.directory.resolve())
    else:
        if args.binary is None:
            parser.error("--binary is required for check")
        check(args.directory.resolve(), args.binary.resolve())


if __name__ == "__main__":
    main()
