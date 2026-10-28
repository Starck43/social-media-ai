"""Check a runtime-only installed wheel from outside the checkout, without pytest.

Build/install in separate virtual environments first; pass the installed Python
and wheel paths. Imports construct engines and the ASGI object, but never enter
lifespan, handle requests, connect to a database, or start runtime/providers.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import tomllib
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

# Runs under the installed environment's Python -I, not the checkout interpreter.
INSTALLED_CHECK = r"""
import asyncio
import hashlib
import importlib
import importlib.abc
import importlib.metadata
import json
import os
import platform
import sys
import threading
import traceback
from pathlib import Path

expected = json.load(sys.stdin)
assert sys.prefix != sys.base_prefix, "Use a separate runtime virtual environment"
for name in ("pytest", "pytest-asyncio", "pytest-cov", "aiosqlite", "black", "isort", "mypy", "flake8"):
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        continue
    raise AssertionError(f"Development dependency present: {name}")

external_attempts = []


def no_external_effects(event, args):
    if event in {
        "socket.connect",
        "socket.bind",
        "socket.getaddrinfo",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
        "os.fork",
        "os.posix_spawn",
    }:
        external_attempts.append((event, traceback.format_stack(limit=8)))
        raise RuntimeError(f"Forbidden smoke-test operation: {event}")


sys.addaudithook(no_external_effects)
# croniter probes platform.architecture(), which otherwise shells out to `file`.
# Supply interpreter metadata only; do not relax the subprocess tripwire.
platform.architecture = lambda *args, **kwargs: ("64bit" if sys.maxsize > 2**32 else "32bit", "")

allowed = {
    "app",
    "app.core",
    "app.core.config",
    "app.core.settings_views",
    "app.core.database",
    "app.services",
    "app.services.collection",
    "app.services.collection.intent",
    "app.services.social",
    "app.services.social.vk_access",
    "app.services.task_plans",
    "app.services.task_contracts",
    "app.services.task_preparation",
    "app.services.task_creation",
    "app.utils",
    "app.utils.crypto",
    "app.jobs",
    "app.jobs.progress",
    "app.jobs.progress_metrics",
    "app.types",
    "app.types.enums",
    "app.utils.db_enums",
    "cli",
    "cli.commands",
    "cli.commands.llm",
}


class SafeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"app", "cli"}:
            if fullname not in allowed and not fullname.startswith("app.types.enums."):
                raise RuntimeError(f"Unreviewed application import: {fullname}")
        return None


safe_imports = SafeImports()
sys.meta_path.insert(0, safe_imports)
dist = importlib.metadata.distribution("social-media-ai")
installed = {str(p): p for p in dist.files or ()}
for name, digest in expected.items():
    assert name in installed, f"Not installed: {name}"
    path = Path(dist.locate_file(installed[name])).resolve()
    assert path.is_relative_to(Path(sys.prefix).resolve()), f"Outside runtime venv: {path}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"Changed installed file: {name}"

# Verify native runtime dependencies and the actual installed database module.
# SQLAlchemy constructors must load their drivers, but may never open a connection.
import asyncpg
import greenlet
import psycopg
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

connection_attempts = []


def reject_database_connect(*args, **kwargs):
    connection_attempts.append(True)
    raise RuntimeError("Database connection forbidden in installed-wheel smoke")


event.listen(Dialect, "do_connect", reject_database_connect)
modules = (
    "app.core.config",
    "app.utils.crypto",
    "app.jobs.progress",
    "app.types.enums",
    "app.core.database",
    # This is a narrow import-time check for the extracted plan/contract core.
    # It does not classify all of app.agent or app.core as a particular layer.
    "app.services.task_plans",
    "app.services.task_contracts",
    "app.services.task_preparation",
    "app.services.task_creation",
    "cli.commands.llm",
)
for name in modules:
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), name

assert {
    "app.models",
    "app.agent",
    "app.channels",
    "app.api",
    "app.web",
    "app.admin",
}.isdisjoint(sys.modules), "Task-plan imports loaded an unreviewed model or adapter graph"

# Import the compatibility shell without exercising its lazy persistence paths.
# Existing adapters must keep the same pure functions/classes by identity.
from app.services import task_creation, task_preparation

for name in (
    "TaskPlanCreate", "TaskPlanCreateConflict", "resolve_task_plan_create",
    "project_task_plan_create", "prepare_task_plan_create",
):
    assert getattr(task_creation, name) is getattr(task_preparation, name), name

