"""Internal isolated metadata probe; never invoke application/CLI callbacks."""

from __future__ import annotations

import asyncio
import importlib.abc
import json
import platform
import sys
import threading
from pathlib import Path


def install_guards():
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append("execution")
        raise RuntimeError("Reference generation forbids DB/runtime/provider execution")

    def audit(event, args):
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
            attempts.append(event)
            raise RuntimeError(f"Reference generation forbids external operation: {event}")

    sys.addaudithook(audit)
    # croniter's interpreter-width probe otherwise invokes the system `file` tool.
    platform.architecture = lambda *args, **kwargs: ("64bit" if sys.maxsize > 2**32 else "32bit", "")

    class NoRuntimeImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in {"app.runtime", "app.worker"} or fullname.startswith(("app.runtime.", "app.worker.")):
                forbidden()
            return None

    sys.meta_path.insert(0, NoRuntimeImports())
    from sqlalchemy import event
    from sqlalchemy.engine.interfaces import Dialect

    event.listen(Dialect, "do_connect", forbidden)
    asyncio.run = forbidden
    asyncio.BaseEventLoop.run_until_complete = forbidden
    asyncio.BaseEventLoop.run_forever = forbidden
    asyncio.BaseEventLoop.create_task = forbidden
    threading.Thread.start = forbidden

    def providers(frame, event, arg):
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
            forbidden()

    sys.setprofile(providers)
    return attempts


def guard_check():
    import socket
    import subprocess

    from sqlalchemy import create_engine

    attempts = install_guards()
    coroutine = asyncio.sleep(0)
    operations = {
        "network": lambda: socket.getaddrinfo("synthetic.invalid", 443),
        "subprocess": lambda: subprocess.run(["forbidden-reference-probe"]),
        "thread": lambda: threading.Thread(target=lambda: None).start(),
        "event_loop": lambda: asyncio.run(coroutine),
        "database": lambda: create_engine("postgresql+psycopg://unused@synthetic.invalid/unused").connect(),
        "runtime_import": lambda: __import__("app.runtime"),
        "provider": lambda: exec(
            "def __init__(): pass\n__init__()",
            {"__name__": "app.services.ai.llm_client"},
        ),
    }
    try:
        for name, operation in operations.items():
            before = len(attempts)
            try:
                operation()
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"Guard did not stop {name}")
            assert len(attempts) == before + 1, name
    finally:
        coroutine.close()
    print(f"PASS: {len(operations)} metadata execution tripwires")


def main():
    if sys.argv[1:] == ["--guard-check"]:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        guard_check()
        return
    root, output = (Path(value).resolve() for value in sys.argv[1:])
    sys.path.insert(0, str(root))
    attempts = install_guards()
    from typer.main import get_command

    from app.main import app
    from cli.main import app as cli

    def value(item):
        # Never evaluate default factories or serialize object reprs/addresses.
        if item is None or isinstance(item, (str, int, float, bool)):
            return json.dumps(item, ensure_ascii=False)
        if isinstance(item, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in item):
            return json.dumps(item, ensure_ascii=False)
        return "<dynamic>"

    def walk(command, path):
        params = []
        for param in command.params:
            params.append(
                {
                    "name": ", ".join([*param.opts, *getattr(param, "secondary_opts", [])]),
                    "kind": param.param_type_name,
                    "type": param.type.name,
                    "required": "yes" if param.required else "no",
                    "default": value(param.default),
                }
            )
        rows = [{"path": path, "help": command.help or "", "params": params}]
        for name, child in sorted(getattr(command, "commands", {}).items()):
            rows.extend(walk(child, path + " " + name))
        return rows

    result = {"openapi": app.openapi(), "cli": walk(get_command(cli), "python -m cli.main")}
    assert not attempts, "A forbidden operation was attempted even if handled by application code"
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
