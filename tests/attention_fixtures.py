"""Generate actual attention-service snapshots using inspected fake repositories.

No application/DB imports. Use after shell_page_fixtures.py, with --out <dir>.
"""

import argparse
import asyncio
import json
import runpy
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
fixture = runpy.run_path(str(Path(__file__).parent / "unit_web/test_attention_feed.py"))
Repository, Perms, feed = (fixture[key] for key in ("Repository", "Perms", "feed"))
event, task, connection = (fixture[key] for key in ("event", "task", "connection"))


async def snapshots():
    failed = task()
    failed.name = '<img src=x onerror="window.attentionXss=1">'
    repo = Repository(
        events=[event(1), event(2, entity=None, kind="report_ready")], tasks=[failed], connections=[connection()]
    )
    service = feed(repo)
    result = {"mixed": await service.snapshot(), "window": await service.snapshot(limit=1)}
    result["readonly"] = await feed(repo, Perms({("notification", "view")})).snapshot()
    repo.broken.add("connections")
    result["partial"] = await service.snapshot()
    repo.broken.update(["tasks", "messages"])
    result["unknown"] = await service.snapshot()
    repo.broken.clear()
    await service.read_one(1)  # changes the in-memory fake row only
    result["after-read"] = await service.snapshot()
    failed.last_status = "ok"
    repo.statuses.clear()
    result["cleared"] = await service.snapshot()
    await service.read_one(2)
    result["empty"] = await service.snapshot()
    result["foreign-context"] = await feed(repo, workspace=8).snapshot()
    return result


args.out.mkdir(parents=True, exist_ok=True)
(args.out / "attention-snapshots.json").write_text(json.dumps(asyncio.run(snapshots()), ensure_ascii=False, indent=2))
print("Generated mocked attention snapshots; no application, ASGI, DB or provider.")