# Exercise the real installed core, not mocks: selected/all/discovery, recurring
# and one-off triggers, payload context and legacy-field conflict ordering.
raw = {
    "version": 1, "platforms": ["vk"], "source_scope": "selected", "source_ids": [4],
    "collection": {"queries": ["repair"]}, "window": {"type": "rolling", "days": 3},
    "trigger": {"type": "schedule", "cron": "0 9 * * *", "timezone": "UTC"},
}
for scope in ("selected", "all", "discovery"):
    ids = [4] if scope == "selected" else []
    prepared = task_preparation.prepare_task_plan_create(
        {"payer_receipt": {"id": 99}}, dict(raw, source_scope=scope, source_ids=ids),
        job_type="analyze", requested_source_ids=ids, enforce_source_match=True,
    )
    assert prepared.source_scope == scope and prepared.source_ids == tuple(ids)
    assert prepared.payload["payer_receipt"] == {"id": 99}
    assert prepared.cron_expr == "0 9 * * *"
    assert prepared.payload["task_plan"]["window"] == {"type": "rolling", "days": 3}
once = task_creation.prepare_task_plan_create({}, dict(raw, trigger={"type": "once"}), job_type="collect")
assert once.cron_expr == "@once" and once.payload["task_plan"]["trigger"] == {"type": "once"}
try:
    task_preparation.prepare_task_plan_create(
        {}, raw, job_type="collect", requested_cron="0 8 * * *", requested_source_ids=[],
        enforce_source_match=True, conflict_order=("source_ids", "cron_expr", "scenario_id"),
    )
except task_preparation.TaskPlanCreateConflict as error:
    assert error.field == "source_ids"
else:
    raise AssertionError("Task-create legacy conflict was not preserved")
from app.services.task_plans import TaskPlanError

fixed = task_preparation.prepare_task_plan_create(
    {}, dict(raw, window={"type": "fixed", "start": "2026-01-02", "end": "2026-01-04"}),
    job_type="collect",
)
assert fixed.payload["cli_dates"] == {"start_date": "2026-01-02", "end_date": "2026-01-04"}
for kwargs, field in (
    ({"requested_cron": "0 8 * * *"}, "cron_expr"),
    ({"requested_scenario_id": 7}, "scenario_id"),
    ({"requested_source_ids": [], "enforce_source_match": True}, "source_ids"),
):
    try:
        task_preparation.prepare_task_plan_create({}, raw, job_type="collect", **kwargs)
    except task_preparation.TaskPlanCreateConflict as error:
        assert error.field == field
    else:
        raise AssertionError(f"Missing legacy conflict: {field}")
try:
    task_preparation.prepare_task_plan_create({}, dict(raw, source_ids=[]), job_type="collect")
except TaskPlanError as error:
    assert error.code == "task_plan_selected_sources_required"
else:
    raise AssertionError("Empty selected source scope was accepted")
resolved = task_preparation.resolve_task_plan_create(raw, requested_cron="0 9 * * *")
try:
    task_preparation.project_task_plan_create({}, resolved, job_type="digest")
except TaskPlanError as error:
    assert error.code == "task_plan_requires_collect_or_analyze"
else:
    raise AssertionError("Invalid job type was accepted at projection")
assert {"app.models", "app.agent", "app.channels", "app.api", "app.web", "app.admin"}.isdisjoint(sys.modules)

from app.utils.crypto import decrypt_secret, encrypt_secret

assert decrypt_secret(encrypt_secret("wheel-smoke-only")) == "wheel-smoke-only"
database = sys.modules["app.core.database"]
assert database.async_engine.dialect.driver == "asyncpg"
assert database.engine.dialect.driver in {"psycopg", "psycopg2"}
assert not connection_attempts, "Database connection was attempted during imports"
assert {"app.main", "app.runtime", "app.worker", "app.models", "app.web"}.isdisjoint(sys.modules)

# The small allowlist above is retained as an independent safe-import check.
# Now exercise the real ASGI import graph, not stubs. Block execution boundaries:
# no event loop, task/thread startup, runtime entry point, or DB connection.
sys.meta_path.remove(safe_imports)


class NoRuntimeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.runtime", "app.worker"} or fullname.startswith(("app.runtime.", "app.worker.")):
            raise RuntimeError(f"Runtime entry point forbidden: {fullname}")
        return None


sys.meta_path.insert(0, NoRuntimeImports())
execution_attempts = []


def reject_execution(*args, **kwargs):
    execution_attempts.append(True)
    raise RuntimeError("Runtime/lifespan/task execution forbidden in installed-wheel smoke")


asyncio.run = reject_execution
asyncio.BaseEventLoop.run_until_complete = reject_execution
asyncio.BaseEventLoop.run_forever = reject_execution
asyncio.BaseEventLoop.create_task = reject_execution
threading.Thread.start = reject_execution
database.init_db = reject_execution


def reject_provider_construction(frame, event, arg):
    if (
        event == "call"
        and frame.f_globals.get("__name__")
        in {
            "app.services.ai.llm_client",
            "app.services.social.base",
            "app.services.social.tg_session",
        }
        and frame.f_code.co_name in {"__init__", "create", "build_client"}
    ):
        reject_execution()


