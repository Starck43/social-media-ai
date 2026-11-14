"""Frozen local-only entry point; never starts the application/cloud runtime."""

from __future__ import annotations

import argparse
import os
import platform
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path


def data_directory() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "AI Assistant Connector"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AI Assistant Connector"
    return Path.home() / ".local" / "share" / "ai-assistant-connector"


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-only connector development build (not activated)")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--verify-update", nargs=3, metavar=("MANIFEST", "SIGNATURE", "ARTIFACT"))
    args = parser.parse_args()
    if args.version:
        from importlib.metadata import version

        print(version("social-media-ai-local-connector"))
        return 0
    if args.verify_update:
        return verify_update(args.verify_update)
    if sys.platform == "win32":
        print("Unavailable: native Windows handles and owner-private ACLs are not yet certified.", file=sys.stderr)
        return 2

    from local_connector.config import ConnectorConfig
    from local_connector.runtime import ConnectorRuntime

    stop = threading.Event()
    for event in (signal.SIGINT, signal.SIGTERM):
        signal.signal(event, lambda *_: stop.set())
    runtime = None
    try:
        runtime = ConnectorRuntime(ConnectorConfig(index_path=data_directory() / "index.sqlite3"))
        host, port = runtime.start()
        address = f"http://{host}:{port}"
        print(f"Local-only development connector: {address}", flush=True)
        if not args.no_browser:
            webbrowser.open(address)
        stop.wait()
    except (OSError, ValueError):
        print("Connector unavailable: local data directory or safe filesystem access denied.", file=sys.stderr)
        return 2
    finally:
        if runtime is not None:
            runtime.close()
    return 0


def verify_update(paths: list[str]) -> int:
    """Verify locally using only the build-bundled key; no update is applied."""
    from importlib.metadata import version

    from local_connector.packaging.updates import (
        MAX_MANIFEST_BYTES,
        UpdateRejected,
        verify_artifact_stream,
        verify_manifest,
    )

    try:
        if not getattr(sys, "frozen", False):
            raise UpdateRejected("no bundled trust anchor")
        key_file = Path(sys._MEIPASS) / "connector-release" / "update-key.bin"
        with key_file.open("rb") as key:
            trusted_key = key.read(33)
        manifest_path, signature_path, artifact_path = map(Path, paths)
        with manifest_path.open("rb") as manifest:
            raw = manifest.read(MAX_MANIFEST_BYTES + 1)
        with signature_path.open("rb") as detached:
            signature = detached.read(65)
        release = verify_manifest(
            raw,
            signature,
            trusted_key=trusted_key,
            platform=f"{sys.platform}-{platform.machine()}",
            current_version=version("social-media-ai-local-connector"),
            now=int(time.time()),
        )
        with artifact_path.open("rb") as artifact:
            before = os.fstat(artifact.fileno())
            verify_artifact_stream(release, artifact)
            after = os.fstat(artifact.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise UpdateRejected("artifact changed during verification")
    except (OSError, ValueError):
        print("Update rejected: authentication, target or artifact validation failed.", file=sys.stderr)
        return 2
    print("Update authenticated; no files installed or executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
