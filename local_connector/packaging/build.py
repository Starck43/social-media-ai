"""Build an unsigned, standalone development bundle on the current OS."""

from __future__ import annotations

import argparse
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
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / "local_connector/pyproject.toml").read_text())["project"]["version"]
    work = output / "work"
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