sys.setprofile(reject_provider_construction)
application = importlib.import_module("app.main")
assert application.app.version == importlib.metadata.version("social-media-ai")
assert application.app.title == "AI Assistant"
cli_main = importlib.import_module("cli.main")
assert cli_main.app.info.help == "Privileged operator-only administration; not a customer or end-user CLI."
web = importlib.import_module("app.web.deps")
admin = importlib.import_module("app.admin.setup")
from app.core.config import Settings, settings
from app.core.logger import file_handler, log_dir
from pydantic import ValidationError

for module in (application, web, admin):
    assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), module.__name__
package = Path(application.__file__).resolve().parent
resources = (
    (application.templates.env, "analytics_dashboard.html", package / "templates"),
    (web.templates.env, "web/base.html", package / "web" / "templates"),
    (application.app.state.admin.templates.env, "sqladmin/login.html", package / "templates"),
)
for environment, name, directory in resources:
    source, filename, _ = environment.loader.get_source(environment, name)
    assert source and Path(filename).resolve() == directory / name, (name, filename)
static = next(route.app for route in application.app.routes if getattr(route, "path", None) == "/static")
assert Path(static.directory).resolve() == package / "static"
assert (Path(static.directory) / "logo.png").is_file()
expected_logs = Path(os.environ.get("LOG_DIR", str(Path.home() / ".social-media-ai" / "logs")))
assert settings.LOG_DIR == log_dir == expected_logs
assert Path(file_handler.baseFilename) == expected_logs / "app.log"
assert Path(file_handler.baseFilename).is_file()
try:
    Settings(_env_file=None, LOG_DIR="relative-logs")
except ValidationError as error:
    assert "LOG_DIR must be an absolute path" in str(error)
else:
    raise AssertionError("Relative LOG_DIR accepted")
assert list(Path.cwd().iterdir()) == [], "Application import wrote into empty CWD"
assert (
    not connection_attempts and not execution_attempts and not external_attempts
), (
    "Forbidden execution attempted: "
    f"DB={connection_attempts}, runtime={execution_attempts}, external={external_attempts}"
)
assert {"app.runtime", "app.worker"}.isdisjoint(sys.modules)

print(
    json.dumps(
        {
            "installed_files_verified": len(expected),
            "safe_imports": list(modules),
            "fernet_round_trip": "passed",
            "cryptography": importlib.metadata.version("cryptography"),
            "dev_dependencies": "absent",
            "external_effects": "guarded",
            "database_connection_attempts": len(connection_attempts),
            "database_driver": database.engine.dialect.driver,
            "application_import": "passed (no lifespan/requests)",
            "resource_lookups": len(resources) + 1,
            "runtime_execution_attempts": len(execution_attempts),
            "external_operation_attempts": len(external_attempts),
            "empty_cwd": "unchanged",
            "log_dir": str(log_dir),
            "greenlet": importlib.metadata.version("greenlet"),
            "psycopg": importlib.metadata.version("psycopg"),
        },
        indent=2,
    )
)
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
        assert any(
            Requirement(r).name == "cryptography" and Requirement(r).marker is None
            for r in metadata.get_all("Requires-Dist", [])
        ), "Encryption must be runtime, not extra"
        requirements = metadata.get_all("Requires-Dist", [])
        declaration = tomllib.loads((root / "pyproject.toml").read_text())["project"]

        def normalized(value):
            requirement = Requirement(value)
            requirement.name = canonicalize_name(requirement.name)
            return str(requirement)

        expected_requirements = {normalized(value) for value in declaration["dependencies"]}
        for extra, values in declaration["optional-dependencies"].items():
            expected_requirements.update(normalized(f"{value}; extra == '{extra}'") for value in values)
        assert {
            normalized(value) for value in requirements
        } == expected_requirements, "Wheel/source dependency metadata differs"
        assert any(
            Requirement(r).name.lower() == "sqlalchemy"
            and "asyncio" in Requirement(r).extras
            and Requirement(r).marker is None
            for r in requirements
        ), "SQLAlchemy asyncio support must be runtime"
        assert any(
            Requirement(r).name == "psycopg" and "binary" in Requirement(r).extras and Requirement(r).marker is None
            for r in requirements
        ), "Default PostgreSQL driver must be runtime"
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
    with tempfile.TemporaryDirectory(prefix="wheel-smoke-") as temporary:
        temporary = Path(temporary)
        for mode in ("default", "override"):
            cwd = temporary / f"cwd-{mode}"
            cwd.mkdir()
            env["HOME"] = str(temporary / f"home-{mode}")
            if mode == "override":
                env["LOG_DIR"] = str(temporary / "configured-logs")
            result = subprocess.run(
                [str(args.python.absolute()), "-I", "-B", "-c", INSTALLED_CHECK],
                input=json.dumps(expected),
                text=True,
                cwd=cwd,
                env=env,
                check=False,
                timeout=90,
            )
            if result.returncode:
                raise SystemExit(result.returncode)
    python_count = sum(n.endswith(".py") for n in expected)
    print(f"PASS: {python_count} Python files, {len(expected) - python_count} assets; wheel and installed bytes match")


if __name__ == "__main__":
    main()
