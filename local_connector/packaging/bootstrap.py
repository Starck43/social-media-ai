"""Frozen local-only entry point; never starts the application/cloud runtime."""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument(
        "--desktop", action="store_true", help="Run the native tray controls instead of the loopback page"
    )
    parser.add_argument("--self-test", action="store_true", help="Offline packaged dependency health check; no runtime")
    parser.add_argument("--install-update", nargs=3, metavar=("MANIFEST", "SIGNATURE", "ARTIFACT"))
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--confirm", action="store_true", help="Confirm rollback of application files only")
    parser.add_argument("--verify-update", nargs=3, metavar=("MANIFEST", "SIGNATURE", "ARTIFACT"))
    args = parser.parse_args()
    if args.version:
        from importlib.metadata import version

        print(version("social-media-ai-local-connector"))
        return 0
    if args.verify_update:
        return verify_update(args.verify_update)
    if args.self_test:
        return self_test()
    if sys.platform == "win32":
        print("Unavailable: native Windows handles and owner-private ACLs are not yet certified.", file=sys.stderr)
        return 2

    from local_connector.config import ConnectorConfig
    from local_connector.runtime import ConnectorRuntime

    stop = threading.Event()
    for event in (signal.SIGINT, signal.SIGTERM):
        signal.signal(event, lambda *_: stop.set())
    runtime = None
    updater = None
    try:
        key, feed = bundled_release()
        if key:
            from importlib.metadata import version

            from local_connector.packaging.updater import UpdateManager

            root = data_directory() / "updates"
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(root, 0o700)
            updater = UpdateManager(
                root, key, f"{sys.platform}-{platform.machine()}", version("social-media-ai-local-connector")
            )
        if args.install_update or args.rollback:
            if updater is None:
                raise ValueError("no bundled update identity")
            if args.install_update:
                updater.install(*map(Path, args.install_update))
            else:
                updater.rollback(confirmed=args.confirm)
            print("Authenticated application version selected. Restart the connector; personal data is unchanged.")
            return 0
        if updater is not None:
            active = updater.active_binary()
            if active is not None and active.resolve() != Path(sys.executable).resolve():
                updater.close()
                updater = None
                # The permanent bootstrap chooses the authenticated slot on every launch.
                os.execv(active, [str(active), *sys.argv[1:]])
        runtime = ConnectorRuntime(ConnectorConfig(index_path=data_directory() / "index.sqlite3"))
        host, port = runtime.start()
        address = f"http://{host}:{port}"
        print(f"Local-only development connector: {address}", flush=True)
        if args.desktop:
            from local_connector.packaging.desktop import run_desktop

            return run_desktop(runtime, address, stop, updater=updater, feed=feed)
        if not args.no_browser:
            webbrowser.open(address)
        stop.wait()
    except (OSError, ValueError):
        print("Connector unavailable: local data directory or safe filesystem access denied.", file=sys.stderr)
        return 2
    finally:
        if runtime is not None:
            runtime.close()
        if updater is not None:
            updater.close()
    return 0


def bundled_release() -> tuple[bytes, str | None]:
    """Only frozen build assets may define the update trust anchor and fixed feed."""
    if not getattr(sys, "frozen", False):
        return b"", None
    folder = Path(sys._MEIPASS) / "connector-release"
    with (folder / "update-key.bin").open("rb") as stream:
        key = stream.read(33)
    if len(key) not in (0, 32):
        raise ValueError("invalid bundled trust anchor")
    with (folder / "release-config.json").open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("invalid bundled release configuration")
    payload = json.loads(raw)
    if type(payload) is not dict or set(payload) != {"feed"} or payload["feed"] is not None and not key:
        raise ValueError("invalid bundled release configuration")
    feed = payload["feed"]
    if feed is not None:
        from local_connector.packaging.updater import _feed_base

        feed = _feed_base(feed)
    return key, feed


def self_test() -> int:
    """No keychain access, socket, selected folder, runtime or persistent database."""
    try:
        import sqlite3
        from importlib.metadata import version

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        version("social-media-ai-local-connector")
        Ed25519PublicKey.from_public_bytes(bytes(32))
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE health USING fts5(content)")
        if getattr(sys, "frozen", False):
            bundled_release()
    except (ImportError, OSError, ValueError, sqlite3.Error):
        print("Packaged dependency self-test failed.", file=sys.stderr)
        return 2
    desktop = "absent"
    try:
        import PySide6  # noqa: F401
    except ImportError:
        desktop = "absent"
    else:
        desktop = "present"
    print(f"Packaged dependency self-test passed; runtime not started; native desktop {desktop}.")
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
