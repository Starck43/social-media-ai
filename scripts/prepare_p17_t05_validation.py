"""Parent-owned isolated DB preparation, lock and cleanup; never shared writes."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCHEMA = "p17_t05_validation"
STATE = ROOT / ".agents/tmp/p17-db-inventory.json"

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["prepare", "cleanup", "run"])
parser.add_argument("--env-file", required=True)
parser.add_argument("--dev-checkout", type=Path, required=True)
parser.add_argument("command", nargs=argparse.REMAINDER)
args = parser.parse_args()
from dotenv import load_dotenv

load_dotenv(args.env_file)
url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("POSTGRES_URL", "")
p = urlsplit(url)
if p.hostname not in {"localhost", "127.0.0.1", "::1"} or p.path != "/social_manager":
    raise SystemExit("Only local social_manager is authorized")
os.environ.update(
    POSTGRES_URL=url,
    TEST_POSTGRES_URL=url,
    DB_SCHEMA=SCHEMA,
    DB_TEST_SCHEMA=SCHEMA,
    DEBUG="false",
    USER_SERVICE_PLANS_ENABLED="false",
    DB_POOL="null",
)
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.core.database import async_engine, engine
from scripts.ci.schema_inventory import diff, snapshot

config = Config(str(ROOT / "alembic.ini"))
config.set_main_option("script_location", str(ROOT / "migrations"))
config.attributes["skip_model_type_registration_on_stamp"] = True

dev_config = Config(str(args.dev_checkout / "alembic.ini"))
dev_config.set_main_option("script_location", str(args.dev_checkout / "migrations"))
dev_heads = ScriptDirectory.from_config(dev_config).get_heads()
task_graph = ScriptDirectory.from_config(config)
task_heads = task_graph.get_heads()
if len(dev_heads) != 1 or task_heads != ["p17t05_onec_audit_export"]:
    raise SystemExit("One fresh dev head and the expected task head are required")
if task_graph.get_revision(task_heads[0]).down_revision != dev_heads[0]:
    raise SystemExit("Task migration parent differs from fresh dev baseline")
print(
    "Dev SHA:", subprocess.check_output(["git", "-C", str(args.dev_checkout), "rev-parse", "HEAD"], text=True).strip()
)
print("Source heads: dev=", dev_heads, "task=", task_heads)


def migrate(target, *, down=False):
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        (command.downgrade if down else command.upgrade)(config, target)
    config.attributes.pop("connection", None)


with engine.connect() as lock:
    if not lock.execute(text("select pg_try_advisory_lock(1370093)")).scalar_one():
        raise SystemExit("Another runner owns DB lock")
    try:
        writers = (
            lock.execute(
                text(
                    "select pid from pg_stat_activity where datname=current_database() "
                    "and pid<>pg_backend_pid() and state='active' "
                    "and query ~* '\\m(insert|update|delete|alter|create|drop|truncate)\\M'"
                )
            )
            .scalars()
            .all()
        )
        if writers:
            raise SystemExit("Coordinate active DB writers")
        exists = lock.execute(text("select to_regnamespace(:s)"), {"s": SCHEMA}).scalar_one() is not None
        shared = lock.execute(text("select version_num from test_schema.alembic_version")).scalars().all()
        if len(shared) != 1 or shared[0] not in {
            r.revision for r in task_graph.iterate_revisions(dev_heads[0], "base")
        }:
            raise SystemExit("Unexplained shared baseline mismatch; no bootstrap is permitted")
        print("Observed shared revision:", shared[0], "(read-only; isolated schema runs the fresh dev baseline)")
        lock.commit()
        if args.action == "prepare":
            if exists or STATE.exists():
                raise SystemExit("Refusing existing task schema/inventory")
            before = {s: snapshot(s) for s in ["test_schema", SCHEMA]}
            STATE.write_text(json.dumps(before, sort_keys=True))
            with engine.begin() as conn:
                conn.execute(text(f'create schema "{SCHEMA}"'))
            try:
                migrate(dev_heads[0])

                async def seed():
                    from scripts.setup.roles import seed_roles_async
                    from scripts.setup_test_db import _seed_permissions, _seed_platforms, _seed_role_grants

                    await seed_roles_async()
                    await _seed_platforms()
                    await _seed_permissions()
                    await _seed_role_grants()
                    await async_engine.dispose()

                asyncio.run(seed())
                baseline = snapshot(SCHEMA)
                migrate("p17t05_onec_audit_export")
                print("Prepared isolated Alembic 0155 -> p17t05_onec_audit_export; canonical reference rows")
                STATE.write_text(json.dumps({"before": before, "baseline": baseline}, sort_keys=True))
            except BaseException:
                print("PREPARATION FAILED; cleanup required with preserved inventory")
                raise
        elif args.action == "run":
            if not exists or not STATE.exists():
                raise SystemExit("Preparation evidence required")
            cmd = args.command
            if cmd and cmd[0] == "--":
                cmd = cmd[1:]
            if not cmd:
                raise SystemExit("Explicit test command required")
            print(
                "Locked runner; head=" + subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                flush=True,
            )
            result = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy(), check=False)
            raise SystemExit(result.returncode)
        else:
            if not STATE.exists():
                raise SystemExit("Owned preparation inventory required")
            evidence = json.loads(STATE.read_text())
            before = evidence.get("before", evidence)
            if exists:
                with engine.begin() as conn:
                    conn.execute(text(f'drop schema "{SCHEMA}" cascade'))
            after = {s: snapshot(s) for s in ["test_schema", SCHEMA]}
            problems = {s: diff(before[s], after[s]) for s in before}
            if any(problems.values()):
                print("Cleanup inventory differences:", problems)
                raise SystemExit(1)
            print("Cleanup PASS: task schema/owned synthetic data absent; shared schema inventory unchanged")
    finally:
        lock.rollback()
        lock.execute(text("select pg_advisory_unlock(1370093)"))
        lock.commit()
engine.dispose()
