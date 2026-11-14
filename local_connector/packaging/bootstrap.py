"""Frozen local-only entry point; never starts the application/cloud runtime."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
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
    args = parser.parse_args()
    if args.version:
        from importlib.metadata import version

        print(version("social-media-ai-local-connector"))
        return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
