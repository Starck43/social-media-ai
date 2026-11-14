"""Build an unsigned, standalone development bundle on the current OS."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "ai-assistant-connector"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--update-public-key", type=Path, help="Authenticated release Ed25519 public key, 32 raw bytes")
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
            "--collect-submodules",
            "watchdog.observers",
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
    archive = f"{NAME}-{version}-{sys.platform}-{platform.machine()}"
    shutil.copy2(ROOT / "docs/LOCAL_CONNECTOR.md", output / "dist" / NAME / "INSTALL.md")
    shutil.make_archive(str(output / archive), "zip", output / "dist", NAME)


if __name__ == "__main__":
    main()
