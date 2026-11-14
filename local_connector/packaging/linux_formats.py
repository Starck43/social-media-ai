"""Produce unsigned Linux desktop packages from an already frozen bundle.

Runs only in the build environment. No post-install scripts, services, login
auto-start, pairing, system data directory or runtime activation is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

NAME = "ai-assistant-connector"
APPIMAGETOOL_SHA256 = "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
RUNTIME_SHA256 = "617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"
DESKTOP = """[Desktop Entry]
Type=Application
Name=AI Assistant Connector (development)
Comment=Local-only development connector; not activated
Exec=ai-assistant-connector
Icon=ai-assistant-connector
Terminal=true
Categories=Utility;
"""
ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#334155"/><text x="12" y="42" fill="white" font-size="26">AI</text></svg>\n'


def populate(root: Path, bundle: Path) -> None:
    shutil.copytree(bundle, root / "opt" / NAME, symlinks=True)
    binary = root / "usr" / "bin"
    binary.mkdir(parents=True)
    launcher = binary / NAME
    launcher.write_text(f'#!/bin/sh\nexec /opt/{NAME}/{NAME} "$@"\n')
    launcher.chmod(0o755)
    desktop = root / "usr" / "share" / "applications"
    desktop.mkdir(parents=True)
    (desktop / f"{NAME}.desktop").write_text(DESKTOP)
    icons = root / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icons.mkdir(parents=True)
    (icons / f"{NAME}.svg").write_text(ICON)


def build(bundle: Path, output: Path, version: str, tool: Path, runtime: Path) -> None:
    # Tool pin comes from the official appimagetool 1.9.1 release asset digest.
    if hashlib.sha256(tool.read_bytes()).hexdigest() != APPIMAGETOOL_SHA256:
        raise ValueError("untrusted appimagetool")
    if hashlib.sha256(runtime.read_bytes()).hexdigest() != RUNTIME_SHA256:
        raise ValueError("untrusted AppImage runtime")
    deb_root = output / "deb-root"
    populate(deb_root, bundle)
    control = deb_root / "DEBIAN"
    control.mkdir()
    (control / "control").write_text(
        f"Package: {NAME}\nVersion: {version}\nArchitecture: amd64\n"
        "Maintainer: AI Assistant contributors\nSection: utils\nPriority: optional\n"
        "Depends: libc6 (>= 2.39), zlib1g, libgcc-s1\n"
        "Description: Unsigned local-only connector development scaffold\n"
        " No cloud activation, service or auto-start.\n"
    )
    deb = output / f"{NAME}_{version}_amd64.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(deb_root), str(deb)], check=True)
    subprocess.run(["dpkg-deb", "--info", str(deb)], check=True)

    appdir = output / f"{NAME}.AppDir"
    shutil.copytree(bundle, appdir / "usr" / "lib" / NAME, symlinks=True)
    (appdir / f"{NAME}.desktop").write_text(DESKTOP)
    (appdir / f"{NAME}.svg").write_text(ICON)
    launcher = appdir / "AppRun"
    launcher.write_text(f'#!/bin/sh\nHERE=$(dirname "$(readlink -f "$0")")\nexec "$HERE/usr/lib/{NAME}/{NAME}" "$@"\n')
    launcher.chmod(0o755)
    appimage = output / f"{NAME}-{version}-x86_64.AppImage"
    subprocess.run(
        [
            str(tool),
            "--appimage-extract-and-run",
            "--no-appstream",
            "--runtime-file",
            str(runtime),
            str(appdir),
            str(appimage),
        ],
        check=True,
        env={**os.environ, "ARCH": "x86_64"},
    )
    subprocess.run([str(appimage), "--appimage-extract-and-run", "--version"], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--appimagetool", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    # Not untrusted user input: CI derives the version from reviewed pyproject.
    from updates import _version

    _version(args.version)
    build(
        args.bundle.resolve(), args.output.resolve(), args.version, args.appimagetool.resolve(), args.runtime.resolve()
    )


if __name__ == "__main__":
    main()
