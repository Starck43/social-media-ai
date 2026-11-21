"""Run or resume the P22-T06 legacy task backfill.

Examples
--------
    python -m scripts.backfill_legacy_tasks --tenant-id 7 --batch-size 100
    python -m scripts.backfill_legacy_tasks --tenant-id 7 --run-id 12 --once --report

Each batch commits its cursor and reconciliation rows. A failed batch can be
retried with the same run ID. No downgrade or destructive cleanup is performed
by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session_maker
from app.models.planner import TaskBackfillRecord, TaskBackfillRun
from app.services.planner.legacy_migration import LegacyIdentityError, legacy_backfill_service


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", type=int, required=True)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--once", action="store_true", help="commit one batch and return")
    parser.add_argument("--report", action="store_true", help="print per-record reconciliation rows")
    parser.add_argument("--watermark", type=datetime.fromisoformat)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.batch_size < 1 or args.batch_size > 1000:
        raise LegacyIdentityError("invalid_backfill_limit")
    run_id = args.run_id
    result = None
    async with async_session_maker() as session:
        while True:
            async with session.begin():
                if run_id is None:
                    run = await legacy_backfill_service.start(
                        session,
                        tenant_id=args.tenant_id,
                        watermark=args.watermark,
                    )
                    run_id = run.id
                else:
                    run = await session.scalar(
                        select(TaskBackfillRun)
                        .where(TaskBackfillRun.tenant_id == args.tenant_id, TaskBackfillRun.id == run_id)
                        .with_for_update()
                    )
                    if run is None:
                        raise LegacyIdentityError("backfill_run_unavailable")
                result = await legacy_backfill_service.process(session, run, limit=args.batch_size)
            print(json.dumps(result, sort_keys=True, default=str))
            if args.once or result["status"] == "completed":
                break
    if args.report:
        async with async_session_maker() as session:
            records = list(
                await session.scalars(
                    select(TaskBackfillRecord)
                    .where(TaskBackfillRecord.tenant_id == args.tenant_id, TaskBackfillRecord.run_id == run_id)
                    .order_by(TaskBackfillRecord.legacy_namespace, TaskBackfillRecord.legacy_id)
                )
            )
            for record in records:
                print(
                    json.dumps(
                        {
                            "namespace": record.legacy_namespace,
                            "legacy_id": record.legacy_id,
                            "task_id": record.task_id,
                            "outcome": record.outcome,
                            "reason": record.reason,
                            "source_fingerprint": record.source_fingerprint,
                            "access_fingerprint": record.access_fingerprint,
                        },
                        sort_keys=True,
                    )
                )
    return 0


def main() -> int:
    args = _parser().parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
