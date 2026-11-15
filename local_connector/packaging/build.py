"""Build an unsigned, standalone development bundle on the current OS."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "ai-assistant-connector"


def approved_backend() -> str | None:
    """The one OS key-store backend this platform may ever load."""

    from local_connector.packaging.key_store import approved_backends

    names = approved_backends()
    return names[0] if names else None


def optional_imports(*, with_desktop: bool) -> list[str]:
    """Hidden imports for the frozen bundle; a missing optional extra is skipped."""

    arguments: list[str] = []
    backend = approved_backend()
    if backend:
        arguments += ["--hidden-import", "keyring.core", "--hidden-import", backend]
    if with_desktop:
        try:
            import PySide6  # noqa: F401
        except ImportError:
            raise ValueError("the desktop extra is required for a native tray bundle") from None
        arguments += [
            "--hidden-import",
            "PySide6.QtCore",
            "--hidden-import",
            "PySide6.QtGui",
            "--hidden-import",
            "PySide6.QtWidgets",
        ]
    return arguments


def update_archive(bundle: Path, target: Path) -> None:
    """Write the update ZIP with the launcher at its root.

    The portable install ZIP keeps its top-level directory; an update archive is
    extracted into a version slot, so its launcher must be at the archive root
    and every sibling file must keep its relative path.
    """

    import zipfile

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, path.relative_to(bundle).as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--update-public-key", type=Path, help="Authenticated release Ed25519 public key, 32 raw bytes")
    parser.add_argument("--update-feed", help="Reviewed fixed HTTPS feed; only embedded at build time")
    parser.add_argument("--with-desktop", action="store_true", help="Bundle the optional native tray controls")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / "local_connector/pyproject.toml").read_text())["project"]["version"]
    work = output / "work"
    key = args.update_public_key.read_bytes() if args.update_public_key else b""
    if args.update_public_key and len(key) != 32:
        raise ValueError("update public key must contain 32 raw bytes")
    key_file = output / "update-key.bin"
    key_file.write_bytes(key)
    feed = None
    if args.update_feed:
        if not key:
            raise ValueError("update feed requires an authenticated public key")
        from local_connector.packaging.updater import _feed_base

        feed = _feed_base(args.update_feed)
    config_file = output / "release-config.json"
    config_file.write_text(json.dumps({"feed": feed}))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--console",
            "--name",
            NAME,
            "--paths",
            str(ROOT),
            "--copy-metadata",
            "social-media-ai-local-connector",
            "--add-data",
            f"{key_file}{os.pathsep}connector-release",
            "--add-data",
            f"{config_file}{os.pathsep}connector-release",
            "--hidden-import",
            "keyring.core",
            "--hidden-import",
            approved_backend() or "keyring.core",
            "--collect-submodules",
            "watchdog.observers",
            *optional_imports(with_desktop=args.with_desktop),
            "--distpath",
            str(output / "dist"),
            "--workpath",
            str(work),
            "--specpath",
            str(output),
            str(Path(__file__).with_name("bootstrap.py")),
        ],
        check=True,
        cwd=ROOT,
    )
    suffix = ".exe" if sys.platform == "win32" else ""
    subprocess.run([str(output / "dist" / NAME / (NAME + suffix)), "--version"], check=True)
    subprocess.run([str(output / "dist" / NAME / (NAME + suffix)), "--self-test"], check=True)
    archive = f"{NAME}-{version}-{sys.platform}-{platform.machine()}"
    shutil.copy2(ROOT / "docs/LOCAL_CONNECTOR.md", output / "dist" / NAME / "INSTALL.md")
    shutil.make_archive(str(output / archive), "zip", output / "dist", NAME)
    # An update archive is extracted into a version slot, so its launcher is at
    # the archive root; the portable ZIP above keeps its top-level directory.
    update_archive(output / "dist" / NAME, output / f"{archive}-update.zip")


if __name__ == "__main__":
    main()
