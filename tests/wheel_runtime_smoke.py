"""Check a runtime-only installed wheel from outside the checkout, without pytest.

Build/install in separate virtual environments first; pass the installed Python
and wheel paths. Only explicitly safe modules are imported, never entry points.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

# Runs under the installed environment's Python -I, not the checkout interpreter.
INSTALLED_CHECK = r"""
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import sys
from pathlib import Path

expected = json.load(sys.stdin)
assert sys.prefix != sys.base_prefix, "Use a separate runtime virtual environment"
for name in ("pytest", "pytest-asyncio", "pytest-cov", "aiosqlite", "black", "isort", "mypy", "flake8"):
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        continue
    raise AssertionError(f"Development dependency present: {name}")

def no_external_effects(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
        raise RuntimeError(f"Forbidden smoke-test operation: {event}")

sys.addaudithook(no_external_effects)
allowed = {
    "app", "app.core", "app.core.config", "app.utils", "app.utils.crypto",
    "app.jobs", "app.jobs.progress", "app.types", "app.types.enums", "app.utils.db_enums",
    "cli", "cli.commands", "cli.commands.llm",
}

class SafeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"app", "cli"}:
            if fullname not in allowed and not fullname.startswith("app.types.enums."):
                raise RuntimeError(f"Unreviewed application import: {fullname}")
        return None

sys.meta_path.insert(0, SafeImports())
dist = importlib.metadata.distribution("social-media-ai")
installed = {str(p): p for p in dist.files or ()}
for name, digest in expected.items():
    assert name in installed, f"Not installed: {name}"
    path = Path(dist.locate_file(installed[name])).resolve()
    assert path.is_relative_to(Path(sys.prefix).resolve()), f"Outside runtime venv: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"Changed installed file: {name}"

modules = ("app.core.config", "app.utils.crypto", "app.jobs.progress", "app.types.enums", "cli.commands.llm")
for name in modules:
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), name

from app.utils.crypto import decrypt_secret, encrypt_secret
assert decrypt_secret(encrypt_secret("wheel-smoke-only")) == "wheel-smoke-only"
assert {"app.main", "app.runtime", "app.worker", "app.models", "app.core.database", "app.web"}.isdisjoint(sys.modules)
print(json.dumps({"installed_files_verified": len(expected), "safe_imports": list(modules),
                  "fernet_round_trip": "passed", "cryptography": importlib.metadata.version("cryptography"),
                  "dev_dependencies": "absent", "external_effects": "guarded"}, indent=2))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="Python in a clean runtime-only venv")
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # Inventory every Python module, including implicit namespace packages, and
    # every asset at the three runtime resource roots; compare bytes, not samples.
    sources = set()
    for package in ("app", "cli"):
        sources.update((root / package).rglob("*.py"))
    for directory in ("app/static", "app/templates", "app/web/templates"):
        sources.update(p for p in (root / directory).rglob("*") if p.is_file())
    sources = {p for p in sources if "__pycache__" not in p.parts and p.suffix != ".pyc"}
    expected = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
    with ZipFile(args.wheel.resolve()) as wheel:
        names = wheel.namelist()
        missing = sorted(set(expected) - set(names))
        assert not missing, f"Missing runtime files in wheel ({len(missing)}): {missing[:12]}"
        for name, digest in expected.items():
            assert hashlib.sha256(wheel.read(name)).hexdigest() == digest, f"Stale wheel file: {name}"
        assert not any(n.startswith(("tests/", "docs/", ".agents/", "docker/", ".env")) for n in names)
        (metadata_name,) = (n for n in names if n.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(wheel.read(metadata_name).decode())
        assert "cryptography>=42.0.0" in metadata.get_all("Requires-Dist", []), "Encryption must be runtime, not extra"
        (entry_points_name,) = (n for n in names if n.endswith(".dist-info/entry_points.txt"))
        assert "cli = cli.main:app" in wheel.read(entry_points_name).decode()
    # Do not inherit owner credentials, DB URLs, PYTHONPATH or dotenv files.
    env = {
        "PATH": os.defpath,
        "PYTHON_DOTENV_DISABLED": "1",
        "SECRET_KEY": "wheel-smoke-synthetic-not-a-secret",
        "POSTGRES_URL": "postgresql://wheel-smoke.invalid/no_database",
        "CREDENTIALS_KEY": base64.urlsafe_b64encode(b"0" * 32).decode(),
    }
    with tempfile.TemporaryDirectory(prefix="wheel-smoke-") as cwd:
        result = subprocess.run(
            [str(args.python.absolute()), "-I", "-c", INSTALLED_CHECK],
            input=json.dumps(expected),
            text=True,
            cwd=cwd,
            env=env,
            check=False,
        )
        if result.returncode:
            raise SystemExit(result.returncode)
    python_count = sum(n.endswith(".py") for n in expected)
    print(f"PASS: {python_count} Python files, {len(expected) - python_count} assets; wheel and installed bytes match")


if __name__ == "__main__":
    main()
