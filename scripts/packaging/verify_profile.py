"""Install a real wheel outside the checkout and check one explicit profile.

Only package/config and optional library imports execute in the small probe.
The established wheel smoke separately guards full ASGI imports without lifespan,
network, database connections, runtime entry points or provider construction.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "core": ROOT / "requirements.txt",
    **{name: ROOT / f"requirements/{name}.txt" for name in ("dev", "telegram", "qr", "legacy-celery")},
}
PROBE = r"""
import importlib, importlib.metadata, json, sys
from pathlib import Path
profile = sys.argv[1]
optional = {"telegram": {"telethon"}, "qr": {"segno"}, "legacy-celery": {"celery"}, "dev": {"pytest", "black", "isort", "aiosqlite"}}
expected = optional.get(profile, set())
for name in set().union(*optional.values()):
    try: importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError: assert name not in expected, name
    else: assert name in expected, f"Unrequested integration/tool: {name}"
# Telethon asks ctypes to discover OpenSSL via ldconfig. Resolve the two
# system-library names before the import tripwire; no client or provider starts.
if profile == "telegram":
    import ctypes.util
    discovered = {name: ctypes.util.find_library(name) for name in ("ssl", "crypto")}
    ctypes.util.find_library = lambda name: discovered.get(name)
def no_effects(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto", "subprocess.Popen", "os.system"}:
        raise AssertionError(f"Installed profile attempted external operation: {event}")
sys.addaudithook(no_effects)
for name in sorted(expected): importlib.import_module(name)
assert importlib.metadata.version("redis")
from app.version import __version__
from app.core.config import settings
assert __version__ == importlib.metadata.version("social-media-ai")
assert Path(importlib.import_module("app.core.config").__file__).is_relative_to(Path(sys.prefix))
assert settings.database.DB_SCHEMA == "public"
assert settings.MAX_WORKSPACE_MEMBERS == 2
assert not settings.PERSONAL_BILLING_ENABLED
assert not {"app.models", "app.core.database", "app.runtime", "app.worker"}.intersection(sys.modules)
print(json.dumps({"profile": profile, "installed_import_smoke": "PASS", "unrequested_optional_packages": "absent"}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with tempfile.TemporaryDirectory(prefix=f"package-{args.profile}-") as directory:
        temporary = Path(directory)
        venv = temporary / "venv"
        subprocess.run([args.python, "-m", "venv", str(venv)], check=True)
        python = venv / "bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "-r", str(PROFILES[args.profile])], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--no-deps", str(wheel)], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)
        # Never inherit owner credentials or a checkout .env/PYTHONPATH.
        env = {
            "PATH": os.defpath,
            "HOME": str(temporary / "home"),
            "PYTHON_DOTENV_DISABLED": "1",
            "SECRET_KEY": secrets.token_urlsafe(32),
            "POSTGRES_URL": "postgresql://synthetic.invalid/unused",
        }
        cwd = temporary / "cwd"
        cwd.mkdir()
        subprocess.run([str(python), "-I", "-B", "-c", PROBE, args.profile], cwd=cwd, env=env, check=True)
        if args.profile != "dev":
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tests/wheel_runtime_smoke.py"),
                    "--python",
                    str(python),
                    "--wheel",
                    str(wheel),
                ],
                check=True,
            )
        else:
            subprocess.run(
                [str(python), "-I", str(ROOT / "tests/config/test_p11_t15_settings_contract.py")],
                env=env,
                cwd=cwd,
                check=True,
            )


if __name__ == "__main__":
    main()
